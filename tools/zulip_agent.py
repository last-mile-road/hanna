"""Read and post in the HANNA Zulip channels as the bot.

    python tools/zulip_agent.py --recent 20
    python tools/zulip_agent.py --reply general "run form16" "Note: the treatment grid…"

This is the seam for an agent. It prints messages as JSON so a model or a script
can act on them; replying uses the same bot the portal links to.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core import settings

AUTH = (settings.ZULIP_BOT_EMAIL, settings.ZULIP_BOT_KEY)


def _require() -> None:
    if not (settings.ZULIP_SITE and settings.ZULIP_BOT_KEY):
        raise SystemExit("ZULIP_SITE and ZULIP_BOT_KEY are not configured")


def recent(channel: str, count: int) -> list[dict]:
    _require()
    response = requests.get(
        f"{settings.ZULIP_SITE}/api/v1/messages",
        params={
            "anchor": "newest",
            "num_before": count,
            "num_after": 0,
            "narrow": json.dumps([["channel", channel]]),
            "apply_markdown": "false",
        },
        auth=AUTH,
        timeout=20,
    )
    response.raise_for_status()
    return response.json().get("messages", [])


def reply(channel: str, topic: str, body: str) -> dict:
    _require()
    response = requests.post(
        f"{settings.ZULIP_SITE}/api/v1/messages",
        data={"type": "stream", "to": channel, "topic": topic, "content": body},
        auth=AUTH,
        timeout=20,
    )
    response.raise_for_status()
    return response.json()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--recent", type=int, nargs="?", const=20)
    parser.add_argument("--channel", default="general")
    parser.add_argument("--reply", nargs=2, metavar=("TOPIC", "MESSAGE"))
    args = parser.parse_args()

    if args.reply:
        print(json.dumps(reply(args.channel, args.reply[0], args.reply[1]), indent=2))
    elif args.recent is not None:
        for message in recent(args.channel, args.recent):
            print(json.dumps({
                "id": message["id"],
                "sender": message["sender_full_name"],
                "topic": message["subject"],
                "time": message["timestamp"],
                "content": message["content"],
            }))
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
