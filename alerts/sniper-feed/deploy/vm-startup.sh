#!/bin/bash
# Runs as root on every boot of the sniper-feed VM (and on redeploy). Idempotent.
# Installs the code from git, Python venvs, and these systemd units:
#   sniper-feed.service         the scan + track loop, restarted if it dies
#   solbot.service              the trading bot, buying the feed's alerts (dry run unless solbot-live=yes)
#   sniper-feed-backup.timer    hourly: database + bot state to Cloud Storage, public stats.json
# Secrets are read from Secret Manager when a service starts; none are written to disk.
set -euo pipefail

md() { curl -fsS -H 'Metadata-Flavor: Google' \
  "http://metadata.google.internal/computeMetadata/v1/instance/attributes/$1" 2>/dev/null || true; }
REPO="$(md sniper-repo)"; REF="$(md sniper-ref)"; BUCKET="$(md sniper-bucket)"; SEND="$(md sniper-send)"
PUBLIC_BUCKET="$(md sniper-public-bucket)"; BOT="$(md solbot-enabled)"; LIVE="$(md solbot-live)"
[ -n "$REPO" ] && [ -n "$REF" ] && [ -n "$BUCKET" ] || { echo "sniper-feed: metadata missing" >&2; exit 1; }
case "$REF" in *[!A-Za-z0-9._/-]*) echo "sniper-feed: bad ref" >&2; exit 1;; esac
case "$BUCKET$PUBLIC_BUCKET" in *[!a-z0-9._-]*) echo "sniper-feed: bad bucket" >&2; exit 1;; esac
case "$BOT" in 0|1|"") ;; *) echo "sniper-feed: bad solbot-enabled" >&2; exit 1;; esac
case "$LIVE" in yes|no|"") ;; *) echo "sniper-feed: bad solbot-live" >&2; exit 1;; esac
[ "$LIVE" = yes ] || LIVE=no

APP=/opt/quasi
VENV=/opt/sniper-venv
BOT_VENV=/opt/solbot-venv
STATE=/var/lib/sniper-feed
BOT_STATE=$STATE/solbot

command -v gcloud >/dev/null || { echo "sniper-feed: gcloud CLI missing; use a Debian GCE image" >&2; exit 1; }
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq git python3-venv >/dev/null

id sniper >/dev/null 2>&1 || useradd --system --home-dir "$STATE" --shell /usr/sbin/nologin sniper
install -d -o sniper -g sniper -m 700 "$STATE"
install -d -o sniper -g sniper -m 700 "$BOT_STATE"

if [ -d "$APP/.git" ]; then
  git -C "$APP" fetch -q --depth 1 origin "$REF"
  git -C "$APP" checkout -q -f FETCH_HEAD
else
  git clone -q --depth 1 --branch "$REF" "$REPO" "$APP"
fi
[ -x "$VENV/bin/python" ] || python3 -m venv "$VENV"
"$VENV/bin/pip" install -q --upgrade pip
"$VENV/bin/pip" install -q -r "$APP/alerts/sniper-feed/requirements.txt"
if [ "$BOT" = 1 ]; then
  [ -x "$BOT_VENV/bin/python" ] || python3 -m venv "$BOT_VENV"
  "$BOT_VENV/bin/pip" install -q --upgrade pip
  "$BOT_VENV/bin/pip" install -q -r "$APP/bots/solbot/requirements.txt"
fi

SEND_FLAG=""
[ "$SEND" = "1" ] && SEND_FLAG="--send"

# A fresh disk starts from the last backup, so the track record (and any open positions) survive a
# rebuilt VM. If a backup exists but can't be fetched, fail (systemd retries in 30s) rather than
# start empty.
RESTORE='restore() {  # restore LOCAL_FILE GS_URL
  if [ ! -s "$1" ] && gcloud storage ls "$2" >/dev/null 2>&1; then gcloud storage cp -q "$2" "$1"; fi
}'

