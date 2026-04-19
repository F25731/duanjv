# KDocs Quark Link Extractor

This project uses Python + Playwright to:

1. Open a KDocs document with a persistent browser profile.
2. Reuse a previously scanned WeChat login session.
3. Use the document-level `查找` panel to search by keyword.
4. Collect all matched rows from the find result list.
5. Read the matched records through the page's live `WPSOpenApi` session.
6. Extract all Quark links and export them.

## Files

- `main.py`: entry point with `login` and `extract` commands
- `config.example.json`: example config, copy to `config.json`
- `keywords.txt`: keyword list, one per line

## Setup

1. Create a virtual environment if you want:

   ```powershell
   python -m venv .venv
   .\.venv\Scripts\Activate.ps1
   ```

2. Install dependencies:

   ```powershell
   pip install -r requirements.txt
   python -m playwright install chromium
   ```

   This project is pinned to Playwright `1.48.x` because the current machine uses Python 3.8, Playwright dropped Python 3.8 support starting in `1.52`, and the currently reachable package index on this machine only provides versions up to `1.48.0`.

3. Copy the config template:

   ```powershell
   Copy-Item config.example.json config.json
   ```

4. Edit `config.json` and `keywords.txt`.

## First Login

Run:

```powershell
python main.py login --config config.json
```

The script will open a dedicated Chromium profile under `browser_profile/`.
Use WeChat scan login manually, wait until the document can be opened normally, then return to the terminal and press Enter.

Important:

- The browser profile contains your login state. Do not share it.
- This project uses a separate automation profile. Do not point it to your normal Chrome profile.

## Extract Links

Run:

```powershell
python main.py extract --config config.json
```

The script will write:

- `output/results.json`
- `output/results.csv`
- debug screenshots and html snapshots when a step fails

## Remote Login In Docker

If you deploy this project to a Linux server with Docker Compose, the container can expose a temporary noVNC desktop on port `6080`.

After the container starts, open:

```text
http://your-server-ip:6080/vnc.html?autoconnect=1&resize=scale
```

Then run inside the container:

```bash
docker compose exec duanjv python main.py login --config config.json
```

The Playwright browser window will appear in the noVNC page, and you can complete the WeChat scan login there.

## HTTP API

When the Docker container is running, the API is exposed on port `8000`.

Health check:

```bash
curl http://your-server-ip:8000/health
```

Extract a single keyword:

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

If you set `DUANJV_API_KEY`, add:

```bash
-H "x-api-key: your-api-key"
```

## If the page structure is different

KDocs pages are dynamic, so you may need to adjust the selectors in `config.json`.

Usually the first places to tweak are:

- `selectors.search_input_selectors`
- `selectors.close_button_names`

## Useful options

```powershell
python main.py extract --config config.json --keyword 关键字A --keyword 关键字B
python main.py extract --config config.json --headless
python main.py login --config config.json --headless
```

## Notes

- This script uses the document `查找` panel rather than the left sidebar `搜索`.
- The table itself is canvas-rendered, so the extractor reads records through `WPSOpenApi`, which is much more stable than trying to click canvas cells.
- In Docker, Chromium runs with `--no-sandbox` when `PW_CHROMIUM_NO_SANDBOX=1` is set by Compose.
- If a run fails, check the files in `output/` first.
