#!/usr/bin/env bash
#
# Idempotent Azure provisioning for the Healthcare No-Show API.
#
# Creates (or reuses, if they already exist): resource group, Azure Container
# Registry (ACR), Azure Database for PostgreSQL (Flexible Server), an App Service
# plan + Web App for Containers, and the GitHub OIDC federated identity + role
# assignments used by the deploy pipeline (.github/workflows/deploy.yml).
#
# It is safe to re-run: every step checks for the resource first, resource names
# are deterministic (derived from the subscription + resource group), and the
# generated PostgreSQL password is cached locally so DATABASE_URL stays stable.
#
# Requirements: Azure CLI (`az login` done) with permission to create resources
# and app registrations. Optionally GitHub CLI (`gh auth login`) if you pass
# --push-github to upload the secrets/variables for you.
#
# Usage:
#   bash infra/azure-provision.sh                 # provision + print creds
#   bash infra/azure-provision.sh --push-github   # also set GitHub secrets/vars
#
# Override any default by exporting the matching env var before running, e.g.:
#   LOCATION=westeurope ACR_NAME=myacr bash infra/azure-provision.sh
set -euo pipefail

# On Git Bash / MSYS2 (Windows), arguments that look like Unix paths (e.g. the
# "/subscriptions/.../resourceGroups/..." role-assignment scopes) get rewritten
# to Windows paths before reaching `az`, which then fails with "MissingSubscription".
# Disable that path conversion. Harmless/no-op on Linux and Azure Cloud Shell.
export MSYS_NO_PATHCONV=1
export MSYS2_ARG_CONV_EXCL='*'

# ----------------------------- configuration -------------------------------
LOCATION="${LOCATION:-eastus}"
# PostgreSQL Flexible Server is region-restricted on some subscriptions (notably
# "Azure for Students", where eastus/eastus2 return "location is restricted").
# Default it to a region that accepts the Burstable tier; override if needed.
PG_LOCATION="${PG_LOCATION:-canadacentral}"
RESOURCE_GROUP="${RESOURCE_GROUP:-noshow-rg}"
APP_PLAN="${APP_PLAN:-noshow-plan}"
PG_ADMIN="${PG_ADMIN:-noshowadmin}"
PG_DB="${PG_DB:-noshow}"
IMAGE_NAME="${IMAGE_NAME:-healthcare-noshow}"
AAD_APP_NAME="${AAD_APP_NAME:-noshow-github-oidc}"

PUSH_GITHUB=false
for arg in "$@"; do
  case "$arg" in
    --push-github) PUSH_GITHUB=true ;;
    -h|--help) grep '^#' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "Unknown argument: $arg" >&2; exit 2 ;;
  esac
done

# ----------------------------- prerequisites -------------------------------
command -v az >/dev/null || { echo "Azure CLI (az) is required." >&2; exit 1; }
az account show >/dev/null 2>&1 || { echo "Run 'az login' first." >&2; exit 1; }

SUBSCRIPTION_ID="$(az account show --query id -o tsv)"
TENANT_ID="$(az account show --query tenantId -o tsv)"

# Deterministic, globally-unique-ish suffix so re-runs target the same resources.
SUFFIX="$(printf '%s' "${SUBSCRIPTION_ID}-${RESOURCE_GROUP}" | sha1sum | cut -c1-8)"
ACR_NAME="${ACR_NAME:-noshowacr${SUFFIX}}"           # alphanumeric, <=50 chars
WEBAPP_NAME="${WEBAPP_NAME:-noshow-api-${SUFFIX}}"   # globally unique
PG_SERVER="${PG_SERVER:-noshow-pg-${SUFFIX}}"        # globally unique

