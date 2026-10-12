"""Public Telegram preview polling with one shared courtesy limiter and edit overlap."""

import asyncio
import contextlib
import re
import time
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from email.utils import parsedate_to_datetime

import httpx
from selectolax.lexbor import LexborHTMLParser

from airalert.news.channels import VERIFIED
from airalert.news.policy import REQUEST_SPACING
from airalert.news.store import Message, Store, iso


class SourceUnavailable(RuntimeError):
    pass


class Limiter:
    def __init__(
        self,
        monotonic: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._lock = asyncio.Lock()
        self._last: float | None = None
        self.monotonic = monotonic
        self.sleep = sleep

    async def wait(self) -> None:
        async with self._lock:
            if self._last is not None:
                delay = max(0, REQUEST_SPACING - (self.monotonic() - self._last))
                await self.sleep(delay)
            self._last = self.monotonic()


GLOBAL_LIMITER = Limiter()


def parse_page(
    body: str, handle: str, observed_at: datetime, allow_empty: bool = False
) -> list[Message]:
    html = LexborHTMLParser(body)
    if html.css_first(".tgme_channel_info_header_title") is None:
        raise SourceUnavailable("Channel preview has no expected metadata")
    if not allow_empty and not html.css(".tgme_widget_message[data-post]"):
        raise SourceUnavailable("Latest channel preview has no expected message elements")
    messages = []
    for node in html.css(".tgme_widget_message[data-post]"):
        post = node.attributes.get("data-post", "")
        match = re.fullmatch(r"([A-Za-z0-9_]+)/(\d+)", post)
        footer = node.css_first("time[datetime]")
        if match is None or match[1].lower() != handle.lower() or footer is None:
            raise SourceUnavailable("Channel preview message identity or footer changed")
        published = datetime.fromisoformat(footer.attributes["datetime"])
        if published.tzinfo is None:
            raise SourceUnavailable("Channel preview timestamp lacks a timezone")
        text_node = node.css_first(".tgme_widget_message_text")
        if text_node is not None:
            for br in text_node.css("br"):
                br.replace_with("\n")
        text = text_node.text(strip=False).strip() if text_node is not None else ""
        meta = node.css_first(".tgme_widget_message_meta")
        messages.append(
            Message(
                handle,
                int(match[2]),
                published.astimezone(UTC),
                observed_at,
                text,
                meta is not None and "edited" in meta.text().lower(),
            )
        )
    # Empty forward pages have valid channel metadata. Media-only posts still advance the cursor.
    return sorted(messages, key=lambda m: m.message_id)


class Scraper:
    def __init__(
        self,
        store: Store,
        transport: httpx.AsyncBaseTransport | None = None,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        limiter: Limiter = GLOBAL_LIMITER,
    ) -> None:
        self.store = store
        self.clock = clock
        self.limiter = limiter
        self.client = httpx.AsyncClient(
            transport=transport,
            timeout=25,
            follow_redirects=False,
            headers={"User-Agent": "AirAlertForecaster/2 optional-news public-preview poller"},
        )

    async def close(self) -> None:
        await self.client.aclose()

    async def page(self, handle: str, after: int | None = None) -> list[Message]:
        if handle not in {h for h, _, _ in VERIFIED}:
            raise ValueError("Unverified channel")
        await self.limiter.wait()
        response = await self.client.get(
            f"https://t.me/s/{handle}", params={"after": after} if after is not None else None
        )
        response.raise_for_status()
        if len(response.content) > 4_194_304:
            raise SourceUnavailable("Channel preview exceeded the response limit")
        return parse_page(response.text, handle, self.clock(), allow_empty=after is not None)

    async def poll(self, handle: str, bootstrap_only: bool = False) -> list[Message]:
        state = self.store.channel(handle)
        now = self.clock()
        if state["paused"] or (
            state["retry_at"] and datetime.fromisoformat(str(state["retry_at"])) > now
        ):
            return []
        new = []
        try:
            cursor = int(state["cursor"]) if state["cursor"] is not None else None
            lagging = False
            for _page_number in range(10):
                messages = await self.page(handle, cursor)
                newest = max((m.message_id for m in messages), default=cursor)
                new.extend(self.store.ingest(handle, messages, newest))
                if cursor is None or bootstrap_only or not messages or newest == cursor:
                    break
                cursor = newest
            else:
                lagging = True
            self.store.channel_update(handle, lagging=lagging)
            overlap = state["last_overlap"]
            if state["cursor"] is not None and (
                overlap is None
                or now - datetime.fromisoformat(str(overlap)) >= timedelta(minutes=10)
            ):
                messages = await self.page(handle)
                # Latest-page overlap must not advance a lagging forward cursor.
                new.extend(self.store.ingest(handle, messages, None))
                self.store.channel_update(handle, last_overlap=iso(self.clock()))
            if state["cursor"] is None:
                self.store.channel_update(handle, last_overlap=iso(self.clock()))
            self.store.channel_update(
                handle, failures=0, retry_at=None, last_poll=iso(self.clock())
            )
        except (httpx.HTTPError, SourceUnavailable, ValueError):
            failures = int(state["failures"]) + 1
            delay = (120, 300, 900, 1800)[min(failures - 1, 3)]
            paused = False
            import sys

            error = sys.exception()
            if isinstance(error, httpx.HTTPStatusError):
                paused = error.response.status_code == 403
                retry = error.response.headers.get("Retry-After")
                if retry:
                    try:
                        delay = max(delay, int(retry))
                    except ValueError:
                        with contextlib.suppress(ValueError, TypeError):
                            delay = max(
                                delay, int((parsedate_to_datetime(retry) - now).total_seconds())
                            )
            self.store.channel_update(
                handle,
                failures=failures,
                paused=paused,
                retry_at=iso(now + timedelta(seconds=delay)),
            )
            raise SourceUnavailable("Channel unavailable; source backoff is active") from None
        return new
