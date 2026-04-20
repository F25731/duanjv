import os
import threading
import time
from pathlib import Path
from typing import List, Optional
from uuid import uuid4

from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel, Field
from playwright.sync_api import sync_playwright

import main


APP_ROOT = Path(__file__).resolve().parent
CONFIG_PATH = Path(os.environ.get("DUANJV_CONFIG", APP_ROOT / "config.json")).resolve()
API_KEY = os.environ.get("DUANJV_API_KEY", "").strip()
EXTRACT_LOCK = threading.Lock()


def env_int(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


WORKER_MAX_REQUESTS = max(1, env_int("DUANJV_WORKER_MAX_REQUESTS", 20))
WORKER_MAX_IDLE_SECONDS = max(60, env_int("DUANJV_WORKER_MAX_IDLE_SECONDS", 900))

app = FastAPI(title="duanjv", version="1.2.0")


class ExtractRequest(BaseModel):
    keyword: Optional[str] = None
    keywords: List[str] = Field(default_factory=list)
    limit: Optional[int] = None
    headless: bool = True
    include_rows: bool = True


class BrowserWorker:
    def __init__(self) -> None:
        self._playwright = None
        self._context = None
        self._page = None
        self._headless: Optional[bool] = None
        self._request_count = 0
        self._created_at = 0.0
        self._last_used_at = 0.0

    def _close_runtime(self) -> None:
        if self._context is not None:
            try:
                self._context.close()
            except Exception:
                pass
        self._context = None
        self._page = None
        self._headless = None
        self._request_count = 0
        self._created_at = 0.0
        self._last_used_at = 0.0

        if self._playwright is not None:
            try:
                self._playwright.stop()
            except Exception:
                pass
        self._playwright = None

    def _start_runtime(self, config: dict, base_dir: Path, force_headless: bool) -> None:
        self._close_runtime()
        self._playwright = sync_playwright().start()
        self._context = main.launch_persistent_context(
            self._playwright,
            config=config,
            base_dir=base_dir,
            force_headless=force_headless,
        )
        self._page = self._context.pages[0] if self._context.pages else self._context.new_page()
        self._headless = bool(force_headless)
        self._request_count = 0
        self._created_at = time.time()
        self._last_used_at = self._created_at

    def _needs_restart(self, force_headless: bool) -> bool:
        if self._context is None or self._page is None or self._playwright is None:
            return True
        if self._headless != bool(force_headless):
            return True
        if self._request_count >= WORKER_MAX_REQUESTS:
            return True
        if self._last_used_at and time.time() - self._last_used_at >= WORKER_MAX_IDLE_SECONDS:
            return True
        try:
            if self._page.is_closed():
                return True
        except Exception:
            return True
        return False

    def _ensure_runtime(self, config: dict, base_dir: Path, force_headless: bool) -> None:
        if self._needs_restart(force_headless):
            self._start_runtime(config, base_dir, force_headless)

    def _run_once(
        self,
        config: dict,
        base_dir: Path,
        force_headless: bool,
        cli_keywords: List[str],
        cli_limit: Optional[int],
        output_prefix: str,
        progress,
    ) -> dict:
        self._ensure_runtime(config, base_dir, force_headless)
        result = main.perform_extraction_with_page(
            config=config,
            base_dir=base_dir,
            context=self._context,
            page=self._page,
            cli_keywords=cli_keywords,
            cli_limit=cli_limit,
            output_prefix=output_prefix,
            progress=progress,
        )
        self._request_count += 1
        self._last_used_at = time.time()
        return result

    def extract(
        self,
        *,
        config: dict,
        base_dir: Path,
        force_headless: bool,
        cli_keywords: List[str],
        cli_limit: Optional[int],
        output_prefix: str,
        progress,
    ) -> dict:
        try:
            return self._run_once(
                config=config,
                base_dir=base_dir,
                force_headless=force_headless,
                cli_keywords=cli_keywords,
                cli_limit=cli_limit,
                output_prefix=output_prefix,
                progress=progress,
            )
        except Exception as first_exc:
            progress("Worker recovery triggered: {}".format(first_exc))
            self._start_runtime(config, base_dir, force_headless)
            return self._run_once(
                config=config,
                base_dir=base_dir,
                force_headless=force_headless,
                cli_keywords=cli_keywords,
                cli_limit=cli_limit,
                output_prefix=output_prefix,
                progress=progress,
            )

    def health(self) -> dict:
        return {
            "ready": bool(self._context and self._page),
            "headless": self._headless,
            "request_count": self._request_count,
            "max_requests": WORKER_MAX_REQUESTS,
            "max_idle_seconds": WORKER_MAX_IDLE_SECONDS,
            "last_used_at": self._last_used_at or None,
        }


BROWSER_WORKER = BrowserWorker()


def require_api_key(x_api_key: Optional[str]) -> None:
    if not API_KEY:
        return
    if x_api_key != API_KEY:
        raise HTTPException(status_code=401, detail="invalid api key")


def cleanup_output_files(paths: List[Path], delay_seconds: float = 3.0) -> None:
    time.sleep(max(delay_seconds, 0.0))
    for path in paths:
        try:
            path.unlink(missing_ok=True)
        except Exception:
            pass


def schedule_output_cleanup(paths: List[Path], delay_seconds: float = 3.0) -> None:
    thread = threading.Thread(
        target=cleanup_output_files,
        args=(paths, delay_seconds),
        daemon=True,
    )
    thread.start()


@app.get("/health")
def health() -> dict:
    return {
        "ok": True,
        "service": "duanjv",
        "busy": EXTRACT_LOCK.locked(),
        "config_path": str(CONFIG_PATH),
        "api_key_enabled": bool(API_KEY),
        "worker": BROWSER_WORKER.health(),
    }


@app.post("/extract")
def extract(payload: ExtractRequest, x_api_key: Optional[str] = Header(default=None)) -> dict:
    require_api_key(x_api_key)

    cli_keywords: List[str] = []
    if payload.keyword:
        cli_keywords.append(payload.keyword)
    cli_keywords.extend(payload.keywords)

    if not cli_keywords:
        raise HTTPException(status_code=400, detail="keyword or keywords is required")

    if not CONFIG_PATH.exists():
        raise HTTPException(status_code=500, detail="config.json not found in container")

    config = main.load_config(CONFIG_PATH)
    progress_lines: List[str] = []

    def progress(message: str) -> None:
        progress_lines.append(message)

    output_prefix = "api_{}".format(uuid4().hex)

    with EXTRACT_LOCK:
        try:
            result = BROWSER_WORKER.extract(
                config=config,
                base_dir=CONFIG_PATH.parent,
                force_headless=payload.headless,
                cli_keywords=cli_keywords,
                cli_limit=payload.limit,
                output_prefix=output_prefix,
                progress=progress,
            )
        except Exception as exc:
            raise HTTPException(
                status_code=500,
                detail={
                    "message": str(exc),
                    "progress": progress_lines,
                },
            ) from exc

    json_path = Path(result["json_path"])
    csv_path = Path(result["csv_path"])
    schedule_output_cleanup([json_path, csv_path], delay_seconds=3.0)

    response = {
        "ok": True,
        "keywords": result["keywords"],
        "row_count": result["row_count"],
        "json_path": str(json_path),
        "csv_path": str(csv_path),
        "progress": progress_lines,
    }
    if payload.include_rows:
        response["rows"] = result["rows"]
    return response
