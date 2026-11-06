#!/usr/bin/env bash
# Warm the HANNA box before a test round and report per-engine ready times.
#
# The box scales to zero after HANNA_BOX_SCALEDOWN seconds idle (default 300).
# A cold boot takes ~5 minutes because three engines load in sequence; this
# script pays that cost once, before the first card, instead of inside a run.
#
# Usage:
#   source /home/sam/tmp/opencode/box-key.env   # or export HANNA_KEY=...
#   tools/warm_box.sh
set -euo pipefail

URL="${BOX_URL:-https://ariedk--hanna-box-box.us-east.modal.run}"
KEY="${HANNA_KEY:?set HANNA_KEY (the hanna-box-key secret)}"

echo "warming $URL …"
started=$(date +%s)
curl -s -m 60 "$URL/warm" -H "X-HANNA-Key: $KEY" >/dev/null || true
for i in $(seq 1 60); do
  body=$(curl -s -m 20 "$URL/ready" -H "X-HANNA-Key: $KEY" || true)
  echo "$body" | head -c 220; echo
  if echo "$body" | grep -q '"ready":true'; then
    echo "ready after $(( $(date +%s) - started ))s"
    exit 0
  fi
  sleep 10
done
echo "not ready after 10 minutes" >&2
exit 1
