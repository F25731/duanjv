# duanjv Docker Deployment

This project is deployed as a Docker Compose service named `duanjv`.

## What this deployment does

- Builds a dedicated Python + Playwright image.
- Keeps one long-running container alive for manual commands.
- Persists the browser profile, exported state, and extraction results on the host.

## Important limitation

At the current stage, `duanjv` is a containerized CLI worker, not an HTTP service.

That means:

- No container port needs to be exposed yet.
- You run extraction commands with `docker compose exec`.
- If you later want AstrBot to call it directly, the next step is to wrap it as an HTTP API and then expose a port such as `8000`.

## Files used by the container

- `config.json`: runtime config, mounted into the container
- `keywords.txt`: runtime keyword list, mounted into the container
- `browser_profile/`: persistent Chromium login profile
- `output/`: extraction outputs
- `playwright_state.json`: exported Playwright auth state

## Build and start

```bash
docker compose build duanjv
docker compose up -d duanjv
```

## Run extraction

Single keyword:

```bash
docker compose exec duanjv python main.py extract --config config.json --keyword 夫君
```

Batch from `keywords.txt`:

```bash
docker compose exec duanjv python main.py extract --config config.json
```

## View output

- `output/results.json`
- `output/results.csv`

## First-time login note

The current project uses WeChat scan login and stores the session in `browser_profile/`.

If you deploy to a headless Linux server, you still need a way to complete the first scan login in that Linux runtime. The current Compose setup does not include a remote desktop or noVNC viewer yet.

So for server deployment, treat this as:

1. Build and run the container.
2. Prepare the runtime files and persisted directories.
3. Later add a visible login path for the first scan if needed.
