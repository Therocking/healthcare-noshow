#!/usr/bin/env bash
# Apply database migrations, then hand off to the container CMD (the API server).
set -euo pipefail

echo "Running database migrations (alembic upgrade head)..."
alembic upgrade head

echo "Starting application: $*"
exec "$@"
