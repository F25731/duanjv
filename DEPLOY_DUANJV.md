# duanjv Docker Deployment

This project is deployed as a Docker Compose service named `duanjv`.

## What this deployment does

- Builds a dedicated Python + Playwright image.
- Starts a long-running container with a virtual desktop.
- Persists the browser profile, exported state, and extraction results on the host.
- Exposes a temporary noVNC web desktop on port `6080` for first-time login.

## Port usage

- `6080`: noVNC desktop used for manual scan login
- `8000`: HTTP API endpoint

At the current stage, `duanjv` exposes both:

- a CLI entrypoint you can still run with `docker compose exec`
- an HTTP API that can be called by IP and port

That means:

- You run extraction commands with `docker compose exec`.
- You can also call the extractor directly over `http://server-ip:8000`.

## Files used by the container

- `config.json`: runtime config, mounted into the container
- `keywords.txt`: runtime keyword list, mounted into the container
- `.env`: optional Compose environment file for `DUANJV_API_KEY`
- `browser_profile/`: persistent Chromium login profile
- `output/`: extraction outputs
- `playwright_state.json`: exported Playwright auth state

## Build and start

```bash
docker compose build duanjv
docker compose up -d duanjv
```

## First-time login

Open the remote desktop in your browser:

```text
http://your-server-ip:6080/vnc.html?autoconnect=1&resize=scale
```

Then run:

```bash
docker compose exec duanjv python main.py login --config config.json
```

The browser window will appear in the noVNC desktop. Complete the WeChat scan there, then press Enter in the terminal where the login command is running.

## Run extraction

Single keyword:

```bash
docker compose exec duanjv python main.py extract --config config.json --keyword 夫君
```

Batch from `keywords.txt`:

```bash
docker compose exec duanjv python main.py extract --config config.json
```

## Call the HTTP API

Health check:

```bash
curl http://your-server-ip:8000/health
```

Extract one keyword:

```bash
curl -X POST "http://your-server-ip:8000/extract" \
  -H "Content-Type: application/json" \
  -d '{"keyword":"夫君"}'
```

Extract multiple keywords:

```bash
curl -X POST "http://your-server-ip:8000/extract" \
  -H "Content-Type: application/json" \
  -d '{"keywords":["夫君","少主"]}'
```

If you set `DUANJV_API_KEY` in Compose, add:

```bash
-H "x-api-key: your-api-key"
```

## View output

- `output/results.json`
- `output/results.csv`

## First-time login note

The current project uses WeChat scan login and stores the session in `browser_profile/`.

The current Compose setup now includes a remote desktop path through noVNC, so you can complete the first scan login inside the Linux container runtime and keep the session on disk.