# Auto-detect the GitHub repo ("owner/repo") from the git remote unless set.
detect_repo() {
  local url
  url="$(git -C "$(dirname "$0")/.." remote get-url origin 2>/dev/null || true)"
  # Handles git@github.com:owner/repo.git and https://github.com/owner/repo.git
  url="${url%.git}"
  url="${url#*github.com[:/]}"
  printf '%s' "$url"
}
GITHUB_REPO="${GITHUB_REPO:-$(detect_repo)}"
if [[ -z "$GITHUB_REPO" || "$GITHUB_REPO" != */* ]]; then
  echo "Could not determine GitHub repo. Set GITHUB_REPO=owner/repo and re-run." >&2
  exit 1
fi

# Cache the generated PostgreSQL password so DATABASE_URL is stable across runs.
PW_FILE="$(dirname "$0")/.pg-password"
if [[ -f "$PW_FILE" ]]; then
  PG_PASSWORD="$(cat "$PW_FILE")"
else
  PG_PASSWORD="$(openssl rand -base64 18)"
  ( umask 077; printf '%s' "$PG_PASSWORD" > "$PW_FILE" )
fi

echo "Subscription : $SUBSCRIPTION_ID"
echo "GitHub repo  : $GITHUB_REPO"
echo "Resource grp : $RESOURCE_GROUP ($LOCATION)"
echo

# ------------------------------- resources ---------------------------------
echo "==> Resource group"
az group create -n "$RESOURCE_GROUP" -l "$LOCATION" -o none

echo "==> Container registry: $ACR_NAME"
if ! az acr show -n "$ACR_NAME" -o none 2>/dev/null; then
  az acr create -g "$RESOURCE_GROUP" -n "$ACR_NAME" --sku Basic -o none
fi
ACR_LOGIN_SERVER="$(az acr show -n "$ACR_NAME" --query loginServer -o tsv)"
ACR_ID="$(az acr show -n "$ACR_NAME" --query id -o tsv)"

echo "==> PostgreSQL flexible server: $PG_SERVER"
if ! az postgres flexible-server show -g "$RESOURCE_GROUP" -n "$PG_SERVER" -o none 2>/dev/null; then
  az postgres flexible-server create \
    -g "$RESOURCE_GROUP" -n "$PG_SERVER" -l "$PG_LOCATION" \
    --admin-user "$PG_ADMIN" --admin-password "$PG_PASSWORD" \
    --tier Burstable --sku-name Standard_B1ms --version 16 \
    --storage-size 32 --public-access 0.0.0.0 --yes -o none
else
  # Ensure the admin password matches our cached value (e.g. first run failed late).
  az postgres flexible-server update -g "$RESOURCE_GROUP" -n "$PG_SERVER" \
    --admin-password "$PG_PASSWORD" -o none
fi
az postgres flexible-server db create \
  -g "$RESOURCE_GROUP" -s "$PG_SERVER" -d "$PG_DB" -o none 2>/dev/null || true
PG_HOST="$(az postgres flexible-server show -g "$RESOURCE_GROUP" -n "$PG_SERVER" --query fullyQualifiedDomainName -o tsv)"
DATABASE_URL="postgresql+psycopg2://${PG_ADMIN}:${PG_PASSWORD}@${PG_HOST}:5432/${PG_DB}?sslmode=require"

echo "==> App Service plan + Web App for Containers"
if ! az appservice plan show -g "$RESOURCE_GROUP" -n "$APP_PLAN" -o none 2>/dev/null; then
  az appservice plan create -g "$RESOURCE_GROUP" -n "$APP_PLAN" --is-linux --sku B1 -o none
fi
if ! az webapp show -g "$RESOURCE_GROUP" -n "$WEBAPP_NAME" -o none 2>/dev/null; then
  # Start from a public placeholder; the pipeline swaps in the real image.
  az webapp create -g "$RESOURCE_GROUP" -p "$APP_PLAN" -n "$WEBAPP_NAME" \
    --deployment-container-image-name "mcr.microsoft.com/azuredocs/aci-helloworld" -o none
fi

echo "==> Web App settings (port + database)"
az webapp config appsettings set -g "$RESOURCE_GROUP" -n "$WEBAPP_NAME" --settings \
  WEBSITES_PORT=8000 \
  ENVIRONMENT=production \
  LOG_LEVEL=INFO \
  ANALYSIS_DEFAULT_YEAR=2024 \
  DATABASE_URL="$DATABASE_URL" -o none

echo "==> Let the Web App pull from ACR via its managed identity"
az webapp identity assign -g "$RESOURCE_GROUP" -n "$WEBAPP_NAME" -o none
WEBAPP_PRINCIPAL_ID="$(az webapp identity show -g "$RESOURCE_GROUP" -n "$WEBAPP_NAME" --query principalId -o tsv)"
az role assignment create --assignee-object-id "$WEBAPP_PRINCIPAL_ID" \
  --assignee-principal-type ServicePrincipal \
  --role AcrPull --scope "$ACR_ID" -o none 2>/dev/null || true
az webapp config set -g "$RESOURCE_GROUP" -n "$WEBAPP_NAME" \
  --generic-configurations '{"acrUseManagedIdentityCreds": true}' -o none

# ----------------------- GitHub OIDC federated identity --------------------
echo "==> GitHub OIDC identity for the deploy pipeline"
APP_ID="$(az ad app list --display-name "$AAD_APP_NAME" --query "[0].appId" -o tsv)"
if [[ -z "$APP_ID" ]]; then
  APP_ID="$(az ad app create --display-name "$AAD_APP_NAME" --query appId -o tsv)"
fi
# Ensure a service principal exists for the app.
az ad sp show --id "$APP_ID" -o none 2>/dev/null || az ad sp create --id "$APP_ID" -o none

# Federated credentials. The build job runs with no environment (subject =
# ...:ref:refs/heads/main); the deploy job runs in environment "production"
# (subject = ...:environment:production). BOTH are required or azure/login
# fails in whichever job lacks a matching credential.
ensure_fic() {
  local name="$1" subject="$2"
  if ! az ad app federated-credential list --id "$APP_ID" \
        --query "[?subject=='${subject}'] | [0].id" -o tsv | grep -q .; then
    az ad app federated-credential create --id "$APP_ID" --parameters "{
      \"name\": \"${name}\",
      \"issuer\": \"https://token.actions.githubusercontent.com\",
      \"subject\": \"${subject}\",
      \"audiences\": [\"api://AzureADTokenExchange\"]
    }" -o none
  fi
}
ensure_fic "github-main"        "repo:${GITHUB_REPO}:ref:refs/heads/main"
ensure_fic "github-env-prod"    "repo:${GITHUB_REPO}:environment:production"

# Allow the federated identity to deploy to the web app and push to ACR.
az role assignment create --assignee "$APP_ID" --role Contributor \
  --scope "/subscriptions/${SUBSCRIPTION_ID}/resourceGroups/${RESOURCE_GROUP}" -o none 2>/dev/null || true
az role assignment create --assignee "$APP_ID" --role AcrPush \
  --scope "$ACR_ID" -o none 2>/dev/null || true

# ------------------------- optional: push to GitHub ------------------------
if [[ "$PUSH_GITHUB" == "true" ]]; then
  if command -v gh >/dev/null && gh auth status >/dev/null 2>&1; then
    echo "==> Pushing secrets and variables to GitHub ($GITHUB_REPO)"
    gh secret   set AZURE_CLIENT_ID       -R "$GITHUB_REPO" -b "$APP_ID"
    gh secret   set AZURE_TENANT_ID       -R "$GITHUB_REPO" -b "$TENANT_ID"
    gh secret   set AZURE_SUBSCRIPTION_ID -R "$GITHUB_REPO" -b "$SUBSCRIPTION_ID"
    gh variable set ACR_NAME              -R "$GITHUB_REPO" -b "$ACR_NAME"
    gh variable set ACR_LOGIN_SERVER      -R "$GITHUB_REPO" -b "$ACR_LOGIN_SERVER"
    gh variable set AZURE_WEBAPP_NAME     -R "$GITHUB_REPO" -b "$WEBAPP_NAME"
    echo "    done."
  else
    echo "!! --push-github was set but gh CLI is unavailable or not authenticated."
    echo "   Run 'gh auth login' and re-run, or copy the values below manually."
  fi
fi

# --------------------------------- output ----------------------------------
cat <<EOF

=====================================================================
Provisioning complete. Pipeline credentials for GitHub
(Settings -> Secrets and variables -> Actions):

  SECRETS (encrypted — add under "Secrets"):
    AZURE_CLIENT_ID         = ${APP_ID}
    AZURE_TENANT_ID         = ${TENANT_ID}
    AZURE_SUBSCRIPTION_ID   = ${SUBSCRIPTION_ID}

  VARIABLES (plain — add under "Variables"):
    ACR_NAME                = ${ACR_NAME}
    ACR_LOGIN_SERVER        = ${ACR_LOGIN_SERVER}
    AZURE_WEBAPP_NAME       = ${WEBAPP_NAME}

Copy/paste to set them with the GitHub CLI:
    gh secret   set AZURE_CLIENT_ID       -R ${GITHUB_REPO} -b "${APP_ID}"
    gh secret   set AZURE_TENANT_ID       -R ${GITHUB_REPO} -b "${TENANT_ID}"
    gh secret   set AZURE_SUBSCRIPTION_ID -R ${GITHUB_REPO} -b "${SUBSCRIPTION_ID}"
    gh variable set ACR_NAME              -R ${GITHUB_REPO} -b "${ACR_NAME}"
    gh variable set ACR_LOGIN_SERVER      -R ${GITHUB_REPO} -b "${ACR_LOGIN_SERVER}"
    gh variable set AZURE_WEBAPP_NAME     -R ${GITHUB_REPO} -b "${WEBAPP_NAME}"
  (or just re-run this script with --push-github)

Database (already set on the Web App; NOT needed in GitHub):
    DATABASE_URL            = ${DATABASE_URL}
    (PostgreSQL password cached at infra/.pg-password — keep it out of git)

App URL: https://${WEBAPP_NAME}.azurewebsites.net
Push to main to trigger test -> build -> deploy.
=====================================================================
EOF
