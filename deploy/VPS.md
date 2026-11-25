# VPS — clean OCI build

Runbook for the test server. Written 2026-11-24. Nothing here exposes the app:
authentication is not built yet, so the machine is prepared and left closed.

## 1. Audit before creating anything

The terminated instance may have left billable and quota-consuming pieces behind.
Check each in the OCI console, region = the home region of the tenancy.

| Where | What to look for | Action |
|---|---|---|
| Compute → Instances | no running instance | terminated ones are already gone |
| Storage → Block Storage → **Boot Volumes** | orphaned boot volumes from the old instance | delete them; they count against 200 GB |
| Storage → Block Storage → **Block Volumes** | leftover data volumes | delete |
| Storage → Block Storage → **Backups** | old volume backups (5 free, a 6th fails) | delete all you do not need |
| Storage → Object Storage | test buckets and objects | empty and delete; 20 GB total |
| Networking → **Reserved public IPs** | the IP you kept | keep it; confirm it is not attached to a dead instance |
| Governance → Limits, Quotas and Usage | block storage, object storage, A1 cores | confirm 200 GB, 20 GB, 2 OCPU |

The free ARM allowance is 2 OCPU / 12 GB since June 2026. The old 4/24 instance
is why the tenancy showed more; nothing else should be holding A1 capacity.

## 2. Create the instance

- **Shape:** `VM.Standard.A1.Flex`, **2 OCPU, 12 GB** (the whole Always Free allowance).
- **Image:** Ubuntu 24.04 (aarch64). Confirm the image is offered for ARM in your region.
- **Boot volume:** custom, **50 GB**. Leaves 150 GB of the 200 GB allowance for a data volume later.
- **Networking:** the existing VCN if it is clean, otherwise a new one with a public subnet.
- **Public IP:** assign the **reserved** IP, not an ephemeral one.
- **SSH key:** paste the public key below. Do not reuse the VPS or Hermes keys.

```
ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIL3yNUrvp7yefZZN1VvmS9JL+xhVvaNozYzmed2CneQW hanna-vps
```

This is a new key, separate from the GitHub one: private half at
`~/.ssh/hanna_vps`, no passphrase. Keeping it separate means revoking one does
not affect the other.

## 3. Network rules

Security list or NSG on the public subnet:

| Direction | Port | Source | Why |
|---|---|---|---|
| Ingress | 22 | your IP only (`/32`) | SSH; open it to the world only while setting up |
| Ingress | 80 | `0.0.0.0/0` | certificate issuance and redirect |
| Ingress | 443 | `0.0.0.0/0` | the portal, once it exists |
| Egress | all | `0.0.0.0/0` | package updates, model API |

Do not open 5432, 8100 or 8001. Postgres listens on localhost; uvicorn listens on
localhost behind nginx; MinerU likewise.

## 4. First login and hardening

```bash
ssh ubuntu@<reserved-ip>
sudo apt update && sudo apt -y upgrade
sudo timedatectl set-timezone UTC

# swap (2 GB) if none
sudo fallocate -l 2G /swapfile && sudo chmod 600 /swapfile
sudo mkswap /swapfile && sudo swapon /swapfile
echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab

# a deploy user, no password login
sudo adduser --disabled-password --gecos "" deploy
sudo usermod -aG sudo deploy
sudo install -d -m 700 -o deploy -g deploy /home/deploy/.ssh
sudo cp ~/.ssh/authorized_keys /home/deploy/.ssh/authorized_keys
sudo chown deploy:deploy /home/deploy/.ssh/authorized_keys
sudo chmod 600 /home/deploy/.ssh/authorized_keys
```

Then edit `/etc/ssh/sshd_config`:

```
PermitRootLogin no
PasswordAuthentication no
KbdInteractiveAuthentication no
```

```bash
sudo systemctl restart ssh
```

Firewall and updates:

```bash
sudo apt -y install ufw fail2ban unattended-upgrades
sudo ufw default deny incoming
sudo ufw default allow outgoing
sudo ufw allow from <your-ip> to any port 22 proto tcp
sudo ufw allow 80,443/tcp
sudo ufw --force enable
sudo dpkg-reconfigure -plow unattended-upgrades
```

## 5. Runtime

```bash
sudo apt -y install git python3-venv python3-pip build-essential libpq-dev \
  nginx certbot python3-certbot-nginx curl

# PostgreSQL 18 from PGDG (Ubuntu 24.04 ships 16)
sudo install -d /usr/share/postgresql-common/pgdg
sudo curl -o /usr/share/postgresql-common/pgdg/apt.postgresql.org.asc \
  https://www.postgresql.org/media/keys/ACCC4CF8.asc
echo "deb [signed-by=/usr/share/postgresql-common/pgdg/apt.postgresql.org.asc] \
  https://apt.postgresql.org/pub/repos/apt noble-pgdg main" | \
  sudo tee /etc/apt/sources.list.d/pgdg.list
sudo apt update && sudo apt -y install postgresql-18
```

Database and directory layout:

```bash
sudo -u postgres psql -c "CREATE ROLE hanna LOGIN PASSWORD '<set-me>';"
sudo -u postgres psql -c "CREATE DATABASE hanna OWNER hanna;"
sudo install -d -o deploy -g deploy /srv/hanna /var/lib/hanna/uploads /var/lib/hanna/output
sudo install -d -m 750 -o root -g deploy /etc/hanna
sudo tee /etc/hanna/hanna.env >/dev/null <<'EOF'
DATABASE_URL=postgresql+pg8000://hanna:<set-me>@127.0.0.1:5432/hanna
HANNA_UPLOAD_DIR=/var/lib/hanna/uploads
HANNA_OUTPUT_DIR=/var/lib/hanna/output
HANNA_COUNTRY_DATA=/var/lib/hanna/country_data
OPENROUTER_API_KEY=
MINERU_LOCAL_BASE=http://127.0.0.1:8001
EOF
sudo chown root:deploy /etc/hanna/hanna.env && sudo chmod 640 /etc/hanna/hanna.env
```

The app is not deployed yet: authentication comes first. When it does, nginx
gets one site for `hanna.lastmileroad.org`, proxying to `127.0.0.1:8100`, and
certbot issues the certificate.

## 6. DNS

At the registrar, add:

```
hanna.lastmileroad.org.  A  <reserved-ip>
```

Then, once nginx and the certificate exist, the portal is
`https://hanna.lastmileroad.org`. The root domain stays with the transcription
service.

## 7. What this machine is not

- Not exposed: no app until authentication exists.
- Not backed up: configure the nightly `pg_dump` to object storage before any
  country uploads a card.
- Not sized for table reading at speed: 2 ARM cores run MinerU, but a card's
  tables will take tens of seconds. Route table blocks to the Modal box if that
  is too slow in a call.

## Checklist

- [ ] Orphaned boot volumes and backups deleted
- [ ] Object storage emptied
- [ ] Reserved IP confirmed
- [ ] Instance created: A1.Flex 2/12, Ubuntu 24.04, 50 GB boot
- [ ] Reserved IP attached
- [ ] SSH key works, password login off
- [ ] ufw: 22 from one IP, 80/443 open
- [ ] PostgreSQL 18 running, `hanna` database created
- [ ] `/etc/hanna/hanna.env` in place, mode 640
- [ ] DNS `hanna` → reserved IP
