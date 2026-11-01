"""Run one card through the pipeline.

    python tools/run_card.py --form form16
    python tools/run_card.py --form form16 --block general_demographic
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.run import run_card


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--form", default="form16")
    ap.add_argument("--profile", default="or-qwen3vl-8b-v1")
    ap.add_argument("--purpose", default="slice")
    ap.add_argument("--block", action="append", dest="blocks")
    ap.add_argument("--routing", action="store_true")
    ap.add_argument("--routing-file", default="routing.yaml")
    args = ap.parse_args()

    result = run_card(
        args.form,
        profile_id=args.profile,
        purpose=args.purpose,
        only_blocks=args.blocks,
        routing=args.routing,
        routing_file=args.routing_file,
    )
    print(json.dumps({k: v for k, v in result.items() if k != "blocks"}, indent=2))
    for block in result["blocks"]:
        print(
            f"{block['block']:22s} {block['status']:6s} "
            f"{block['correct']}/{block['judged']} "
            f"{block['latency_ms']} ms"
        )


if __name__ == "__main__":
    main()