cat > /usr/local/bin/sniper-feed-run <<EOF
#!/bin/bash
set -euo pipefail
secret() { gcloud secrets versions access latest --secret="\$1" 2>/dev/null || true; }
$RESTORE
export MOBY_API_KEY="\$(secret sniper-feed-moby-api-key)"
export TELEGRAM_BOT_TOKEN="\$(secret sniper-feed-telegram-bot-token)"
export TELEGRAM_CHAT_ID="\$(secret sniper-feed-telegram-chat-id)"
export COINGECKO_API_KEY="\$(secret sniper-feed-coingecko-api-key)"
export SOLANA_TRACKER_API_KEY="\$(secret sniper-feed-solanatracker-api-key)"
export FEED_DB=$STATE/feed.db
restore "\$FEED_DB" "gs://$BUCKET/feed.db"
cd $APP/alerts/sniper-feed
exec $VENV/bin/python -m sniper_feed run --every 60 $SEND_FLAG
EOF

cat > /usr/local/bin/solbot-run <<EOF
#!/bin/bash
set -euo pipefail
secret() { gcloud secrets versions access latest --secret="\$1" 2>/dev/null || true; }
$RESTORE
export SOLBOT_STATE_DIR=$BOT_STATE ENTRY_SOURCE=feed FEED_DB=$STATE/feed.db PYTHONUNBUFFERED=1
export JUPITER_API_KEY="\$(secret solbot-jupiter-api-key)"
# Trade pings go to their own chat, never the subscribers' alert channel; no chat id = no pings.
export TELEGRAM_BOT_TOKEN="\$(secret sniper-feed-telegram-bot-token)"
export TELEGRAM_CHAT_ID="\$(secret solbot-telegram-chat-id)"
if [ "$LIVE" = yes ]; then
  export SOLBOT_LIVE=yes
  export WALLET_PRIVATE_KEY="\$(secret solbot-wallet-private-key)"
  export RPC_URL="\$(secret solbot-rpc-url)"
fi
restore "$BOT_STATE/state.json" "gs://$BUCKET/solbot/state.json"
restore "$BOT_STATE/trades.jsonl" "gs://$BUCKET/solbot/trades.jsonl"
cd $APP/bots/solbot
exec $BOT_VENV/bin/python bot.py
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
# The bot's positions and trade journal (its writes are atomic, so a copy is always whole).
for f in state.json trades.jsonl; do
  if [ -s "$BOT_STATE/\$f" ]; then gcloud storage cp -q "$BOT_STATE/\$f" "gs://$BUCKET/solbot/\$f"; fi
done
# The public track record for the website: alerts only, details delayed 24h.
if [ -n "$PUBLIC_BUCKET" ]; then
  $VENV/bin/python -m sniper_feed publish --db $STATE/feed.db --to $STATE/stats.json
  gcloud storage cp -q --cache-control="public, max-age=300" $STATE/stats.json "gs://$PUBLIC_BUCKET/stats.json"
fi
EOF
chmod 755 /usr/local/bin/sniper-feed-run /usr/local/bin/solbot-run /usr/local/bin/sniper-feed-backup

UNIT_COMMON="User=sniper
Group=sniper
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
WorkingDirectory=$APP/alerts/sniper-feed
Restart=always
RestartSec=30
$UNIT_COMMON

[Install]
WantedBy=multi-user.target
EOF

cat > /etc/systemd/system/solbot.service <<EOF
[Unit]
Description=Solana trading bot (buys the sniper feed's alerts)
Wants=network-online.target
After=network-online.target sniper-feed.service

[Service]
ExecStart=/usr/local/bin/solbot-run
WorkingDirectory=$APP/bots/solbot
Restart=always
RestartSec=10
$UNIT_COMMON

[Install]
WantedBy=multi-user.target
EOF

cat > /etc/systemd/system/sniper-feed-backup.service <<EOF
[Unit]
Description=Sniper feed: back up the database and bot state; publish stats.json

[Service]
Type=oneshot
ExecStart=/usr/local/bin/sniper-feed-backup
WorkingDirectory=$APP/alerts/sniper-feed
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
if [ "$BOT" = 1 ]; then
  systemctl enable -q solbot.service
  systemctl restart solbot.service
else
  systemctl disable -q --now solbot.service 2>/dev/null || true
fi
BOT_MODE="off"
[ "$BOT" = 1 ] && BOT_MODE="$([ "$LIVE" = yes ] && echo LIVE || echo "dry run")"
echo "sniper-feed: running $(git -C "$APP" rev-parse --short HEAD) from $REF (send=$SEND, bot=$BOT_MODE)"
