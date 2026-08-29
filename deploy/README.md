# Deploying raghealth-server

The server stores only findings metadata (kilobytes per snapshot) in
SQLite — any small instance is enough.

## Docker Compose (a VPS, 2 minutes)

```bash
git clone GITHUB_URL && cd raghealth/deploy
docker compose up -d
docker compose exec server python -m raghealth_server create-workspace acme \
  --slack-webhook https://hooks.slack.com/services/...
# prints the API key for your agent and the /d/<token> dashboard path
```

Put Caddy or nginx with TLS in front for production, and set
`RAGHEALTH_BASE_URL=https://your-domain` so alert links resolve.

## Fly.io (free-tier friendly)

```bash
fly launch --dockerfile deploy/Dockerfile --name my-raghealth --no-deploy
fly volumes create raghealth_data --size 1
# add to fly.toml:  [mounts]  source="raghealth_data"  destination="/data"
fly deploy
fly ssh console -C "python -m raghealth_server create-workspace acme"
```

## Render

New Web Service → your repo → Docker → dockerfile path `deploy/Dockerfile`
→ add a 1 GB disk mounted at `/data`. Create the workspace from the
service Shell tab with the same `create-workspace` command.

## Backups

Everything lives in one SQLite file at `/data/raghealth_server.db` — copy
that file and you've backed up the server.
