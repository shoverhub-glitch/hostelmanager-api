#!/bin/bash
# Clear production Docker containers, volumes, images, and database for API only
# Usage: ./clear_production_docker.sh

set -e

SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
ENV_FILE="$SCRIPT_DIR/../.env"

echo "============================================"
echo "   PRODUCTION CLEAR SCRIPT"
echo "============================================"
echo ""

# Check if .env exists
if [ ! -f "$ENV_FILE" ]; then
    echo "Error: .env file not found at $ENV_FILE"
    exit 1
fi

# Load environment variables
source "$ENV_FILE"

# Check if MONGO_URL is set
if [ -z "$MONGO_URL" ]; then
    echo "Error: MONGO_URL is not set in .env"
    exit 1
fi

# Extract database name from MONGO_URL
DB_NAME=$(echo "$MONGO_URL" | sed -n 's|.*/\([^?]*\).*|\1|p')

if [ -z "$DB_NAME" ]; then
    echo "Error: Could not extract database name from MONGO_URL: $MONGO_URL"
    exit 1
fi

echo "MongoDB URL: $MONGO_URL"
echo "Database Name: $DB_NAME"
echo ""

echo ""
echo "This will: stop/remove containers, volumes, images, and DROP database '$DB_NAME'"
echo ""

echo "=== Step 1: Dropping database ==="
docker exec hostel-api python /deploy/drop_database.py

echo ""
echo "=== Step 2: Stopping and removing containers & volumes ==="
docker compose --env-file "$ENV_FILE" -f "$SCRIPT_DIR/docker-compose.yml" down -v 2>/dev/null || true

echo ""
echo "=== Step 3: Removing project images ==="
IMAGES=$(docker images --filter "reference=hostel*" --format "{{.Repository}}:{{.Tag}}" 2>/dev/null || true)
if [ -n "$IMAGES" ]; then
    echo "Removing images: $IMAGES"
    docker rmi -f $IMAGES 2>/dev/null || true
fi

echo ""
echo "============================================"
echo "   PRODUCTION CLEAR COMPLETE"
echo "============================================"
echo ""
echo "Database: $DB_NAME - DROPPED"
echo "Containers: Stopped and removed"
echo "Volumes: Removed"
echo "Images: Removed (project only)"
echo ""
