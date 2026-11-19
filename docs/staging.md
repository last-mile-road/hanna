# Local staging — the production-like run before the VPS

The laptop can run the same shape as the test server, minus TLS and the reverse
proxy. Use it for the full round before touching the VPS, and use the VPS for the
round that proves the deployment.

## What the local stack is

| Piece | Local | VPS |
|---|---|---|
| Database | PostgreSQL 18 on `127.0.0.1:55432` | managed or system PostgreSQL |
| Table reader | `mineru-api` on `127.0.0.1:8001` | same service, systemd |
| App | `uvicorn server.app:app --port 8100` | uvicorn behind nginx + TLS |
| Worker | `python -m server.worker` | systemd unit |
| Public entry | cloudflared tunnel | DNS + certificate |
| Images | `output/uploads`, local disk | object storage, signed links |
| Data | country data in `country_data/` | country data on the server volume |

## Rules for a staging run

- A separate database (`hanna_staging`), loaded with `tools/load_pack.py`. Do not
  test on the development ledger; a stale row hides a migration bug.
- No `--reload`, no debug toolbar. Run the commands the VPS will run.
- One card, front and back, through capture → extraction → review → finalise →
  map → report. Then a closed card and a re-scan.
- Restart the app and the worker mid-round: the job must resume or fail visibly,
  not hang.
- Check the retention clock: an uploaded image older than the configured window
  is gone.
- Record timings and failures in the session journal; they are the baseline the
  VPS is compared against.

## What local cannot prove

TLS, the reverse proxy, file permissions under systemd, object storage, backups,
and the phone on a real mobile network. Those belong to the VPS round.
