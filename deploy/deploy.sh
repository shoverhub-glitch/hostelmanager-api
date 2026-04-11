#!/bin/bash
# Deploy API using Docker Compose and create admin if configured
# Usage: ./deploy.sh

set -e

SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
ENV_FILE="$SCRIPT_DIR/../.env"

echo "============================================"
echo "   DEPLOYMENT SCRIPT"
echo "============================================"
echo ""

# Check if .env exists
if [ ! -f "$ENV_FILE" ]; then
    echo "Error: .env file not found at $ENV_FILE"
    exit 1
fi

# Load environment variables
source "$ENV_FILE"

# Step 1: Start containers
echo "=== Step 1: Starting containers ==="
docker compose --env-file "$ENV_FILE" -f "$SCRIPT_DIR/docker-compose.yml" up -d --build

# Step 2: Wait for container to be healthy
echo ""
echo "=== Step 2: Waiting for container to be healthy ==="
MAX_WAIT=60
COUNTER=0
while [ $COUNTER -lt $MAX_WAIT ]; do
    CONTAINER_STATUS=$(docker inspect --format='{{.State.Health.Status}}' hostel-api 2>/dev/null || echo "none")
    if [ "$CONTAINER_STATUS" = "healthy" ]; then
        echo "Container is healthy!"
        break
    fi
    echo "Waiting for container... ($COUNTER/$MAX_WAIT)"
    sleep 2
    COUNTER=$((COUNTER + 2))
done

if [ $COUNTER -ge $MAX_WAIT ]; then
    echo "Warning: Container did not become healthy within $MAX_WAIT seconds, continuing anyway..."
fi

# Step 3: Create admin if configured
echo ""
echo "=== Step 3: Creating admin user ==="
if [ -n "$ADMIN_BOOTSTRAP_EMAIL" ] && [ -n "$ADMIN_BOOTSTRAP_PASSWORD" ]; then
    echo "Admin credentials found in .env, creating admin user..."
    docker exec hostel-api python create_admin.py \
        --name "${ADMIN_BOOTSTRAP_NAME:-Admin}" \
        --email "$ADMIN_BOOTSTRAP_EMAIL" \
        --password "$ADMIN_BOOTSTRAP_PASSWORD" \
        --phone "${ADMIN_BOOTSTRAP_PHONE:-}" \
        --role "${ADMIN_BOOTSTRAP_ROLE:-admin}" \
        --grant-by "${ADMIN_BOOTSTRAP_GRANT_BY:-email}" \
        --skip-env-update
else
    echo "Admin credentials not configured (ADMIN_BOOTSTRAP_EMAIL and ADMIN_BOOTSTRAP_PASSWORD not set), skipping..."
fi

echo ""
echo "============================================"
echo "   DEPLOYMENT COMPLETE"
echo "============================================"
echo ""
