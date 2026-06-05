#!/usr/bin/env bash
#
# One-time Azure provisioning for the Healthcare No-Show API.
#
# Creates: resource group, Azure Container Registry (ACR), Azure Database for
# PostgreSQL (Flexible Server), an App Service plan + Web App for Containers, and
# the GitHub OIDC federated identity + role assignments used by the deploy
# pipeline (.github/workflows/deploy.yml).
#
# Requirements: Azure CLI (`az login` done), and permission to create resources
# and app registrations in the subscription/tenant.
#
# Usage: review the variables below, then:  bash infra/azure-provision.sh
set -euo pipefail

# ----------------------------- configure these -----------------------------
LOCATION="eastus"
RESOURCE_GROUP="noshow-rg"
ACR_NAME="noshowacr$RANDOM"                 # must be globally unique, alphanumeric
WEBAPP_NAME="noshow-api-$RANDOM"            # must be globally unique
APP_PLAN="noshow-plan"

PG_SERVER="noshow-pg-$RANDOM"               # must be globally unique
PG_ADMIN="noshowadmin"
PG_PASSWORD="$(openssl rand -base64 18)"    # generated; stored only in App settings
PG_DB="noshow"

# GitHub repo that runs the deploy workflow, as "owner/repo".
GITHUB_REPO="your-org/your-repo"
IMAGE_NAME="healthcare-noshow"
# ---------------------------------------------------------------------------

SUBSCRIPTION_ID="$(az account show --query id -o tsv)"
TENANT_ID="$(az account show --query tenantId -o tsv)"

echo "==> Resource group"
az group create -n "$RESOURCE_GROUP" -l "$LOCATION" -o none

echo "==> Container registry: $ACR_NAME"
az acr create -g "$RESOURCE_GROUP" -n "$ACR_NAME" --sku Basic -o none
ACR_LOGIN_SERVER="$(az acr show -n "$ACR_NAME" --query loginServer -o tsv)"

echo "==> PostgreSQL flexible server: $PG_SERVER"
az postgres flexible-server create \
  -g "$RESOURCE_GROUP" -n "$PG_SERVER" -l "$LOCATION" \
  --admin-user "$PG_ADMIN" --admin-password "$PG_PASSWORD" \
  --tier Burstable --sku-name Standard_B1ms --version 16 \
  --storage-size 32 --public-access 0.0.0.0 --yes -o none
az postgres flexible-server db create \
  -g "$RESOURCE_GROUP" -s "$PG_SERVER" -d "$PG_DB" -o none
PG_HOST="$(az postgres flexible-server show -g "$RESOURCE_GROUP" -n "$PG_SERVER" --query fullyQualifiedDomainName -o tsv)"
DATABASE_URL="postgresql+psycopg2://${PG_ADMIN}:${PG_PASSWORD}@${PG_HOST}:5432/${PG_DB}?sslmode=require"

echo "==> App Service plan + Web App for Containers"
az appservice plan create -g "$RESOURCE_GROUP" -n "$APP_PLAN" --is-linux --sku B1 -o none
# Start from a public placeholder; the pipeline swaps in the real image.
az webapp create -g "$RESOURCE_GROUP" -p "$APP_PLAN" -n "$WEBAPP_NAME" \
  --deployment-container-image-name "mcr.microsoft.com/azuredocs/aci-helloworld" -o none

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
ACR_ID="$(az acr show -n "$ACR_NAME" --query id -o tsv)"
az role assignment create --assignee "$WEBAPP_PRINCIPAL_ID" --role AcrPull --scope "$ACR_ID" -o none
az webapp config set -g "$RESOURCE_GROUP" -n "$WEBAPP_NAME" --generic-configurations '{"acrUseManagedIdentityCreds": true}' -o none

echo "==> GitHub OIDC identity for the deploy pipeline"
APP_ID="$(az ad app create --display-name "noshow-github-oidc" --query appId -o tsv)"
az ad sp create --id "$APP_ID" -o none
SP_OBJECT_ID="$(az ad sp show --id "$APP_ID" --query id -o tsv)"
az ad app federated-credential create --id "$APP_ID" --parameters "{
  \"name\": \"github-main\",
  \"issuer\": \"https://token.actions.githubusercontent.com\",
  \"subject\": \"repo:${GITHUB_REPO}:ref:refs/heads/main\",
  \"audiences\": [\"api://AzureADTokenExchange\"]
}" -o none
# Allow the federated identity to deploy to the web app and push to ACR.
az role assignment create --assignee "$APP_ID" --role Contributor \
  --scope "/subscriptions/${SUBSCRIPTION_ID}/resourceGroups/${RESOURCE_GROUP}" -o none
az role assignment create --assignee "$APP_ID" --role AcrPush --scope "$ACR_ID" -o none

cat <<EOF

=====================================================================
Provisioning complete. Configure these in GitHub
(Settings -> Secrets and variables -> Actions):

  Secrets:
    AZURE_CLIENT_ID         = ${APP_ID}
    AZURE_TENANT_ID         = ${TENANT_ID}
    AZURE_SUBSCRIPTION_ID   = ${SUBSCRIPTION_ID}

  Variables:
    ACR_NAME                = ${ACR_NAME}
    ACR_LOGIN_SERVER        = ${ACR_LOGIN_SERVER}
    AZURE_WEBAPP_NAME       = ${WEBAPP_NAME}

Database (already set on the Web App, shown for reference):
    DATABASE_URL            = ${DATABASE_URL}

App URL: https://${WEBAPP_NAME}.azurewebsites.net
Push to main to trigger test -> build -> deploy.
=====================================================================
EOF
