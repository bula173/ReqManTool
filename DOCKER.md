# ReqManTool Server Edition - Docker Deployment

This guide covers deploying the ReqManTool server edition using Docker.

## Quick Start

### 1. Prepare Configuration

```bash
# Copy example files
cp config.json.example config.json
cp server.env.example server.env

# Edit config.json with your settings
# Edit server.env with your secrets
```

### 2. Run with Docker Compose

```bash
# Start the server
docker compose up -d

# Check logs
docker compose logs -f

# Stop the server
docker compose down
```

### 3. Access the Application

Open http://localhost:8899 in your browser.

## Production Deployment

### Configuration

Edit `config.json` with your production settings:

```json
{
  "format_version": 1,
  "data_dir": "/data",
  "mode": "server",
  "host": "0.0.0.0",
  "port": 8899,
  "cors_origins": ["https://reqman.example.com"],
  "server": {
    "base_url": "https://reqman.example.com",
    "cookie_secure": true,
    "admins": ["admin@example.com"],
    "auth_providers": [
      {
        "id": "github",
        "type": "github",
        "name": "GitHub",
        "url": "https://github.com",
        "client_id": "your-github-client-id"
      },
      {
        "id": "gitlab",
        "type": "gitlab",
        "name": "GitLab",
        "url": "https://gitlab.example.com",
        "client_id": "your-gitlab-application-id",
        "git_hosts": ["ssh.gitlab.example.com"]
      }
    ],
    "discovery": {
      "enabled": true,
      "cache_minutes": 15,
      "max_repos": 200
    }
  }
}
```

Edit `server.env` with your secrets (never commit this file):

```bash
REQMAN_AUTH_GITHUB_CLIENT_SECRET=your-github-client-secret
REQMAN_AUTH_GITLAB_CLIENT_SECRET=your-gitlab-client-secret
REQMAN_AUTH_OIDC_CLIENT_SECRET=your-oidc-client-secret
```

### Reverse Proxy with TLS (Optional)

```bash
# Place certificates in ./certs/
# fullchain.pem
# privkey.pem

# Start with proxy profile
docker compose --profile proxy up -d
```

### Kubernetes (Helm)

```bash
# Add Helm repo (when published)
# helm repo add reqmantool https://bula173.github.io/ReqManTool/helm

# Install
helm install reqman ./helm/reqmantool \
  --set config.server.base_url=https://reqman.example.com \
  --set config.server.admins[0]=admin@example.com \
  --set config.server.auth_providers[0].id=github \
  --set config.server.auth_providers[0].type=github \
  --set config.server.auth_providers[0].client_id=your-client-id \
  --set secrets.github_client_secret=your-client-secret
```

## One-Click Deploy

### Railway

[![Deploy on Railway](https://railway.app/button.svg)](https://railway.app/new/template?template=https://github.com/bula173/ReqManTool/tree/main/deploy/railway)

Or:
```bash
railway up --config deploy/railway/railway.toml
```

### Render

[![Deploy to Render](https://render.com/images/deploy-to-render-button.svg)](https://render.com/deploy?repo=https://github.com/bula173/ReqManTool)

### Azure Container Apps

```bash
# Requires Azure CLI and Key Vault setup
az containerapp up --config-file deploy/azure/containerapp.yaml
```

## Building the Image

### Local Build

```bash
# Single arch
docker build -t reqmantool/server:local .

# Multi-arch (requires buildx)
docker buildx build --platform linux/amd64,linux/arm64 -t reqmantool/server:local --load .
```

### GitHub Actions (Automated)

The image is automatically built and published to `ghcr.io/bula173/reqmantool/server` on:
- Tag pushes (v*)
- Main branch pushes
- Manual workflow dispatch

## Data Persistence

The Docker image uses a volume at `/data` for persistent storage:

- Project repositories (git)
- Requirements data
- Baselines and history

### Backup

```bash
# Backup data volume
docker run --rm -v reqman-data:/data -v $(pwd):/backup alpine tar czf /backup/reqman-backup-$(date +%Y%m%d).tar.gz -C /data .
```

### Restore

```bash
# Restore data volume
docker run --rm -v reqman-data:/data -v $(pwd):/backup alpine tar xzf /backup/reqman-backup-YYYYMMDD.tar.gz -C /data
```

## Upgrade Notes

1. **Always backup** before upgrading
2. Pull the new image: `docker compose pull`
3. Restart: `docker compose up -d`
4. Data format migrations run automatically on startup
5. Check release notes for any breaking changes

## Environment Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `REQMAN_DATA_DIR` | Data directory path | `/data` |
| `REQMAN_HOST` | Bind address | `0.0.0.0` |
| `REQMAN_PORT` | Port number | `8899` |
| `REQMAN_MODE` | Edition mode | `server` |
| `REQMAN_CONFIG_FILE` | Config file path | `/config.json` |
| `REQMAN_AUTH_*_CLIENT_SECRET` | OAuth client secrets | (required) |
| `REQMAN_LICENCE_FILE` | Licence file path | (optional) |

## Healthcheck

The container includes a healthcheck that hits `/edition` endpoint:

```bash
# Check health status
docker inspect --format='{{.State.Health.Status}}' reqman-server
```

## Troubleshooting

### Server won't start

1. Check logs: `docker compose logs reqman`
2. Verify config.json syntax
3. Ensure at least one auth provider is configured
4. Check that secrets are set in server.env

### Permission errors

The container runs as user `1000:1000`. Ensure the data volume is owned by this user:

```bash
docker run --rm -v reqman-data:/data alpine chown -R 1000:1000 /data
```

### Database migrations

Data format migrations run automatically. If you encounter issues:

```bash
# Force migration by restarting
docker compose restart reqman
```

## Security Considerations

- The container runs as non-root user (UID 1000)
- Secrets are injected via environment files (not baked into image)
- TLS termination should be handled by reverse proxy
- Keep the base image updated for security patches
- Use `docker scan` to check for vulnerabilities

## License

AGPL-3.0-only. See LICENSE file for details.