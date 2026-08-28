#!/usr/bin/env bash
#
# One-time GCP setup for deploying Kira to Cloud Run.
#
# Prerequisites:
#   - gcloud CLI installed and authenticated (gcloud auth login)
#   - A GCP project created and selected (gcloud config set project <ID>)
#   - Your GitHub repo: owner/repo (e.g. badhrisuresh/Kira)
#
# Usage:
#   chmod +x scripts/gcp-setup.sh
#   ./scripts/gcp-setup.sh

set -euo pipefail

# ── Configuration ───────────────────────────────────────────────

PROJECT_ID=$(gcloud config get-value project 2>/dev/null)
if [[ -z "$PROJECT_ID" ]]; then
  echo "ERROR: No GCP project set. Run: gcloud config set project <PROJECT_ID>"
  exit 1
fi

REGION="${GCP_REGION:-us-central1}"
SERVICE_ACCOUNT_NAME="kira-github-deploy"
SERVICE_ACCOUNT="${SERVICE_ACCOUNT_NAME}@${PROJECT_ID}.iam.gserviceaccount.com"
WIF_POOL="github-actions-pool"
WIF_PROVIDER="github-actions-provider"
REPO_NAME="kira"
AR_LOCATION="$REGION"

read -rp "GitHub repo (owner/repo, e.g. badhrisuresh/Kira): " GITHUB_REPO
if [[ -z "$GITHUB_REPO" ]]; then
  echo "ERROR: GitHub repo is required."
  exit 1
fi

echo ""
echo "=== Kira GCP Setup ==="
echo "Project:    $PROJECT_ID"
echo "Region:     $REGION"
echo "GitHub:     $GITHUB_REPO"
echo ""

# ── Step 1: Enable APIs ────────────────────────────────────────

echo "1/7 Enabling required APIs..."
gcloud services enable \
  run.googleapis.com \
  artifactregistry.googleapis.com \
  secretmanager.googleapis.com \
  iamcredentials.googleapis.com \
  cloudbuild.googleapis.com \
  --quiet

# ── Step 2: Create Artifact Registry repository ────────────────

echo "2/7 Creating Artifact Registry repository..."
if gcloud artifacts repositories describe "$REPO_NAME" \
    --location="$AR_LOCATION" --format="value(name)" 2>/dev/null; then
  echo "  Repository '$REPO_NAME' already exists, skipping."
else
  gcloud artifacts repositories create "$REPO_NAME" \
    --repository-format=docker \
    --location="$AR_LOCATION" \
    --description="Kira Docker images"
fi

# ── Step 3: Create secrets in Secret Manager ───────────────────

echo "3/7 Creating secrets in Secret Manager..."

SECRETS=(
  GOOGLE_API_KEY
  FAL_KEY
  GCS_BUCKET_NAME
  YOUTUBE_TOKEN_JSON
  TWILIO_ACCOUNT_SID
  TWILIO_AUTH_TOKEN
  TWILIO_WHATSAPP_NUMBER
)

for secret in "${SECRETS[@]}"; do
  if gcloud secrets describe "$secret" --format="value(name)" 2>/dev/null; then
    echo "  Secret '$secret' already exists."
  else
    echo ""
    read -rsp "  Enter value for $secret (leave empty to skip): " value
    echo ""
    if [[ -n "$value" ]]; then
      echo -n "$value" | gcloud secrets create "$secret" \
        --data-file=- \
        --replication-policy=automatic
      echo "  Created secret '$secret'."
    else
      echo "  Skipped '$secret' — create it later with:"
      echo "    echo -n 'VALUE' | gcloud secrets create $secret --data-file=- --replication-policy=automatic"
    fi
  fi
done

# ── Step 4: Create service account ─────────────────────────────

echo ""
echo "4/7 Creating service account for GitHub Actions..."
if gcloud iam service-accounts describe "$SERVICE_ACCOUNT" 2>/dev/null; then
  echo "  Service account already exists."
else
  gcloud iam service-accounts create "$SERVICE_ACCOUNT_NAME" \
    --display-name="Kira GitHub Actions Deploy"
