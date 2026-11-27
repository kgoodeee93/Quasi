#!/bin/bash
# Runs as root on every boot of the sniper-feed VM (and on redeploy). Idempotent.
# Installs the code from git, a Python venv, and three systemd units:
#   sniper-feed.service         the scan + track loop, restarted if it dies
#   sniper-feed-backup.timer    hourly copy of the database to Cloud Storage
# Secrets are read from Secret Manager when the service starts; none are written to disk.
set -euo pipefail

md() { curl -fsS -H 'Metadata-Flavor: Google' \
  "http://metadata.google.internal/computeMetadata/v1/instance/attributes/$1" 2>/dev/null || true; }
REPO="$(md sniper-repo)"; REF="$(md sniper-ref)"; BUCKET="$(md sniper-bucket)"; SEND="$(md sniper-send)"
[ -n "$REPO" ] && [ -n "$REF" ] && [ -n "$BUCKET" ] || { echo "sniper-feed: metadata missing" >&2; exit 1; }
case "$REF" in *[!A-Za-z0-9._/-]*) echo "sniper-feed: bad ref" >&2; exit 1;; esac
case "$BUCKET" in *[!a-z0-9._-]*) echo "sniper-feed: bad bucket" >&2; exit 1;; esac

APP=/opt/quasi
VENV=/opt/sniper-venv
STATE=/var/lib/sniper-feed

command -v gcloud >/dev/null || { echo "sniper-feed: gcloud CLI missing; use a Debian GCE image" >&2; exit 1; }
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq git python3-venv >/dev/null

id sniper >/dev/null 2>&1 || useradd --system --home-dir "$STATE" --shell /usr/sbin/nologin sniper
install -d -o sniper -g sniper -m 700 "$STATE"

if [ -d "$APP/.git" ]; then
  git -C "$APP" fetch -q --depth 1 origin "$REF"
  git -C "$APP" checkout -q -f FETCH_HEAD
else
  git clone -q --depth 1 --branch "$REF" "$REPO" "$APP"
fi
[ -x "$VENV/bin/python" ] || python3 -m venv "$VENV"
"$VENV/bin/pip" install -q --upgrade pip
"$VENV/bin/pip" install -q -r "$APP/alerts/sniper-feed/requirements.txt"

SEND_FLAG=""
[ "$SEND" = "1" ] && SEND_FLAG="--send"

cat > /usr/local/bin/sniper-feed-run <<EOF
#!/bin/bash
set -euo pipefail
secret() { gcloud secrets versions access latest --secret="\$1" 2>/dev/null || true; }
export MOBY_API_KEY="\$(secret sniper-feed-moby-api-key)"
export TELEGRAM_BOT_TOKEN="\$(secret sniper-feed-telegram-bot-token)"
export TELEGRAM_CHAT_ID="\$(secret sniper-feed-telegram-chat-id)"
export COINGECKO_API_KEY="\$(secret sniper-feed-coingecko-api-key)"
export FEED_DB=$STATE/feed.db
# A fresh disk starts from the last backup, so the track record survives a rebuilt VM. If a
# backup exists but can't be fetched, fail (systemd retries in 30s) rather than start empty.
if [ ! -s "\$FEED_DB" ] && gcloud storage ls "gs://$BUCKET/feed.db" >/dev/null 2>&1; then
  gcloud storage cp -q "gs://$BUCKET/feed.db" "\$FEED_DB"
fi
cd $APP/alerts/sniper-feed
exec $VENV/bin/python -m sniper_feed run --every 60 $SEND_FLAG
EOF

cat > /usr/local/bin/sniper-feed-backup <<EOF
#!/bin/bash
set -euo pipefail
# Nothing to save until the feed has started (and restored any earlier backup): never upload an
# empty database over the track record.
[ -s $STATE/feed.db ] || { echo "no database yet; skipping backup"; exit 0; }
cd $APP/alerts/sniper-feed
$VENV/bin/python -m sniper_feed backup --db $STATE/feed.db --to $STATE/backup.db
gcloud storage cp -q $STATE/backup.db "gs://$BUCKET/feed.db"
gcloud storage cp -q $STATE/backup.db "gs://$BUCKET/daily/feed-\$(date -u +%F).db"
EOF
chmod 755 /usr/local/bin/sniper-feed-run /usr/local/bin/sniper-feed-backup

UNIT_COMMON="User=sniper
Group=sniper
WorkingDirectory=$APP/alerts/sniper-feed
NoNewPrivileges=yes
PrivateTmp=yes
ProtectSystem=strict
ProtectHome=yes
ReadWritePaths=$STATE"

cat > /etc/systemd/system/sniper-feed.service <<EOF
[Unit]
Description=Sniper feed: scan, alert, track
Wants=network-online.target
After=network-online.target

[Service]
ExecStart=/usr/local/bin/sniper-feed-run
Restart=always
RestartSec=30
$UNIT_COMMON

[Install]
WantedBy=multi-user.target
EOF

cat > /etc/systemd/system/sniper-feed-backup.service <<EOF
[Unit]
Description=Sniper feed: back up the database to Cloud Storage

[Service]
Type=oneshot
ExecStart=/usr/local/bin/sniper-feed-backup
$UNIT_COMMON
EOF

cat > /etc/systemd/system/sniper-feed-backup.timer <<EOF
[Unit]
Description=Hourly sniper feed backup

[Timer]
OnCalendar=hourly
RandomizedDelaySec=300
Persistent=true

[Install]
WantedBy=timers.target
EOF

systemctl daemon-reload
systemctl enable -q sniper-feed.service sniper-feed-backup.timer
systemctl restart sniper-feed.service
systemctl start sniper-feed-backup.timer
echo "sniper-feed: running $(git -C "$APP" rev-parse --short HEAD) from $REF (send=$SEND)"
