#!/bin/bash
# Stop backend and drop MongoDB database after explicit confirmation.
# Usage: ./clear_db.sh

set -e

SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
ENV_FILE="$SCRIPT_DIR/../.env"

echo "============================================"
echo "   CLEAR DATABASE SCRIPT"
echo "============================================"
echo ""

if [ ! -f "$ENV_FILE" ]; then
    echo "Error: .env file not found at $ENV_FILE"
    exit 1
fi

# Load environment variables from .env
source "$ENV_FILE"

if [ -z "$MONGO_URL" ]; then
    echo "Error: MONGO_URL is not set in .env"
    exit 1
fi

# Extract db name from URL path (before query string)
DB_NAME=$(echo "$MONGO_URL" | sed -n 's|.*/\([^?]*\).*|\1|p')

if [ -z "$DB_NAME" ]; then
    echo "Error: Could not extract database name from MONGO_URL: $MONGO_URL"
    exit 1
fi

echo "=== Step 1: Stopping backend container ==="
docker compose --env-file "$ENV_FILE" -f "$SCRIPT_DIR/docker-compose.yml" down

echo ""
echo "=== Step 2: Confirmation required ==="
echo "MongoDB URL : $MONGO_URL"
echo "Database    : $DB_NAME"
echo ""
read -p "Drop this database now? Type 'yes' to continue (or 'no' to cancel): " CONFIRM

if [ "$CONFIRM" != "yes" ]; then
    echo "Cancelled. Database was not dropped."
    exit 0
fi

echo ""
echo "=== Step 3: Dropping database ==="
MONGO_URL="$MONGO_URL" MONGO_DB_NAME="$DB_NAME" docker compose --env-file "$ENV_FILE" -f "$SCRIPT_DIR/docker-compose.yml" run --rm hostel-api python /deploy/drop_database.py -f

echo ""
echo "============================================"
echo "   CLEAR DATABASE COMPLETE"
echo "============================================"
echo "Database dropped: $DB_NAME"
echo "Backend container is down."
echo ""