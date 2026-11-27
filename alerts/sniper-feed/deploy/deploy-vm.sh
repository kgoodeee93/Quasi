#!/usr/bin/env bash
# Deploy the sniper feed, and the trading bot that buys its alerts, to a small always-on Compute
# Engine VM (e2-micro, free-tier size).
#
# Run from Google Cloud Shell (or anywhere gcloud is logged in):
#   cd alerts/sniper-feed && ./deploy/deploy-vm.sh
#
# First run asks for your Moby API key, Telegram bot token and channel id (typed, not echoed)
# and stores them in Secret Manager. Re-runs keep them unless you pass new ones in the
# environment, then redeploy the latest code from REF.
#
# The bot runs in DRY RUN on every deploy unless BOT_LIVE=yes is given on that deploy: a plain
# re-run always comes back up in dry run.
#
# Env overrides: PROJECT, ZONE (default us-central1-a; the free e2-micro is us-central1,
#   us-east1 or us-west1), VM (sniper-feed), REF (git branch, default main), BUCKET,
#   SEND (1 = post alerts to Telegram, 0 = record only), BOT (1 = run the bot, 0 = don't),
#   BOT_LIVE (yes = real trades), PUBLISH (1 = public stats.json for the website), PUBLIC_BUCKET,
#   and MOBY_API_KEY, TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, COINGECKO_API_KEY,
#   SOLANA_TRACKER_API_KEY, SOLBOT_TELEGRAM_CHAT_ID, JUPITER_API_KEY to replace a stored secret.
set -euo pipefail
cd "$(dirname "$0")"

PROJECT="${PROJECT:-$(gcloud config get-value project 2>/dev/null)}"
ZONE="${ZONE:-us-central1-a}"
REGION="${ZONE%-*}"
VM="${VM:-sniper-feed}"
REPO="${REPO:-https://github.com/kgoodeee93/Quasi.git}"
REF="${REF:-main}"
BUCKET="${BUCKET:-${PROJECT}-sniper-feed}"
SEND="${SEND:-1}"
BOT="${BOT:-1}"
BOT_LIVE="${BOT_LIVE:-no}"
PUBLISH="${PUBLISH:-1}"
PUBLIC_BUCKET="${PUBLIC_BUCKET:-${PROJECT}-sniper-feed-public}"
SA="sniper-feed@${PROJECT}.iam.gserviceaccount.com"

die() { echo "error: $*" >&2; exit 1; }
[ -n "$PROJECT" ] || die "no project set. Run: gcloud config set project YOUR_PROJECT_ID"
case "$SEND" in 0|1) ;; *) die "SEND must be 0 or 1";; esac
case "$BOT" in 0|1) ;; *) die "BOT must be 0 or 1";; esac
case "$BOT_LIVE" in yes|no) ;; *) die "BOT_LIVE must be yes or no";; esac
case "$PUBLISH" in 0|1) ;; *) die "PUBLISH must be 0 or 1";; esac
[ "$BOT_LIVE" = no ] || [ "$BOT" = 1 ] || die "BOT_LIVE=yes needs BOT=1"

if [ "$BOT_LIVE" = yes ]; then
  echo "!!  LIVE TRADING: the bot will spend real SOL from the wallet whose key you enter, on every"
  echo "!!  feed alert, within the limits in bots/solbot/config.py. Use a burner wallet."
  read -rp "Type LIVE to confirm: " confirm
  [ "$confirm" = LIVE ] || die "not confirmed; nothing changed"
fi

echo "==> Project $PROJECT, zone $ZONE, VM $VM, code from $REF, bucket gs://$BUCKET"
gcloud services enable compute.googleapis.com secretmanager.googleapis.com storage.googleapis.com \
  iam.googleapis.com --project "$PROJECT"

if ! gcloud iam service-accounts describe "$SA" --project "$PROJECT" >/dev/null 2>&1; then
  echo "==> Creating service account $SA"
  gcloud iam service-accounts create sniper-feed --project "$PROJECT" \
    --display-name "Sniper feed VM (reads its secrets, writes its backups)"
fi

# --- secrets: only the VM's service account can read them ---------------------------------------
store_secret() {  # store_secret SECRET_ID "value"
  local id="$1" value="$2"
  if ! gcloud secrets describe "$id" --project "$PROJECT" >/dev/null 2>&1; then
    gcloud secrets create "$id" --project "$PROJECT" --replication-policy automatic >/dev/null
  fi
  printf '%s' "$value" | gcloud secrets versions add "$id" --project "$PROJECT" --data-file=- >/dev/null
  echo "    stored $id"
}
has_secret() { gcloud secrets versions access latest --secret "$1" --project "$PROJECT" >/dev/null 2>&1; }

