# Backup and restore

Nightly at 03:30 UTC, `hanna-backup.timer` runs `deploy/backup_to_dropbox.py`:
`pg_dump` of the `hanna` database plus the uploads directory, tarred, encrypted
with Fernet, and uploaded to Dropbox under `/lrm-backups/hanna/YYYY-MM-DD/`.
Fourteen daily folders are kept.

Same Dropbox app as the transcription service, separate folder.

## Environment (in `/etc/hanna/hanna.env`, mode 640)

```
BACKUP_ENCRYPTION_KEY=<Fernet key, generated once>
DROPBOX_APP_KEY=...
DROPBOX_APP_SECRET=...
DROPBOX_REFRESH_TOKEN=...
DROPBOX_HANNA_ROOT=/lrm-backups/hanna
```

Generate the key once and keep a copy off the server:

```bash
python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

The Dropbox values are the ones already used by the transcription service; copy
them from its `.env`. The refresh token needs the same app scopes
(`files.content.write`, `files.content.read`).

## Install

```bash
sudo cp deploy/hanna-backup.service deploy/hanna-backup.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now hanna-backup.timer
sudo systemctl start hanna-backup.service      # first run, watch it
journalctl -u hanna-backup.service -n 30
```

## Restore

```bash
# download the .enc from Dropbox, then:
python3 - <<'PY'
from cryptography.fernet import Fernet
key = open('/etc/hanna/backup.key').read().strip()
data = open('hanna-YYYY-MM-DD.enc','rb').read()
open('hanna-YYYY-MM-DD.tar.gz','wb').write(Fernet(key.encode()).decrypt(data))
PY
tar xzf hanna-YYYY-MM-DD.tar.gz
pg_restore -h 127.0.0.1 -U hanna -d hanna_restore hanna.dump
```

Restore into a scratch database first, never over the live one. Do one restore
drill before the first country uploads anything.