fi

# ── Step 5: Grant IAM roles to service account ─────────────────

echo "5/7 Granting IAM roles..."

ROLES=(
  roles/run.admin
  roles/artifactregistry.writer
  roles/secretmanager.secretAccessor
  roles/iam.serviceAccountUser
)

for role in "${ROLES[@]}"; do
  gcloud projects add-iam-policy-binding "$PROJECT_ID" \
    --member="serviceAccount:$SERVICE_ACCOUNT" \
    --role="$role" \
    --quiet \
    --condition=None > /dev/null
  echo "  Granted $role"
done

# ── Step 6: Set up Workload Identity Federation ────────────────

echo "6/7 Setting up Workload Identity Federation..."

# Create the pool
if gcloud iam workload-identity-pools describe "$WIF_POOL" \
    --location=global --format="value(name)" 2>/dev/null; then
  echo "  Pool '$WIF_POOL' already exists."
else
  gcloud iam workload-identity-pools create "$WIF_POOL" \
    --location=global \
    --display-name="GitHub Actions"
fi

# Create the provider
POOL_ID=$(gcloud iam workload-identity-pools describe "$WIF_POOL" \
  --location=global --format="value(name)")

if gcloud iam workload-identity-pools providers describe "$WIF_PROVIDER" \
    --workload-identity-pool="$WIF_POOL" \
    --location=global --format="value(name)" 2>/dev/null; then
  echo "  Provider '$WIF_PROVIDER' already exists."
else
  gcloud iam workload-identity-pools providers create-oidc "$WIF_PROVIDER" \
    --workload-identity-pool="$WIF_POOL" \
    --location=global \
    --issuer-uri="https://token.actions.githubusercontent.com" \
    --attribute-mapping="google.subject=assertion.sub,attribute.repository=assertion.repository" \
    --attribute-condition="assertion.repository=='${GITHUB_REPO}'"
fi

# Allow the GitHub repo to impersonate the service account
gcloud iam service-accounts add-iam-policy-binding "$SERVICE_ACCOUNT" \
  --role="roles/iam.workloadIdentityUser" \
  --member="principalSet://iam.googleapis.com/${POOL_ID}/attribute.repository/${GITHUB_REPO}" \
  --quiet > /dev/null

WIF_PROVIDER_FULL=$(gcloud iam workload-identity-pools providers describe "$WIF_PROVIDER" \
  --workload-identity-pool="$WIF_POOL" \
  --location=global \
  --format="value(name)")

# ── Step 7: Create GCS bucket for memory.json ──────────────────

echo "7/7 Creating GCS bucket for memory persistence..."
BUCKET_NAME="${PROJECT_ID}-kira-memory"
if gsutil ls -b "gs://$BUCKET_NAME" 2>/dev/null; then
  echo "  Bucket '$BUCKET_NAME' already exists."
else
  gsutil mb -l "$REGION" "gs://$BUCKET_NAME"
  echo "  Created bucket '$BUCKET_NAME'."

  # Update the GCS_BUCKET_NAME secret
  echo -n "$BUCKET_NAME" | gcloud secrets versions add GCS_BUCKET_NAME --data-file=-
  echo "  Updated GCS_BUCKET_NAME secret."
fi

# ── Done ────────────────────────────────────────────────────────

echo ""
echo "============================================"
echo "  GCP setup complete!"
echo "============================================"
echo ""
echo "Now configure these GitHub repository variables"
echo "(Settings → Secrets and variables → Actions → Variables tab):"
echo ""
echo "  GCP_PROJECT_ID        = $PROJECT_ID"
echo "  GCP_REGION            = $REGION"
echo "  WIF_PROVIDER          = $WIF_PROVIDER_FULL"
echo "  WIF_SERVICE_ACCOUNT   = $SERVICE_ACCOUNT"
echo ""
echo "No GitHub Secrets needed — all secrets are in GCP Secret Manager."
echo ""
echo "After configuring, push to main and the deploy workflow will run."
echo ""