setup_secret() {  # setup_secret ENV_NAME SECRET_ID required|optional hidden|visible "prompt"
  local name="$1" id="$2" need="$3" vis="$4" prompt="$5" value="${!1:-}"
  if [ -z "$value" ] && has_secret "$id"; then
    echo "    keeping $id"
  else
    if [ -z "$value" ]; then
      if [ "$vis" = hidden ]; then read -rsp "$prompt: " value; echo; else read -rp "$prompt: " value; fi
    fi
    if [ -z "$value" ]; then
      [ "$need" = optional ] && { echo "    skipped $id"; return 0; }
      die "$name is required"
    fi
    store_secret "$id" "$value"
  fi
  gcloud secrets add-iam-policy-binding "$id" --project "$PROJECT" --member "serviceAccount:$SA" \
    --role roles/secretmanager.secretAccessor >/dev/null
}

echo "==> Secrets"
setup_secret MOBY_API_KEY sniper-feed-moby-api-key required hidden "Moby API key"
setup_secret TELEGRAM_BOT_TOKEN sniper-feed-telegram-bot-token required hidden "Telegram bot token (from @BotFather)"
setup_secret TELEGRAM_CHAT_ID sniper-feed-telegram-chat-id required visible "Telegram channel id (e.g. -1001234567890)"
setup_secret COINGECKO_API_KEY sniper-feed-coingecko-api-key optional hidden \
  "CoinGecko paid API key (Enter to skip and use the free GeckoTerminal API)"
setup_secret SOLANA_TRACKER_API_KEY sniper-feed-solanatracker-api-key optional hidden \
  "Solana Tracker API key (Enter to skip; adds sniper and bundle data)"
if [ "$BOT" = 1 ]; then
  setup_secret SOLBOT_TELEGRAM_CHAT_ID solbot-telegram-chat-id optional visible \
    "Telegram chat id for the bot's trade pings, NOT the alert channel (Enter to skip)"
  setup_secret JUPITER_API_KEY solbot-jupiter-api-key optional hidden \
    "Jupiter API key (Enter to skip; keyless works at the bot's volume)"
fi
if [ "$BOT_LIVE" = yes ]; then
  setup_secret WALLET_PRIVATE_KEY solbot-wallet-private-key required hidden "Burner wallet private key (base58)"
  setup_secret RPC_URL solbot-rpc-url required hidden "Solana RPC URL (Helius, Triton, QuickNode...)"
fi

if [ "$SEND" = 1 ]; then
  echo "==> Checking the bot can post to the channel"
  token="$(gcloud secrets versions access latest --secret sniper-feed-telegram-bot-token --project "$PROJECT")"
  chat="$(gcloud secrets versions access latest --secret sniper-feed-telegram-chat-id --project "$PROJECT")"
  # The token goes to curl on stdin, not the command line, so it never shows in `ps`.
  if ! printf 'url = "https://api.telegram.org/bot%s/sendMessage"\n' "$token" | curl -fsS -K - \
      --data-urlencode "chat_id=$chat" --data-urlencode "text=Sniper feed connected. Alerts will post here." \
      -o /dev/null; then
    die "Telegram refused the test message. Check the bot is an admin of the channel and the id is right."
  fi
  unset token
fi

# --- backups bucket ------------------------------------------------------------------------------
if ! gcloud storage buckets describe "gs://$BUCKET" --project "$PROJECT" >/dev/null 2>&1; then
  echo "==> Creating gs://$BUCKET"
  gcloud storage buckets create "gs://$BUCKET" --project "$PROJECT" --location "$REGION" \
    --uniform-bucket-level-access --public-access-prevention
fi
# Versioning keeps the copies each hourly backup replaces, so a bad backup can't erase the track
# record; versions more than 168 replacements old (about a week of hourly backups) are deleted.
LIFECYCLE="$(mktemp)"
trap 'rm -f "$LIFECYCLE"' EXIT
printf '%s' '{"rule": [{"action": {"type": "Delete"}, "condition": {"numNewerVersions": 168, "isLive": false}}]}' \
  > "$LIFECYCLE"
gcloud storage buckets update "gs://$BUCKET" --project "$PROJECT" --versioning \
  --lifecycle-file "$LIFECYCLE" >/dev/null
