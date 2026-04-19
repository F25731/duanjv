import os
import threading
import time
from pathlib import Path
from typing import List, Optional
from uuid import uuid4

from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel, Field

import main


APP_ROOT = Path(__file__).resolve().parent
CONFIG_PATH = Path(os.environ.get("DUANJV_CONFIG", APP_ROOT / "config.json")).resolve()
API_KEY = os.environ.get("DUANJV_API_KEY", "").strip()
EXTRACT_LOCK = threading.Lock()

app = FastAPI(title="duanjv", version="1.1.0")


class ExtractRequest(BaseModel):
    keyword: Optional[str] = None
    keywords: List[str] = Field(default_factory=list)
    limit: Optional[int] = None
    headless: bool = True
    include_rows: bool = True


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
            result = main.perform_extraction(
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
