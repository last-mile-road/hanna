#!/usr/bin/env python3
"""One card through all three readers on the deployed HANNA box.

    export HANNA_KEY=...            # the value in the hanna-shared-key secret
    python3 deploy/box_smoke.py --url https://<workspace>--hanna-box-box.modal.run \
        --card ../hanna_archive/versions/v4_hanna3_2025-2026/test_forms/form16.jpg

Waits for the box to warm (first call after scale-down is a cold start), then times
the 9B on one image question, PaddleOCR-VL and MinerU on the same card. Prints JSON
per reader so the numbers can go straight into a report.

Stdlib only: runs on any python3, no venv needed. Nothing is written to disk except
the printed output.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import sys
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path


def call(url: str, method: str = "GET", data: bytes | None = None,
         headers: dict | None = None, timeout: int = 1800) -> tuple[int, dict]:
    request = urllib.request.Request(url, data=data, headers=headers or {}, method=method)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read()
            try:
                return response.status, json.loads(body)
            except json.JSONDecodeError:
                return response.status, {"raw": body[:500].decode(errors="ignore")}
    except urllib.error.HTTPError as error:
        body = error.read()
        try:
            return error.code, json.loads(body)
        except json.JSONDecodeError:
            return error.code, {"raw": body[:500].decode(errors="ignore")}


def post_file(url: str, path: Path, key: str) -> tuple[int, dict]:
    boundary = uuid.uuid4().hex
    head = (f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="file"; filename="{path.name}"\r\n'
            f"Content-Type: application/octet-stream\r\n\r\n").encode()
    body = head + path.read_bytes() + f"\r\n--{boundary}--\r\n".encode()
    return call(url, "POST", body,
                {"Content-Type": f"multipart/form-data; boundary={boundary}",
                 "X-HANNA-Key": key})


def ask_llm(base: str, key: str, path: Path, prompt: str) -> tuple[int, float, dict]:
    encoded = base64.b64encode(path.read_bytes()).decode()
    payload = {
        "model": "hanna-9b",
        "max_tokens": 40,
        "temperature": 0,
        "messages": [{"role": "user", "content": [
            {"type": "text", "text": prompt},
            {"type": "image_url",
             "image_url": {"url": f"data:image/jpeg;base64,{encoded}"}},
        ]}],
    }
    started = time.monotonic()
    status, body = call(f"{base}/llm/v1/chat/completions", "POST",
                        json.dumps(payload).encode(),
                        {"Content-Type": "application/json", "X-HANNA-Key": key})
    return status, round(time.monotonic() - started, 1), body


def wait_warm(base: str, key: str, minutes: float) -> bool:
    deadline = time.monotonic() + minutes * 60
    last = None
    while time.monotonic() < deadline:
        try:
            status, body = call(f"{base}/warm", headers={"X-HANNA-Key": key}, timeout=30)
        except Exception as error:  # noqa: BLE001 - Modal answers 303 while booting
            print(f"  waiting: {error}", file=sys.stderr)
            time.sleep(15)
            continue
        if status == 200 and body.get("ready"):
            print(f"  warm after {body.get('elapsed_s')}s on {body.get('gpu')}")
            return True
        summary = body.get("engines") if isinstance(body, dict) else body
        if summary != last:
            print(f"  {status}: {summary}", file=sys.stderr)
            last = summary
        time.sleep(15)
    return False


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", required=True, help="deployed box base URL, no trailing slash")
    parser.add_argument("--card", required=True)
    parser.add_argument("--key", default=os.environ.get("HANNA_KEY", ""))
    parser.add_argument("--wait", type=float, default=15, help="minutes to wait for a cold start")
    parser.add_argument("--readers", default="llm,paddle,mineru")
    parser.add_argument("--prompt", default="What is the patient's name? Answer with the name only.")
    args = parser.parse_args()

    if not args.key:
        print("error: HANNA_KEY is not set (or pass --key)", file=sys.stderr)
        return 2
    card = Path(args.card)
    if not card.exists():
        print(f"error: card not found: {card}", file=sys.stderr)
        return 2

    base = args.url.rstrip("/")
    print(f"box: {base}\ncard: {card}")

    status, ready = call(f"{base}/ready", headers={"X-HANNA-Key": args.key}, timeout=60)
    print(f"state before warm: {status}")
    if status != 200:
        print(json.dumps(ready, indent=2)[:2000])

    if not wait_warm(base, args.key, args.wait):
        print("error: box did not warm in time. /ready follows:", file=sys.stderr)
        _, state = call(f"{base}/ready", headers={"X-HANNA-Key": args.key}, timeout=60)
        print(json.dumps(state, indent=2)[:4000], file=sys.stderr)
        return 1

    readers = args.readers.split(",")
    results = {}

    if "llm" in readers:
        status, seconds, body = ask_llm(base, args.key, card, args.prompt)
        text = ""
        try:
            text = body["choices"][0]["message"]["content"]
        except Exception:  # noqa: BLE001 - error bodies vary
            text = json.dumps(body)[:300]
        results["llm"] = {"status": status, "seconds": seconds, "answer": text[:300]}
        print(f"\nllm       {status} {seconds}s -> {text[:200]!r}")

    for reader in [r for r in readers if r in {"paddle", "mineru"}]:
        started = time.monotonic()
        status, body = post_file(f"{base}/parse/{reader}", card, args.key)
        elapsed = round(time.monotonic() - started, 1)
        markdown = (body or {}).get("markdown", "") if isinstance(body, dict) else ""
        results[reader] = {
            "status": status,
            "seconds": body.get("seconds") if isinstance(body, dict) else None,
            "round_trip_s": elapsed,
            "ok": body.get("ok") if isinstance(body, dict) else None,
            "chars": len(markdown),
            "tables": markdown.lower().count("<table"),
            "head": markdown[:300],
        }
        print(f"{reader:9s} {status} {elapsed}s ok={results[reader]['ok']} "
              f"chars={len(markdown)} tables={results[reader]['tables']}")

    results["box"] = call(f"{base}/ready", headers={"X-HANNA-Key": args.key}, timeout=60)[1]
    out = Path("/home/sam/tmp/opencode") / f"box-smoke-{card.stem}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(results, indent=2))
    print(f"\nwritten: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
