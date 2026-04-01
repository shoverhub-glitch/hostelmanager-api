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

# Confirmation prompt
read -p "This will: stop/remove containers, volumes, images from docker-compose, and DROP database '$DB_NAME'. Continue? (yes/no): " CONFIRM

if [ "$CONFIRM" != "yes" ]; then
    echo "Aborted."
    exit 0
fi

echo ""
echo "=== Step 1: Stopping and removing containers & volumes ==="
docker-compose --env-file "$ENV_FILE" -f "$SCRIPT_DIR/docker-compose.yml" down -v 2>/dev/null || true

echo ""
echo "=== Step 2: Removing project images ==="
IMAGES=$(docker images --filter "reference=hostel*" --format "{{.Repository}}:{{.Tag}}" 2>/dev/null || true)
if [ -n "$IMAGES" ]; then
    echo "Removing images: $IMAGES"
    docker rmi -f $IMAGES 2>/dev/null || true
fi

echo ""
echo "=== Step 3: Dropping database ==="
echo "Using Docker to connect to central MongoDB and drop database..."
docker run --rm mongo:3.9 mongosh "$MONGO_URL" --eval "db.getSiblingDB('$DB_NAME').dropDatabase()" || \
docker run --rm mongo:7.0 mongosh "$MONGO_URL" --eval "db.getSiblingDB('$DB_NAME').dropDatabase()"

echo ""
echo "============================================"
echo "   PRODUCTION CLEAR COMPLETE"
echo "============================================"
echo ""
echo "Containers: Stopped and removed"
echo "Volumes: Removed"
echo "Images: Removed (project only)"
echo "Database: $DB_NAME - DROPPED"
echo ""
