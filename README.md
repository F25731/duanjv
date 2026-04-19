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
- If a run fails, check the files in `output/` first.
