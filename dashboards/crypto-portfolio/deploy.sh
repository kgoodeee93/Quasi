#!/usr/bin/env bash
# Deploy the dashboard to Google Cloud Run, password-protected, with your wallets
# and API keys stored in Secret Manager (never in the image or the repo).
#
# Run from Google Cloud Shell (or anywhere gcloud is logged in), inside this folder:
#   ./deploy.sh
#
# Reads:   wallets.json (required), .env (optional: ALCHEMY_API_KEY, COINGECKO_API_KEY)
# Env overrides: PROJECT, REGION (default us-central1), SERVICE (default crypto-portfolio),
#                DASHBOARD_PASSWORD (default: keep the existing one, or generate one)
# Safe to re-run: it adds new secret versions and rolls out a new revision.
set -euo pipefail
cd "$(dirname "$0")"

PROJECT="${PROJECT:-$(gcloud config get-value project 2>/dev/null)}"
REGION="${REGION:-us-central1}"
SERVICE="${SERVICE:-crypto-portfolio}"

die() { echo "error: $*" >&2; exit 1; }
[ -n "$PROJECT" ] || die "no project set. Run: gcloud config set project YOUR_PROJECT_ID"
[ -f wallets.json ] || die "wallets.json not found. Copy wallets.example.json to wallets.json and add your addresses."
python3 -c "import json,sys; json.load(open('wallets.json'))" || die "wallets.json is not valid JSON"

env_value() {  # read KEY from .env without sourcing it
  [ -f .env ] || return 0
  grep -E "^$1=" .env | tail -1 | cut -d= -f2- | sed -e 's/^["'\'']//' -e 's/["'\'']$//'
}

echo "==> Project $PROJECT, region $REGION, service $SERVICE"
gcloud services enable run.googleapis.com cloudbuild.googleapis.com \
  artifactregistry.googleapis.com secretmanager.googleapis.com --project "$PROJECT"

PROJECT_NUMBER="$(gcloud projects describe "$PROJECT" --format='value(projectNumber)')"
RUNTIME_SA="${PROJECT_NUMBER}-compute@developer.gserviceaccount.com"

secret_exists() { gcloud secrets describe "$1" --project "$PROJECT" >/dev/null 2>&1; }

put_secret() {  # put_secret NAME  (value on stdin)
  local name="$1"
  if ! secret_exists "$name"; then
    gcloud secrets create "$name" --replication-policy=automatic --project "$PROJECT" >/dev/null
  fi
  gcloud secrets versions add "$name" --data-file=- --project "$PROJECT" >/dev/null
  gcloud secrets add-iam-policy-binding "$name" --project "$PROJECT" \
    --member "serviceAccount:$RUNTIME_SA" --role roles/secretmanager.secretAccessor >/dev/null
  echo "    secret $name updated"
}

echo "==> Storing secrets"
put_secret crypto-wallets < wallets.json
SECRETS="/secrets/wallets.json=crypto-wallets:latest"

NEW_PASSWORD=""
if [ -n "${DASHBOARD_PASSWORD:-}" ]; then
  printf %s "$DASHBOARD_PASSWORD" | put_secret crypto-dashboard-password
elif ! secret_exists crypto-dashboard-password; then
  NEW_PASSWORD="$(python3 -c 'import secrets; print(secrets.token_urlsafe(15))')"
  printf %s "$NEW_PASSWORD" | put_secret crypto-dashboard-password
fi
SECRETS="$SECRETS,DASHBOARD_PASSWORD=crypto-dashboard-password:latest"

for key in ALCHEMY_API_KEY COINGECKO_API_KEY; do
  value="${!key:-$(env_value "$key")}"
  name="crypto-$(echo "$key" | tr 'A-Z_' 'a-z-')"
  if [ -n "$value" ]; then
    printf %s "$value" | put_secret "$name"
  fi
  if secret_exists "$name"; then SECRETS="$SECRETS,$key=$name:latest"; fi
done

echo "==> Building and deploying (takes a few minutes the first time)"
gcloud run deploy "$SERVICE" \
  --source . \
  --project "$PROJECT" \
  --region "$REGION" \
  --allow-unauthenticated \
  --min-instances 0 \
  --max-instances 1 \
  --memory 512Mi \
  --set-env-vars "WALLETS_FILE=/secrets/wallets.json,CACHE_TTL=120,COINGECKO_PLAN=$(env_value COINGECKO_PLAN | grep . || echo demo)" \
  --set-secrets "$SECRETS"

URL="$(gcloud run services describe "$SERVICE" --project "$PROJECT" --region "$REGION" --format='value(status.url)')"
echo
echo "Dashboard: $URL"
echo "Login: any username, password from Secret Manager secret 'crypto-dashboard-password'."
if [ -n "$NEW_PASSWORD" ]; then
  echo "Generated password (save it in your password manager): $NEW_PASSWORD"
fi
