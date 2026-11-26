# Zulip — the country discussion

Organization: `https://hansennot.zulipchat.com` (Cloud Free: 10,000 searchable
messages, 5 GB files).

## Structure

Zulip renamed streams to **channels**; a channel contains **topics**. There are no
folders — channels are flat, so the names carry the structure.

| Channel | Id | Access | Use |
|---|---|---|---|
| `general` | 659053 | all countries | shared questions, what worked, what did not |
| `mozambique` | 659055 | Mozambique team | country-specific work |
| `announcements` | 659056 | all, admin posts only | releases, downtime |

Topic convention: `run form16 · 26 Nov`, `template`, `location list`, `release 0.1`.
One conversation per topic keeps the record searchable.

## The bot

`hanna-agent-bot@hansennot.zulipchat.com`, a generic bot. Its API key lives in
`/etc/hanna/hanna.env` as `ZULIP_BOT_KEY`; the site, channel ids and bot email sit
beside it. The portal reads the channels through this bot; the agent posts as it.

Rotate the key in Zulip → Settings → Bots → `hanna-agent` → regenerate, then
replace `ZULIP_BOT_KEY` and restart `hanna`.

## Agent seam

```bash
# recent messages in a channel, one JSON object per line
.venv/bin/python tools/zulip_agent.py --recent 20 --channel general

# post as the bot
.venv/bin/python tools/zulip_agent.py --channel general \
    --reply "run form16 · 26 Nov" "The treatment grid read F as E on form22."
```

The bot must be subscribed to a channel before it can post there.

## Portal

`/portal` shows a **Discussion** card: links to `#general` and `#mozambique`, and
their recent topics. It reads Zulip through the API; a Zulip outage leaves the
links working.

## First-day settings to check

- Invite-only joining.
- `general` public to the organization, `mozambique` private, `announcements`
  posting restricted to admins.
- Notification defaults for new members: email on mentions and direct messages.
