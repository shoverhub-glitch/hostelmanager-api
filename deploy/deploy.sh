docker-compose -f ./deploy/docker-compose.yml up -d --build
#!/bin/bash
# Deploy API using Docker Compose
# Usage: ./deploy.sh

DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
docker-compose --env-file "$DIR/../.env" -f "$DIR/docker-compose.yml" up -d --build
