"""R4 read-only network/CLI probes. Print evidence; never write credentials or files.

Run via uv run --with selectolax python scripts/r4_probe.py <subcommand>.
The parent agent saves chosen stdout evidence with its native file-edit tool.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import queue
import re
import shutil
import statistics
import subprocess
import sys
import threading
import time
from contextlib import suppress
from datetime import UTC, datetime
from pathlib import Path

import httpx
from selectolax.lexbor import LexborHTMLParser

CANDIDATES = (
    "truexanewsua", "insiderUKR", "uniannet", "UkraineNow", "u_now", "lachentyt",
    "voynareal", "TSNua", "RBC_ua_news", "suspilnenews", "operativnoZSU",
    "kpszsu", "war_monitor", "monitorwarr", "vanek_nikolaev", "kievreal1",
    "kievinfo_kyiv", "kyivoperativ", "kharkivlife", "truexakharkiv", "hueviykharkov",
    "kharkiv_1654", "odessa_infonews", "odessahello", "dnepr_operativ",
    "dnepr_info", "dnipro_alerts", "lvivych", "lviv24_7", "lviv1256", "truexalviv",
)


def redact(text: str) -> str:
    """Public channel posts can themselves contain leaked keys; never emit those strings."""
    text = re.sub(r"\b\d{6,12}:[A-Za-z0-9_-]{25,}\b", "[REDACTED_PUBLIC_TOKEN]", text)
    text = re.sub(r"\bsk-[A-Za-z0-9_-]{15,}\b", "[REDACTED_KEY]", text)
    return re.sub(r"\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\b",
                  "[REDACTED_TOKEN]", text)


def utc_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


class Preview:
    """Use one connection pool and at most one request start per 1.05 seconds."""

    def __init__(self) -> None:
        self.client = httpx.Client(timeout=25, follow_redirects=True)
        self.last_start = 0.0

    def get(self, handle: str, query: str = "") -> dict[str, object]:
        time.sleep(max(0, 1.05 - (time.monotonic() - self.last_start)))
        self.last_start = time.monotonic()
        start = time.perf_counter()
        response = self.client.get(f"https://t.me/s/{handle}{query}")
        latency = time.perf_counter() - start
        if response.status_code in {403, 429}:
            raise RuntimeError(f"Stop requested by Telegram: HTTP {response.status_code}")
        response.raise_for_status()
        html = LexborHTMLParser(response.text)
        title = html.css_first(".tgme_channel_info_header_title")
        if title is None:
            title = html.css_first(".tgme_page_title")
        counts = [n.text(separator=" ", strip=True) for n in html.css(
            ".tgme_channel_info_counter, .tgme_page_extra"
        )]
        raw_subscribers = next((c for c in counts if "subscriber" in c.lower()), None)
        subscribers: int | None = None
        if raw_subscribers:
            match = re.search(r"([\d\s\u00a0\u202f.,]+)([KkMm]?)\s*subscriber", raw_subscribers)
            if match:
                number = re.sub(r"[\s\u00a0\u202f,]", "", match.group(1))
                scale = {"k": 1000, "m": 1000000}.get(match.group(2).lower(), 1)
                subscribers = round(float(number) * scale)
        messages: list[dict[str, object]] = []
        for node in html.css(".tgme_widget_message"):
            timestamp = node.css_first(".tgme_widget_message_date time")
            post = node.attributes.get("data-post", "")
            if timestamp is None or not post:
                continue
            raw_date = timestamp.attributes.get("datetime", "")
            published = datetime.fromisoformat(raw_date).astimezone(UTC)
            body = node.css_first(".tgme_widget_message_text")
            if body is not None:
                for br in body.css("br"):
                    br.replace_with("\n")
            text = body.text(strip=False).strip() if body is not None else ""
            meta = node.css_first(".tgme_widget_message_meta")
            messages.append({
                "id": int(post.rsplit("/", 1)[1]), "channel_handle": post.rsplit("/", 1)[0],
                "published_at": published.isoformat().replace("+00:00", "Z"),
                "edited": meta is not None and "edited" in meta.text().lower(),
                "text": redact(text),
            })
        messages.sort(key=lambda m: int(str(m["id"])))
        span_h = None
        rate = None
        if len(messages) > 1:
            span = (datetime.fromisoformat(str(messages[-1]["published_at"]))
                    - datetime.fromisoformat(str(messages[0]["published_at"])))
            span_h = span.total_seconds() / 3600
            if span_h > 0:
                rate = (len(messages)-1) * 24 / span_h
        return {
            "handle_requested": handle,
            "handle": messages[-1]["channel_handle"] if messages else handle,
            "url": str(response.url), "status": response.status_code,
            "checked_at": utc_now(), "title": title.text(strip=True) if title else "",
            "subscribers": subscribers, "subscribers_display": raw_subscribers,
            "sample_message_count": len(messages),
            "last_post_time": messages[-1]["published_at"] if messages else None,
            "sample_span_hours": round(span_h, 4) if span_h is not None else None,
            "posts_per_day_sample_estimate": round(rate, 2) if rate is not None else None,
            "latency_seconds": round(latency, 4),
            "decoded_body_bytes": len(response.content),
            "downloaded_body_bytes": response.num_bytes_downloaded,
            "content_encoding": response.headers.get("content-encoding"),
            "body_sha256": hashlib.sha256(response.content).hexdigest(),
            "all_message_ids": [m["id"] for m in messages],
            "first_post_time": messages[0]["published_at"] if messages else None,
            "http_request_count": 1 + len(response.history), "messages": messages,
        }


def telegram(
    handles: list[str], cursors: bool, max_messages: int, max_text_chars: int
) -> dict[str, object]:
    preview = Preview()
    start = time.perf_counter()
    pages = [preview.get(handle) for handle in handles]
    elapsed = time.perf_counter() - start
    paging: list[dict[str, object]] = []
    if cursors:
        page = next(p for p in pages if p["handle_requested"] == "war_monitor")
        messages = page["messages"]
        if isinstance(messages, list) and messages:
            oldest = messages[0]["id"]
            newest = messages[-1]["id"]
            for query in (f"?before={oldest}", f"?after={oldest}", f"?after={newest}",
                          f"?after={int(str(oldest))-40}"):
                paging.append(preview.get("war_monitor", query))
    preview.client.close()
    timings = [float(str(p["latency_seconds"])) for p in pages]
    signal_pattern = re.compile(
        r"Ту-95|Ту-160|МіГ|МиГ|БпЛА|БПЛА|шахед|мопед|ракет|баліст|баллист|зліт|взлет|взлёт",
        re.IGNORECASE,
    )
    for page in pages + paging:
        messages = page["messages"]
        assert isinstance(messages, list)
        matches = [m for m in messages if signal_pattern.search(str(m["text"]))]
        chosen = (matches[-max_messages:] if matches else messages[-max_messages:]) \
            if max_messages else []
        page["signal_keyword_messages_on_page"] = len(matches)
        page["messages"] = [{**m, "text": str(m["text"])[:max_text_chars]} for m in chosen]
        page["sample_text_truncated_at_characters"] = max_text_chars
    return {
        "checked_at": utc_now(), "rate_limit_seconds_between_request_starts": 1.05,
        "pages": pages, "cursor_pages": paging,
        "poll": {
            "channels": len(pages),
            "requests": sum(int(str(p["http_request_count"])) for p in pages),
            "decoded_body_bytes": sum(int(str(p["decoded_body_bytes"])) for p in pages),
            "downloaded_body_bytes": sum(int(str(p["downloaded_body_bytes"])) for p in pages),
            "wall_seconds_including_polite_waits": round(elapsed, 4),
            "http_latency_median_seconds": round(statistics.median(timings), 4),
            "http_latency_max_seconds": max(timings),
        },
    }


def profiles(handles: list[str]) -> dict[str, object]:
    """The landing page displays exact subscriber integers; the preview rounds K/M."""
    rows = []
    last_start = 0.0
    with httpx.Client(timeout=25, follow_redirects=True) as client:
        for handle in handles:
            time.sleep(max(0, 1.05 - (time.monotonic() - last_start)))
            last_start = time.monotonic()
            start = time.perf_counter()
            response = client.get(f"https://t.me/{handle}")
            response.raise_for_status()
            html = LexborHTMLParser(response.text)
            title = html.css_first(".tgme_page_title")
            extra = html.css_first(".tgme_page_extra")
            display = extra.text(strip=True) if extra else ""
            match = re.search(r"([\d\s]+) subscribers", display)
            count = int(re.sub(r"\s", "", match.group(1))) if match else None
            rows.append({"handle": handle, "url": str(response.url),
                         "checked_at": utc_now(), "status": response.status_code,
                         "title": title.text(strip=True) if title else "",
                         "subscribers_display": display, "subscribers": count,
                         "latency_seconds": round(time.perf_counter() - start, 4),
                         "downloaded_body_bytes": response.num_bytes_downloaded})
    return {"checked_at": utc_now(), "profiles": rows}


def codex_binary() -> str:
    """Resolve the executable without reading any user credential/config file."""
    found = shutil.which("codex")
    if found is None:
        raise RuntimeError("Codex is not installed")
    return found


def safe_env() -> dict[str, str]:
    """Prevent injected API keys, remote providers and app orchestration reaching the child."""
    return {k: v for k, v in os.environ.items() if not k.startswith((
        "OPENAI_", "CODEX_API", "CODEX_ACCESS", "T3_", "ACP_"
    ))}


def model_list() -> dict[str, object]:
    """Initialize a private stdio app-server and request model/list only; no account tokens."""
    proc = subprocess.Popen(
        [codex_binary(), "app-server", "--stdio", "-c", "mcp_servers={}"],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
        text=True, encoding="utf-8", env=safe_env(), cwd=os.environ["TEMP"],
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    assert proc.stdin is not None and proc.stdout is not None
    lines: queue.Queue[str] = queue.Queue()

    def reader() -> None:
        assert proc.stdout is not None
        for line in proc.stdout:
            lines.put(line)

    threading.Thread(target=reader, daemon=True).start()
    start = time.perf_counter()
    try:
        requests = [
            {"id": 1, "method": "initialize", "params": {
                "clientInfo": {"name": "r4_probe", "title": "R4 research", "version": "0.1"},
                "capabilities": {"experimentalApi": True},
            }},
            {"method": "initialized", "params": {}},
            {"id": 2, "method": "model/list", "params": {"limit": 100, "includeHidden": True}},
        ]
        result: dict[str, object] = {}
        for request in requests:
            proc.stdin.write(json.dumps(request) + "\n")
            proc.stdin.flush()
            if "id" not in request:
                continue
            while time.perf_counter() - start < 40:
                response = json.loads(lines.get(timeout=40 - (time.perf_counter() - start)))
                if response.get("id") == request["id"]:
                    if request["id"] == 2:
                        result = response
                    break
        return {"checked_at": utc_now(), "seconds": round(time.perf_counter()-start, 3),
                "response": result}
    finally:
        proc.terminate()
        proc.wait(timeout=10)


def completion(model: str, schema: Path, timeout: int) -> dict[str, object]:
    """Use stdin, an unrelated existing temp cwd, read-only sandbox, and disabled integrations."""
    prompt = sys.stdin.buffer.read().decode("utf-8")
    args = [
        codex_binary(), "exec", "--ignore-user-config", "--ignore-rules", "--ephemeral",
        "--skip-git-repo-check", "--sandbox", "read-only", "--json", "--color", "never",
        "--cd", os.environ["TEMP"], "--model", model,
        "--output-schema", str(schema.resolve()),
        "-c", "model_reasoning_effort=high", "-c", "approval_policy=never",
        "-c", "web_search=disabled", "-c", "mcp_servers={}",
        "-c", "features.shell_tool=false", "-c", "features.unified_exec=false",
        "-c", "features.apps=false", "-c", "features.plugins=false",
        "-c", "features.multi_agent=false", "-c", "features.view_image=false",
        "-c", "features.image_generation=false", "-c", "features.skill_search=false",
        "-c", "features.skip_host_skill_discovery=true",
        "-c", "shell_environment_policy.inherit=none", "-",
    ]
    start = time.perf_counter()
    proc = subprocess.Popen(
        args, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        encoding="utf-8", text=True, cwd=os.environ["TEMP"], env=safe_env(),
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    timed_out = False
    try:
        stdout, stderr = proc.communicate(prompt, timeout=timeout)
    except subprocess.TimeoutExpired:
        timed_out = True
        # Terminate this child tree, never the owner's parent Codex session.
        if os.name == "nt":
            subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                           capture_output=True, check=False)
        else:
            proc.kill()
        stdout, stderr = proc.communicate(timeout=10)
    events = []
    for line in stdout.splitlines():
        with suppress(json.JSONDecodeError):
            events.append(json.loads(line))
    return {
        "checked_at": utc_now(), "model_requested": model, "reasoning_effort": "high",
        "command_argv": args, "working_directory": os.environ["TEMP"],
        "seconds": round(time.perf_counter() - start, 3), "exit_code": proc.returncode,
        "timed_out": timed_out, "prompt_utf8_bytes": len(prompt.encode("utf-8")),
        "events": events, "stderr_summary": redact(stderr[-3000:]),
    }


def verify(samples: Path) -> dict[str, object]:
    """Check the saved real outputs, provenance, bounds, and scratch-cwd isolation."""
    from jsonschema import Draft202012Validator

    def read_json(name: str) -> dict[str, object]:
        return json.loads((samples / name).read_text(encoding="utf-8"))

    summaries = []
    for kind in ("tool_probe", "extraction", "adjustment"):
        call_name = "r4_tool_probe.json" if kind == "tool_probe" else f"r4_{kind}_call.json"
        call = read_json(call_name)
        assert call["exit_code"] == 0 and not call["timed_out"]
        assert Path(str(call["working_directory"])).resolve() != samples.parents[2].resolve()
        events = call["events"]
        assert isinstance(events, list)
        replies = [e["item"]["text"] for e in events
                   if e.get("item", {}).get("type") == "agent_message"]
        assert len(replies) == 1
        output = json.loads(replies[0])
        Draft202012Validator(read_json(f"r4_{kind}_schema.json")).validate(output)
        tool_items = [e for e in events if e.get("item", {}).get("type")
                      not in {None, "agent_message", "error"}]
        assert not tool_items, f"Unexpected tool item in {kind}"
        assert any(e["type"] == "turn.completed" for e in events)
        summaries.append({"call": kind, "schema_valid": True, "tool_items_observed": 0})

    extraction_call = read_json("r4_extraction_call.json")
    call_events = extraction_call["events"]
    assert isinstance(call_events, list)
    extracted = json.loads(next(e["item"]["text"] for e in call_events
                                if e.get("item", {}).get("type") == "agent_message"))["events"]
    prompt = (samples / "r4_extraction_prompt.txt").read_text(encoding="utf-8")
    messages = json.loads(prompt.split("\n\n", 1)[1])
    by_key = {m["source_key"]: m for m in messages}
    for event in extracted:
        assert 0 <= event["confidence"] <= 1
        assert event["source_keys"]
        assert all(k in by_key and k != "synthetic_injection/1" for k in event["source_keys"])
        assert any(event["evidence_quote"] in by_key[k]["text"] for k in event["source_keys"])

    adjustment_call = read_json("r4_adjustment_call.json")
    call_events = adjustment_call["events"]
    assert isinstance(call_events, list)
    adjustments = json.loads(next(e["item"]["text"] for e in call_events
                                  if e.get("item", {}).get("type") == "agent_message"))[
                                      "adjustments"]
    prompt = (samples / "r4_adjustment_prompt.txt").read_text(encoding="utf-8")
    payload = json.loads(prompt.split("\n\n", 1)[1])
    base = {row["raion_id"]: row for row in payload["base_forecasts"]}
    mapping = {row["source_key"]: row["allowed_target_raions"]
               for row in payload["target_mapping"]}
    seen = set()
    comparison = []
    for row in adjustments:
        key = (row["raion_id"], row["horizon_hours"])
        assert key not in seen and key[0] in base and key[1] in {1, 3, 12}
        seen.add(key)
        assert 0 <= row["logit_shift"] <= 0.5
        assert all(key[0] in mapping[k] for k in row["source_keys"])
        assert not row["logit_shift"] or row["source_keys"]
        p = base[key[0]][f"p_{key[1]}"]
        adjusted = 1 / (1 + math.exp(-(math.log(p / (1-p)) + row["logit_shift"])))
        comparison.append({"raion_id": key[0], "horizon_hours": key[1],
                           "base": p, "logit_shift": row["logit_shift"],
                           "adjusted": round(adjusted, 6)})
        if key[0] == "kyiv-city/kyiv-city":
            assert row["logit_shift"] == 0 and not row["source_keys"]
    assert len(seen) == 12
    for raion_id in base:
        values = [r["adjusted"] for r in sorted(comparison, key=lambda r: r["horizon_hours"])
                  if r["raion_id"] == raion_id]
        assert values == sorted(values)

    files = sorted(samples.glob("r4_*"))
    forbidden = re.compile(r"\b\d{6,12}:[A-Za-z0-9_-]{25,}\b|\bsk-[A-Za-z0-9_-]{15,}\b")
    for path in files:
        assert not forbidden.search(path.read_text(encoding="utf-8")), path.name
    return {"calls": summaries, "real_messages": len(messages)-1,
            "synthetic_injections": 1, "extracted_events": len(extracted),
            "source_keys_and_quotes_valid": True, "injection_source_references": 0,
            "adjustments": comparison, "monotonic_and_bounded": True,
            "files_scanned_for_token_patterns": len(files), "token_pattern_matches": 0,
            "strict_no_tools_guarantee": False,
            "deduplication_warning": "43345 and 43346 each occur in two drone events"}


def main() -> None:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    tg = sub.add_parser("telegram")
    tg.add_argument("handles", nargs="*")
    tg.add_argument("--cursors", action="store_true")
    tg.add_argument("--max-messages", type=int, default=3)
    tg.add_argument("--max-text-chars", type=int, default=500)
    sub.add_parser("models")
    profile = sub.add_parser("profiles")
    profile.add_argument("handles", nargs="+")
    check = sub.add_parser("verify")
    check.add_argument("--samples", type=Path, default=Path("docs/research/samples"))
    call = sub.add_parser("complete")
    call.add_argument("--model", required=True)
    call.add_argument("--schema", type=Path, required=True)
    call.add_argument("--timeout", type=int, default=120)
    args = parser.parse_args()
    if args.command == "telegram":
        result = telegram(args.handles or list(CANDIDATES), args.cursors,
                          args.max_messages, args.max_text_chars)
    elif args.command == "models":
        result = model_list()
    elif args.command == "profiles":
        result = profiles(args.handles)
    elif args.command == "verify":
        result = verify(args.samples)
    else:
        result = completion(args.model, args.schema, args.timeout)
    sys.stdout.reconfigure(encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
