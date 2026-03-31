docker-compose -f ./deploy/docker-compose.yml down -v --rmi all --remove-orphans
#!/bin/bash
# Clear production Docker containers, images, and volumes for API
# Usage: ./clear_production_docker.sh

DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
docker-compose --env-file "$DIR/../.env" -f "$DIR/docker-compose.yml" down -v --rmi all --remove-orphans
