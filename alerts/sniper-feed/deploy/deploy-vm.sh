#!/usr/bin/env bash
# Deploy the sniper feed to a small always-on Compute Engine VM (e2-micro, free-tier size).
#
# Run from Google Cloud Shell (or anywhere gcloud is logged in):
#   cd alerts/sniper-feed && ./deploy/deploy-vm.sh
#
# First run asks for your Moby API key, Telegram bot token and channel id (typed, not echoed)
# and stores them in Secret Manager. Re-runs keep them unless you pass new ones in the
# environment, then redeploy the latest code from REF.
#
# Env overrides: PROJECT, ZONE (default us-central1-a; the free e2-micro is us-central1,
#   us-east1 or us-west1), VM (sniper-feed), REF (git branch, default main), BUCKET,
#   SEND (1 = post to Telegram, 0 = record only), and MOBY_API_KEY, TELEGRAM_BOT_TOKEN,
#   TELEGRAM_CHAT_ID, COINGECKO_API_KEY to replace a stored secret.
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
SA="sniper-feed@${PROJECT}.iam.gserviceaccount.com"

die() { echo "error: $*" >&2; exit 1; }
[ -n "$PROJECT" ] || die "no project set. Run: gcloud config set project YOUR_PROJECT_ID"
case "$SEND" in 0|1) ;; *) die "SEND must be 0 or 1";; esac

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

# --- the VM ------------------------------------------------------------------------------------
META="sniper-repo=$REPO,sniper-ref=$REF,sniper-bucket=$BUCKET,sniper-send=$SEND"
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

cat <<EOF

Done. Useful commands:
  Live log:      gcloud compute ssh $VM --zone $ZONE -- sudo journalctl -u sniper-feed -f
  Track record:  gcloud compute ssh $VM --zone $ZONE -- 'cd /opt/quasi/alerts/sniper-feed && sudo -u sniper /opt/sniper-venv/bin/python -m sniper_feed stats --db /var/lib/sniper-feed/feed.db'
  Backups:       gcloud storage ls gs://$BUCKET/
  Stop posting:  SEND=0 ./deploy/deploy-vm.sh
EOF
