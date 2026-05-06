#!/bin/bash
# Deploy API using Docker Compose
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

echo "This will rebuild the backend image, restart the backend container, and ensure the test user exists."
read -p "Continue with deploy? Type 'yes' to continue (or 'no' to cancel): " CONFIRM

if [ "$CONFIRM" != "yes" ]; then
    echo "Cancelled. Deployment was not started."
    exit 0
fi

# Step 1: Build image (always)
echo "=== Step 1: Building backend image (no cache) ==="
docker compose --env-file "$ENV_FILE" -f "$SCRIPT_DIR/docker-compose.yml" build --no-cache

# Step 2: Start containers
echo ""
echo "=== Step 2: Starting containers ==="
docker compose --env-file "$ENV_FILE" -f "$SCRIPT_DIR/docker-compose.yml" up -d --force-recreate

# Step 3: Wait for container to be healthy
echo ""
echo "=== Step 3: Waiting for container to be healthy ==="
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

# Step 4: Create/update fixed test user
echo ""
echo "=== Step 4: Ensuring test user exists ==="
docker exec -i hostel-api python - <<'PY'
import asyncio
from datetime import datetime, timezone

from app.database.mongodb import db
from app.utils.helpers import hash_password


async def main() -> None:
    users = db["users"]
    now = datetime.now(timezone.utc)

    await users.update_one(
        {"email": "test@shoverhub.com"},
        {
            "$set": {
                "name": "Test User",
                "phone": "9123123123",
                "password": hash_password("Test@123"),
                "role": "propertyowner",
                "isEmailVerified": True,
                "updatedAt": now,
            },
            "$setOnInsert": {
                "createdAt": now,
                "lastLogin": None,
                "propertyIds": [],
            },
        },
        upsert=True,
    )

    print("Test user ready: test@shoverhub.com")


asyncio.run(main())
PY

echo ""
echo "============================================"
echo "   DEPLOYMENT COMPLETE"
echo "============================================"
echo ""