gcloud storage buckets add-iam-policy-binding "gs://$BUCKET" --member "serviceAccount:$SA" \
  --role roles/storage.objectAdmin >/dev/null

# --- public track record (stats.json for the website) ---------------------------------------------
if [ "$PUBLISH" = 1 ]; then
  if ! gcloud storage buckets describe "gs://$PUBLIC_BUCKET" --project "$PROJECT" >/dev/null 2>&1; then
    echo "==> Creating public gs://$PUBLIC_BUCKET (holds only stats.json: alerts older than 24h, aggregates)"
    gcloud storage buckets create "gs://$PUBLIC_BUCKET" --project "$PROJECT" --location "$REGION" \
      --uniform-bucket-level-access
  fi
  if gcloud storage buckets add-iam-policy-binding "gs://$PUBLIC_BUCKET" --member allUsers \
      --role roles/storage.objectViewer >/dev/null 2>&1; then
    gcloud storage buckets add-iam-policy-binding "gs://$PUBLIC_BUCKET" --member "serviceAccount:$SA" \
      --role roles/storage.objectAdmin >/dev/null
  else
    echo "    warning: this project doesn't allow public buckets; skipping stats.json (PUBLISH=0 to silence)"
    PUBLIC_BUCKET=""
  fi
else
  PUBLIC_BUCKET=""
fi

# --- the VM ------------------------------------------------------------------------------------
META="sniper-repo=$REPO,sniper-ref=$REF,sniper-bucket=$BUCKET,sniper-send=$SEND"
META="$META,sniper-public-bucket=$PUBLIC_BUCKET,solbot-enabled=$BOT,solbot-live=$BOT_LIVE"
if gcloud compute instances describe "$VM" --zone "$ZONE" --project "$PROJECT" >/dev/null 2>&1; then
  echo "==> Updating $VM and rerunning its setup"
  gcloud compute instances add-metadata "$VM" --zone "$ZONE" --project "$PROJECT" \
    --metadata "$META" --metadata-from-file startup-script=vm-startup.sh
  gcloud compute ssh "$VM" --zone "$ZONE" --project "$PROJECT" \
    --command "sudo google_metadata_script_runner startup"
else
  echo "==> Creating $VM (setup runs on first boot, about 2 minutes)"
  gcloud compute instances create "$VM" --zone "$ZONE" --project "$PROJECT" \
    --machine-type e2-micro --image-family debian-12 --image-project debian-cloud \
    --boot-disk-size 30GB --boot-disk-type pd-standard \
    --service-account "$SA" --scopes cloud-platform \
    --shielded-secure-boot --shielded-vtpm --shielded-integrity-monitoring \
    --metadata "$META" --metadata-from-file startup-script=vm-startup.sh
fi

BOT_MODE="off"
if [ "$BOT" = 1 ]; then BOT_MODE="dry run"; [ "$BOT_LIVE" = yes ] && BOT_MODE="LIVE TRADING"; fi
PUBLIC_LINE=""
[ -z "$PUBLIC_BUCKET" ] || PUBLIC_LINE="Public track record (updated hourly): https://storage.googleapis.com/$PUBLIC_BUCKET/stats.json"
cat <<EOF

Done. Bot: $BOT_MODE.
$PUBLIC_LINE
Useful commands:
  Live log:      gcloud compute ssh $VM --zone $ZONE -- sudo journalctl -u sniper-feed -f
  Bot log:       gcloud compute ssh $VM --zone $ZONE -- sudo journalctl -u solbot -f
  Bot results:   gcloud compute ssh $VM --zone $ZONE -- 'cd /opt/quasi/bots/solbot && sudo -u sniper env SOLBOT_STATE_DIR=/var/lib/sniper-feed/solbot /opt/solbot-venv/bin/python bot.py report'
  Pause buying:  gcloud compute ssh $VM --zone $ZONE -- sudo -u sniper touch /var/lib/sniper-feed/solbot/PAUSE
  Resume:        gcloud compute ssh $VM --zone $ZONE -- sudo -u sniper rm /var/lib/sniper-feed/solbot/PAUSE
  Track record:  gcloud compute ssh $VM --zone $ZONE -- 'cd /opt/quasi/alerts/sniper-feed && sudo -u sniper /opt/sniper-venv/bin/python -m sniper_feed stats --db /var/lib/sniper-feed/feed.db'
  Backups:       gcloud storage ls gs://$BUCKET/
  Stop posting:  SEND=0 ./deploy/deploy-vm.sh
EOF
