# API Deployment

All deployment-related files for the API (Docker, Compose, Nginx, scripts) are now inside the `api/` directory. Use the `deploy/` folder for Docker Compose and scripts, and `nginx/` for Nginx config. This structure is ready for splitting into a standalone API repo. Local development Compose files (like docker-compose.dev.yml) are not included.
