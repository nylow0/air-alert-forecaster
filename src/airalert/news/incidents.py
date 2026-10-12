"""Conservative deterministic incident matching, independent of model grouping."""

import hashlib
import json
from datetime import datetime, timedelta

from airalert.news.geography import normalize
from airalert.news.store import Incident, Message, Store


def source_ids(incident: Incident) -> set[tuple[str, int]]:
    return {(s.handle, s.message_id) for s in incident.event.sources}


def compatible(a: Incident, b: Incident) -> bool:
    if source_ids(a) & source_ids(b):
        return a.event.type == b.event.type
    return (
        a.event.type == b.event.type
        and a.event.raion_ids == b.event.raion_ids
        and normalize(a.event.origin or "") == normalize(b.event.origin or "")
        and normalize(a.event.heading or "") == normalize(b.event.heading or "")
        and abs(a.event.published_at - b.event.published_at) <= timedelta(minutes=10)
    )


def reconcile(
    store: Store, incoming: list[Incident], messages: list[Message], finished: datetime
) -> None:
    """Edits invalidate earlier lineage; clear posts remove matching prior uplifts."""
    current = store.incidents()
    updated = {(m.handle, m.message_id): m.version_id for m in messages}
    for old in current:
        replaced = any(
            key in updated and updated[key] not in old.version_ids for key in source_ids(old)
        )
        if replaced and old.event.status != "cleared":
            cleared = old.event.model_copy(update={"status": "cleared", "observed_at": finished})
            store.append_incident(Incident(cleared, old.version_ids, finished))
    current = store.incidents()
    for new in sorted(incoming, key=lambda i: (i.event.published_at, i.version_ids)):
        if new.event.type == "all_clear" or new.event.status == "cleared":
            for old in current:
                # Unknown identity clears conservatively only matching explicit target/type scope.
                if (
                    old.event.status != "cleared"
                    and set(old.event.raion_ids) & set(new.event.raion_ids)
                    and old.event.published_at <= new.event.published_at
                    and (new.event.type in {"all_clear", old.event.type})
                ):
                    cleared = old.event.model_copy(
                        update={
                            "status": "cleared",
                            "observed_at": new.event.observed_at,
                            "sources": new.event.sources,
                            "published_at": new.event.published_at,
                        }
                    )
                    store.append_incident(Incident(cleared, new.version_ids, finished))
            current = store.incidents()
            continue
        match = next(
            (
                old
                for old in reversed(current)
                if compatible(old, new) and new.event.published_at <= old.event.expires_at
            ),
            None,
        )
        if match is None:
            identity = json.dumps(
                [
                    new.event.type,
                    new.event.raion_ids,
                    new.event.origin,
                    new.event.heading,
                    new.version_ids,
                ],
                ensure_ascii=False,
            )
            event_id = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:24]
        else:
            event_id = match.event.id
            if new.version_ids == match.version_ids:
                # One multi-route post is one observation, with the union of verified targets.
                new = Incident(
                    new.event.model_copy(
                        update={
                            "raion_ids": sorted(
                                set(new.event.raion_ids) | set(match.event.raion_ids)
                            ),
                            "oblast_ids": sorted(
                                set(new.event.oblast_ids) | set(match.event.oblast_ids)
                            ),
                        }
                    ),
                    new.version_ids,
                    finished,
                )
            if new.event.published_at < match.event.published_at:
                continue
            # Equal-time syndicated copies cannot refresh expiry or amplify confidence.
            if new.event.published_at == match.event.published_at:
                new = Incident(
                    new.event.model_copy(
                        update={
                            "expires_at": match.event.expires_at,
                            "confidence": min(match.event.confidence, new.event.confidence),
                        }
                    ),
                    new.version_ids,
                    finished,
                )
        store.append_incident(
            Incident(new.event.model_copy(update={"id": event_id}), new.version_ids, finished)
        )
        current = store.incidents()


def fingerprint(incidents: list[Incident]) -> str:
    return hashlib.sha256(
        json.dumps(sorted((i.event.id, i.revision) for i in incidents)).encode()
    ).hexdigest()
