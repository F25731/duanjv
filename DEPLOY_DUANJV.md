# duanjv Docker Deployment

This project is deployed as a Docker Compose service named `duanjv`.

## What this deployment does

- Builds a dedicated Python + Playwright image.
- Starts a long-running container with a virtual desktop.
- Persists the browser profile, exported state, and extraction results on the host.
- Exposes a temporary noVNC web desktop on port `6080` for first-time login.

## Port usage

- `6080`: noVNC desktop used for manual scan login
- No HTTP API port is exposed yet

At the current stage, `duanjv` is still a containerized CLI worker, not an HTTP service.

That means:

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

## View output

- `output/results.json`
- `output/results.csv`

## First-time login note

The current project uses WeChat scan login and stores the session in `browser_profile/`.

The current Compose setup now includes a remote desktop path through noVNC, so you can complete the first scan login inside the Linux container runtime and keep the session on disk.
