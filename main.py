import argparse
import csv
import json
import os
import re
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Set, Tuple

from playwright.sync_api import (
    BrowserContext,
    Locator,
    Page,
    TimeoutError as PlaywrightTimeoutError,
    sync_playwright,
)


ROW_CONTEXT_JS = """
el => {
  const className = node => {
    if (!node) return '';
    const raw = node.className;
    if (!raw) return '';
    if (typeof raw === 'string') return raw;
    if (typeof raw.baseVal === 'string') return raw.baseVal;
    return String(raw);
  };

  const looksLikeRow = node => {
    if (!node) return false;
    const role = (node.getAttribute('role') || '').toLowerCase();
    const cls = className(node);
    return (
      node.tagName === 'TR' ||
      role === 'row' ||
      role === 'listitem' ||
      /row|record|item|card|list/i.test(cls)
    );
  };

  let node = el;
  while (node && node !== document.body) {
    if (looksLikeRow(node)) {
      return (node.innerText || '').trim();
    }
    node = node.parentElement;
  }

  return ((el.parentElement && el.parentElement.innerText) || el.innerText || '').trim();
}
"""


def parse_args() -> argparse.Namespace:
    common_parser = argparse.ArgumentParser(add_help=False)
    common_parser.add_argument(
        "--config",
        default="config.json",
        help="Path to config.json. Defaults to ./config.json",
    )
    common_parser.add_argument(
        "--headless",
        action="store_true",
        help="Run in headless mode even if config sets headless=false.",
    )

    parser = argparse.ArgumentParser(
        description="Extract Quark links from a KDocs page via Playwright."
        ,
        parents=[common_parser],
    )

    subparsers = parser.add_subparsers(dest="command", required=True)

    login_parser = subparsers.add_parser(
        "login",
        help="Open the page and let you complete WeChat scan login manually.",
        parents=[common_parser],
    )
    login_parser.add_argument(
        "--url",
        default=None,
        help="Optional override for doc_url in config.",
    )

    extract_parser = subparsers.add_parser(
        "extract",
        help="Search by keyword and extract Quark links.",
        parents=[common_parser],
    )
    extract_parser.add_argument(
        "--keyword",
        action="append",
        default=[],
        help="Extra keyword. Can be passed multiple times.",
    )
    extract_parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Optional max matched rows per keyword for this run.",
    )

    return parser.parse_args()


def configure_stdio() -> None:
    for stream_name in ("stdout", "stderr"):
        stream = getattr(sys, stream_name, None)
        if not stream:
            continue
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            try:
                reconfigure(encoding="utf-8", errors="replace")
            except Exception:
                pass


def load_config(config_path: Path) -> Dict[str, Any]:
    if not config_path.exists():
        raise FileNotFoundError(
            "Config file not found: {}. Copy config.example.json to config.json first.".format(
                config_path
            )
        )
    return json.loads(config_path.read_text(encoding="utf-8"))


def resolve_path(base_dir: Path, value: str) -> Path:
    path = Path(value)
    if path.is_absolute():
        return path
    return (base_dir / path).resolve()


def ensure_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def load_keywords(config: Dict[str, Any], base_dir: Path, cli_keywords: Sequence[str]) -> List[str]:
    keywords: List[str] = []

    for keyword in config.get("keywords", []):
        cleaned = normalize_text(str(keyword))
        if cleaned:
            keywords.append(cleaned)

    keywords_file = config.get("keywords_file")
    if keywords_file:
        file_path = resolve_path(base_dir, str(keywords_file))
        if file_path.exists():
            for line in file_path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line and not line.startswith("#"):
                    keywords.append(line)

    for keyword in cli_keywords:
        cleaned = normalize_text(keyword)
        if cleaned:
            keywords.append(cleaned)

    seen: Set[str] = set()
    deduped: List[str] = []
    for keyword in keywords:
        if keyword not in seen:
            deduped.append(keyword)
            seen.add(keyword)

    return deduped


def normalize_text(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def is_login_page(page: Page) -> bool:
    url = page.url.lower()
    if "account.wps.cn" in url or "passport" in url:
        return True
    try:
        body = page.locator("body").inner_text(timeout=1200)
    except Exception:
        return False
    return "扫码登录" in body or "登录" in body and "微信" in body


def timestamp() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def write_debug_snapshot(page: Page, output_dir: Path, label: str) -> Tuple[Path, Path]:
    ensure_dir(output_dir)
    stamp = "{}_{}".format(timestamp(), slugify(label))
    screenshot_path = output_dir / "{}.png".format(stamp)
    html_path = output_dir / "{}.html".format(stamp)
    try:
        page.screenshot(path=str(screenshot_path), full_page=True)
    except Exception:
        screenshot_path.write_text("screenshot failed", encoding="utf-8")
    try:
        html_path.write_text(page.content(), encoding="utf-8")
    except Exception:
        html_path.write_text("html capture failed", encoding="utf-8")
    return screenshot_path, html_path


def slugify(value: str) -> str:
    value = re.sub(r"[^0-9A-Za-z\u4e00-\u9fff_-]+", "_", value)
    return value.strip("_") or "debug"


def wait_ms(duration_ms: int) -> None:
    time.sleep(max(duration_ms, 0) / 1000.0)


def first_visible(locator: Locator, timeout_ms: int = 500) -> Optional[Locator]:
    try:
        count = locator.count()
    except Exception:
        return None

    for index in range(count):
        candidate = locator.nth(index)
        try:
            if candidate.is_visible(timeout=timeout_ms):
                return candidate
        except Exception:
            continue
    return None


def first_clickable_button_by_names(page: Page, names: Sequence[str]) -> Optional[Locator]:
    for name in names:
        patterns = [
            re.compile(r"^\s*{}\s*$".format(re.escape(name))),
            re.compile(re.escape(name)),
        ]
        for pattern in patterns:
            locator = first_visible(page.get_by_role("button", name=pattern))
            if locator:
                return locator
            locator = first_visible(page.get_by_text(pattern))
            if locator:
                return locator
    return None


def top_toolbar_find_button(page: Page) -> Optional[Locator]:
    # KDocs currently exposes the correct document-level find action as a
    # toolbar button with a magnifier icon near the top-right toolbar area.
    icon_button = first_visible(page.locator("button:has(.kd-icon-magnifier)"))
    if icon_button:
        return icon_button

    return first_clickable_button_by_names(page, ["查找"])


def page_has_wps_api(page: Page) -> bool:
    try:
        return bool(
            page.evaluate(
                "() => Boolean(window.WPSOpenApi && window.WPSOpenApi.Application)"
            )
        )
    except Exception:
        return False


def same_document_url(current_url: str, doc_url: str) -> bool:
    current = str(current_url or "").strip()
    target = str(doc_url or "").strip()
    if not current or not target:
        return False
    if current == target:
        return True
    return current.split("?", 1)[0] == target.split("?", 1)[0]


def describe_page_state(page: Page, selectors: Dict[str, Any]) -> str:
    try:
        title = normalize_text(page.title())
    except Exception:
        title = ""

    current_url = str(getattr(page, "url", "") or "").strip()
    has_modal_input = bool(global_find_input(page))
    has_find_button = bool(
        top_toolbar_find_button(page)
        or first_clickable_button_by_names(
            page, selectors.get("search_button_names", ["鏌ユ壘", "鎼滅储"])
        )
    )
    return (
        "url={!r}, title={!r}, login_page={}, wps_api={}, find_button={}, find_input={}".format(
            current_url,
            title,
            is_login_page(page),
            page_has_wps_api(page),
            has_find_button,
            has_modal_input,
        )
    )


def wait_for_document_ready(
    page: Page,
    selectors: Dict[str, Any],
    timeout_ms: int = 15000,
    poll_ms: int = 300,
) -> None:
    deadline = time.time() + max(timeout_ms, 0) / 1000.0
    last_state = ""

    while time.time() < deadline:
        if is_login_page(page):
            raise RuntimeError("The current page is the login page.")

        has_modal_input = bool(global_find_input(page))
        has_find_button = bool(
            top_toolbar_find_button(page)
            or first_clickable_button_by_names(
                page, selectors.get("search_button_names", ["鏌ユ壘", "鎼滅储"])
            )
        )
        if has_modal_input or has_find_button:
            return

        last_state = describe_page_state(page, selectors)
        wait_ms(poll_ms)

    raise RuntimeError(
        "Document page did not become ready within {} ms. {}".format(
            timeout_ms,
            last_state or describe_page_state(page, selectors),
        )
    )


def ensure_document_page_ready(
    page: Page,
    doc_url: str,
    selectors: Dict[str, Any],
    ready_timeout_ms: int = 15000,
) -> None:
    attempts: List[str] = []

    if not same_document_url(page.url, doc_url):
        page.goto(doc_url, wait_until="domcontentloaded")

    try:
        wait_for_document_ready(page, selectors, timeout_ms=ready_timeout_ms)
        return
    except Exception as exc:
        attempts.append("initial wait failed: {}".format(exc))

    try:
        page.reload(wait_until="domcontentloaded")
        wait_for_document_ready(page, selectors, timeout_ms=ready_timeout_ms)
        return
    except Exception as exc:
        attempts.append("reload failed: {}".format(exc))

    page.goto(doc_url, wait_until="domcontentloaded")
    try:
        wait_for_document_ready(page, selectors, timeout_ms=ready_timeout_ms)
        return
    except Exception as exc:
        attempts.append("goto failed: {}".format(exc))

    raise RuntimeError(" | ".join(attempts))


def visible_global_find_modal(page: Page) -> Optional[Locator]:
    return first_visible(page.locator(".db-global-find-modal-panel"))


def global_find_input(page: Page) -> Optional[Locator]:
    modal = visible_global_find_modal(page)
    if not modal:
        return None

    candidates = [
        modal.locator(".db-global-find-setting .db-global-find-keyword-setting input.kd-input-inner"),
        modal.locator(".db-global-find-setting input.kd-input-inner"),
        modal.locator("input.kd-input-inner"),
        modal.locator("input[type='text']"),
        modal.locator("textarea"),
    ]
    for locator in candidates:
        visible = first_visible(locator)
        if visible:
            return visible
    return None


def global_find_all_button(page: Page) -> Optional[Locator]:
    modal = visible_global_find_modal(page)
    if not modal:
        return None

    candidates = [
        modal.locator(".db-global-find-control button.kd-button-secondary:not(.kd-button-icon)"),
        modal.locator(".db-global-find-control .find-control-wrapper .wo-button").nth(0).locator("button"),
    ]
    for locator in candidates:
        visible = first_visible(locator)
        if visible:
            return visible
    return None


def click_global_find_all(page: Page, wait_after_ms: int) -> bool:
    button = global_find_all_button(page)
    if not button:
        return False

    button.click()
    wait_ms(wait_after_ms)
    return True


def global_find_match_counts(page: Page) -> Tuple[int, int]:
    modal = visible_global_find_modal(page)
    if not modal:
        return (0, 0)

    match_text_locator = first_visible(modal.locator(".match-result-text"))
    if not match_text_locator:
        return (0, 0)

    text = normalize_text(match_text_locator.inner_text())
    match = re.search(r"(\d+)\s*/\s*(\d+)", text)
    if not match:
        return (0, 0)
    return (int(match.group(1)), int(match.group(2)))


def global_find_has_no_data(page: Page) -> bool:
    modal = visible_global_find_modal(page)
    if not modal:
        return False

    no_data_tip = first_visible(modal.locator(".db-global-find-result .no-data-tip"))
    if no_data_tip:
        return True

    try:
        text = normalize_text(modal.inner_text(timeout=800))
    except Exception:
        return False
    return "未找到精确结果" in text


def click_next_search_result(page: Page, wait_after_ms: int) -> bool:
    modal = visible_global_find_modal(page)
    if not modal:
        return False

    next_button = first_visible(modal.locator(".db-global-find-control .find-next button"))
    if not next_button:
        return False

    next_button.click()
    wait_ms(wait_after_ms)
    return True


def select_global_find_result(page: Page, raw_id: str, wait_after_ms: int) -> bool:
    selected = page.evaluate(
        """
        async (rawId) => {
          const container = document.querySelector('.db-global-find-select-list');
          if (!container) {
            return false;
          }

          const findItem = () =>
            Array.from(container.querySelectorAll('.select-item')).find(
              el => ((el.id || '').trim() === rawId)
            );

          const clickItem = item => {
            item.scrollIntoView({ block: 'nearest' });
            item.click();
            return true;
          };

          let item = findItem();
          if (item) {
            return clickItem(item);
          }

          container.scrollTop = 0;
          container.dispatchEvent(new Event('scroll', { bubbles: true }));
          await new Promise(resolve => setTimeout(resolve, 80));

          let guard = 0;
          while (guard < 400) {
            guard += 1;
            item = findItem();
            if (item) {
              return clickItem(item);
            }

            const nextTop = Math.min(
              container.scrollTop + Math.max(36, container.clientHeight - 24),
              container.scrollHeight - container.clientHeight
            );
            if (nextTop === container.scrollTop) {
              break;
            }

            container.scrollTop = nextTop;
            container.dispatchEvent(new Event('scroll', { bubbles: true }));
            await new Promise(resolve => setTimeout(resolve, 80));
          }

          item = findItem();
          if (item) {
            return clickItem(item);
          }

          return false;
        }
        """,
        raw_id,
    )
    wait_ms(wait_after_ms)
    return bool(selected)


def collect_global_find_results(page: Page) -> List[Dict[str, str]]:
    payload = page.evaluate(
        """
        async () => {
          const container = document.querySelector('.db-global-find-select-list');
          if (!container) {
            return [];
          }

          const seen = new Map();
          const collect = () => {
            for (const el of container.querySelectorAll('.select-item')) {
              const rawId = (el.id || '').trim();
              const text = (el.innerText || '').replace(/\\s+/g, ' ').trim();
              if (rawId && text && !seen.has(rawId)) {
                seen.set(rawId, { rawId, text });
              }
            }
          };

          collect();
          let guard = 0;
          let previousTop = -1;
          while (guard < 400) {
            guard += 1;
            const nextTop = Math.min(
              container.scrollTop + Math.max(36, container.clientHeight - 24),
              container.scrollHeight - container.clientHeight
            );
            if (nextTop === previousTop || nextTop === container.scrollTop) {
              break;
            }
            previousTop = container.scrollTop;
            container.scrollTop = nextTop;
            container.dispatchEvent(new Event('scroll', { bubbles: true }));
            await new Promise(resolve => setTimeout(resolve, 80));
            collect();
          }

          container.scrollTop = 0;
          container.dispatchEvent(new Event('scroll', { bubbles: true }));
          return Array.from(seen.values());
        }
        """
    )

    results: List[Dict[str, str]] = []
    for item in payload or []:
        raw_id = str(item.get("rawId", "")).strip()
        title = normalize_text(str(item.get("text", "")))
        record_id = raw_id.rsplit("-", 1)[0] if "-" in raw_id else raw_id
        if raw_id and record_id and title:
            results.append({"raw_id": raw_id, "record_id": record_id, "title": title})
    return results


def read_current_selected_record(page: Page) -> Optional[Dict[str, Any]]:
    payload = page.evaluate(
        """
        async () => {
          const api = window.WPSOpenApi;
          if (!api || !api.Application) {
            return null;
          }

          const app = api.Application;
          const sheetId = await app.ActiveSheet.Id;
          const selectionIds = await app.Selection.GetSelectionRecordIds();
          const recordId = selectionIds && selectionIds[0] && selectionIds[0][0];
          if (!recordId) {
            return null;
          }

          const record = await app.Record.GetRecord({ SheetId: sheetId, RecordId: recordId });
          return JSON.parse(JSON.stringify({ sheetId, recordId, record }));
        }
        """
    )
    if not payload:
        return None
    return payload


def read_record_by_id(page: Page, record_id: str) -> Optional[Dict[str, Any]]:
    payload = page.evaluate(
        """
        async (recordId) => {
          const api = window.WPSOpenApi;
          if (!api || !api.Application) {
            return null;
          }

          const app = api.Application;
          const sheetId = await app.ActiveSheet.Id;
          const record = await app.Record.GetRecord({ SheetId: sheetId, RecordId: recordId });
          return JSON.parse(JSON.stringify({ sheetId, recordId, record }));
        }
        """,
        record_id,
    )
    if not payload:
        return None
    return payload


def extract_links_from_value(value: Any, regex: re.Pattern, found: Set[str]) -> None:
    if value is None:
        return

    if isinstance(value, str):
        for match in regex.findall(value):
            found.add(clean_url(match))
        return

    if isinstance(value, dict):
        for item in value.values():
            extract_links_from_value(item, regex, found)
        return

    if isinstance(value, list):
        for item in value:
            extract_links_from_value(item, regex, found)


def extract_quark_links_from_record_payload(payload: Dict[str, Any], domains: Sequence[str]) -> List[str]:
    regex = compile_quark_regex(domains)
    found: Set[str] = set()
    record = payload.get("record", {})
    fields = record.get("fields", {})

    extract_links_from_value(fields, regex, found)
    return sorted(found)


def extract_record_title_from_payload(payload: Dict[str, Any]) -> str:
    record = payload.get("record", {})
    fields = record.get("fields", {})
    for key in ("短剧名称", "名称", "标题", "name", "title"):
        value = fields.get(key)
        if isinstance(value, str) and value.strip():
            return normalize_text(value)
    return normalize_text(str(record.get("id", "")))


def best_fallback_search_input(page: Page, selectors: Dict[str, Any]) -> Optional[Locator]:
    candidates: List[Tuple[int, Locator]] = []

    for placeholder in selectors.get("search_input_placeholders", []):
        locator = page.get_by_placeholder(placeholder)
        try:
            count = locator.count()
        except Exception:
            count = 0
        for index in range(count):
            item = locator.nth(index)
            try:
                if not item.is_visible(timeout=200):
                    continue
                box = item.bounding_box() or {}
                x = int(box.get("x", 0))
                y = int(box.get("y", 9999))
                score = 0
                if x >= 1000:
                    score += 100
                if y <= 180:
                    score += 20
                if x <= 250:
                    score -= 200
                candidates.append((score, item))
            except Exception:
                continue

    for css_selector in selectors.get("search_input_selectors", []):
        locator = page.locator(css_selector)
        try:
            count = locator.count()
        except Exception:
            count = 0
        for index in range(count):
            item = locator.nth(index)
            try:
                if not item.is_visible(timeout=200):
                    continue
                box = item.bounding_box() or {}
                x = int(box.get("x", 0))
                y = int(box.get("y", 9999))
                score = 0
                if x >= 1000:
                    score += 100
                if y <= 180:
                    score += 20
                if x <= 250:
                    score -= 200
                candidates.append((score, item))
            except Exception:
                continue

    if not candidates:
        return None

    candidates.sort(key=lambda item: item[0], reverse=True)
    return candidates[0][1]


def first_input_by_config(page: Page, selectors: Dict[str, Any]) -> Optional[Locator]:
    for placeholder in selectors.get("search_input_placeholders", []):
        locator = first_visible(page.get_by_placeholder(placeholder))
        if locator:
            return locator

    for css_selector in selectors.get("search_input_selectors", []):
        locator = first_visible(page.locator(css_selector))
        if locator:
            return locator

    generic_candidates = [
        page.locator("input[type='search']"),
        page.locator("input[placeholder]"),
        page.locator("input"),
        page.locator("textarea"),
    ]
    for locator in generic_candidates:
        visible = first_visible(locator)
        if visible:
            return visible
    return None


def open_search_if_needed(page: Page, selectors: Dict[str, Any]) -> Locator:
    search_input = global_find_input(page)
    if search_input:
        return search_input

    search_button = top_toolbar_find_button(page)
    if not search_button:
        search_button = first_clickable_button_by_names(
            page, selectors.get("search_button_names", ["查找", "搜索"])
        )
    if not search_button:
        raise RuntimeError("Could not find the page's 查找/搜索 button.")

    search_button.click()
    wait_ms(600)

    search_input = global_find_input(page)
    if search_input:
        return search_input

    search_input = best_fallback_search_input(page, selectors)
    if not search_input:
        raise RuntimeError("Could not find the search input after clicking 查找.")
    return search_input


def open_search_if_needed_resilient(
    page: Page,
    selectors: Dict[str, Any],
    timeout_ms: int = 10000,
) -> Locator:
    deadline = time.time() + max(timeout_ms, 0) / 1000.0
    click_attempts = 0
    last_state = ""

    while time.time() < deadline:
        search_input = global_find_input(page)
        if search_input:
            return search_input

        search_button = top_toolbar_find_button(page)
        if not search_button:
            search_button = first_clickable_button_by_names(
                page, selectors.get("search_button_names", ["鏌ユ壘", "鎼滅储"])
            )
        if search_button and click_attempts < 2:
            click_attempts += 1
            search_button.click()
            wait_ms(600)
            continue

        search_input = best_fallback_search_input(page, selectors)
        if search_input:
            return search_input

        last_state = describe_page_state(page, selectors)
        wait_ms(300)

    raise RuntimeError(
        "Could not find the page's 鏌ユ壘/鎼滅储 button. {}".format(
            last_state or describe_page_state(page, selectors)
        )
    )


def fill_search_keyword(page: Page, selectors: Dict[str, Any], keyword: str, wait_after_ms: int) -> None:
    search_input = open_search_if_needed_resilient(page, selectors)
    search_input.click()
    try:
        search_input.fill("")
    except Exception:
        search_input.press("Control+A")
        search_input.press("Backspace")
    search_input.fill(keyword)

    if click_global_find_all(page, wait_after_ms):
        return

    try:
        search_input.press("Enter")
    except Exception:
        pass
    wait_ms(wait_after_ms)


def extract_row_context(locator: Locator) -> str:
    try:
        row_text = locator.evaluate(ROW_CONTEXT_JS)
    except Exception:
        row_text = ""
    return normalize_text(row_text)


def visible_view_buttons(page: Page, selectors: Dict[str, Any]) -> List[Tuple[Locator, str]]:
    results: List[Tuple[Locator, str]] = []
    seen: Set[str] = set()

    for name in selectors.get("view_button_names", ["查看"]):
        patterns = [
            re.compile(r"^\s*{}\s*$".format(re.escape(name))),
            re.compile(re.escape(name)),
        ]
        groups = []
        for pattern in patterns:
            groups.append(page.get_by_role("button", name=pattern))
            groups.append(page.get_by_text(pattern))

        for group in groups:
            try:
                count = group.count()
            except Exception:
                continue

            for index in range(count):
                locator = group.nth(index)
                try:
                    if not locator.is_visible(timeout=300):
                        continue
                except Exception:
                    continue

                row_text = extract_row_context(locator)
                signature = row_text or "view_button_{}".format(index)
                signature = normalize_text(signature)[:500]
                if signature in seen:
                    continue

                seen.add(signature)
                results.append((locator, signature))
    return results


def clean_url(url: str) -> str:
    return url.strip().rstrip(").,;\"'")


def compile_quark_regex(domains: Sequence[str]) -> re.Pattern:
    escaped_domains = [re.escape(domain) for domain in domains if domain]
    if not escaped_domains:
        escaped_domains = [re.escape("pan.quark.cn")]
    domain_group = "|".join(escaped_domains)
    pattern = r"https?://(?:{})/[^\s\"'<>]+".format(domain_group)
    return re.compile(pattern, re.IGNORECASE)


def extract_quark_links_from_page(page: Page, domains: Sequence[str]) -> List[str]:
    regex = compile_quark_regex(domains)
    found: Set[str] = set()

    urls_to_check = [page.url]
    for frame in page.frames:
        try:
            urls_to_check.append(frame.url)
        except Exception:
            continue

    for url in urls_to_check:
        for match in regex.findall(url or ""):
            found.add(clean_url(match))

    for frame in page.frames:
        try:
            anchors = frame.locator("a[href]").evaluate_all(
                "(elements) => elements.map((el) => el.href || el.getAttribute('href') || '')"
            )
        except Exception:
            anchors = []
        for anchor in anchors:
            for match in regex.findall(anchor or ""):
                found.add(clean_url(match))

        try:
            html = frame.content()
        except Exception:
            html = ""
        for match in regex.findall(html):
            found.add(clean_url(match))

        try:
            text = frame.locator("body").inner_text(timeout=1200)
        except Exception:
            text = ""
        for match in regex.findall(text):
            found.add(clean_url(match))

    return sorted(found)


def close_detail_surface(page: Page, selectors: Dict[str, Any]) -> None:
    close_button = first_clickable_button_by_names(
        page, selectors.get("close_button_names", ["关闭", "返回", "取消", "收起"])
    )
    if close_button:
        try:
            close_button.click()
            wait_ms(500)
            return
        except Exception:
            pass

    try:
        page.keyboard.press("Escape")
        wait_ms(400)
    except Exception:
        pass


def maybe_click_view_and_collect(
    context: BrowserContext,
    page: Page,
    button: Locator,
    selectors: Dict[str, Any],
    domains: Sequence[str],
    after_view_wait_ms: int,
) -> List[str]:
    popup_page: Optional[Page] = None

    try:
        with context.expect_page(timeout=1500) as popup_info:
            button.click()
        popup_page = popup_info.value
        popup_page.wait_for_load_state("domcontentloaded", timeout=5000)
    except PlaywrightTimeoutError:
        button.click()
    except Exception:
        button.click(force=True)

    wait_ms(after_view_wait_ms)

    target_page = popup_page or page
    links = extract_quark_links_from_page(target_page, domains)

    if popup_page and not popup_page.is_closed():
        popup_page.close()
    else:
        close_detail_surface(page, selectors)

    wait_ms(300)
    return links


def save_results(
    output_dir: Path,
    rows: List[Dict[str, Any]],
    output_prefix: Optional[str] = None,
) -> Tuple[Path, Path]:
    ensure_dir(output_dir)
    if output_prefix:
        safe_prefix = slugify(output_prefix)
        json_path = output_dir / "{}.json".format(safe_prefix)
        csv_path = output_dir / "{}.csv".format(safe_prefix)
    else:
        json_path = output_dir / "results.json"
        csv_path = output_dir / "results.csv"

    json_path.write_text(
        json.dumps(rows, ensure_ascii=False, indent=2),
        encoding="utf-8-sig",
    )

    with csv_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "keyword",
                "match_index",
                "result_raw_id",
                "record_id",
                "match_context",
                "quark_url",
                "source_page_url",
            ],
        )
        writer.writeheader()
        for row in rows:
            writer.writerow(row)

    return json_path, csv_path


def browser_launch_args(config: Dict[str, Any]) -> List[str]:
    args = ["--window-size=1800,1100", "--force-device-scale-factor=0.8"]

    for value in config.get("browser_args", []):
        arg = str(value).strip()
        if arg and arg not in args:
            args.append(arg)

    if os.environ.get("PW_CHROMIUM_NO_SANDBOX") == "1":
        for arg in ("--no-sandbox", "--disable-setuid-sandbox"):
            if arg not in args:
                args.append(arg)

    return args


def launch_persistent_context(playwright, config: Dict[str, Any], base_dir: Path, force_headless: bool) -> BrowserContext:
    profile_dir = resolve_path(base_dir, str(config.get("browser_profile_dir", "browser_profile")))
    ensure_dir(profile_dir)
    return playwright.chromium.launch_persistent_context(
        user_data_dir=str(profile_dir),
        headless=bool(config.get("headless", False) or force_headless),
        accept_downloads=True,
        viewport={"width": 1800, "height": 1100},
        args=browser_launch_args(config),
    )


def export_storage_state(context: BrowserContext, config: Dict[str, Any], base_dir: Path) -> None:
    storage_state_path = resolve_path(
        base_dir, str(config.get("storage_state_path", "playwright_state.json"))
    )
    context.storage_state(path=str(storage_state_path))


def run_login(config: Dict[str, Any], base_dir: Path, force_headless: bool, override_url: Optional[str]) -> int:
    doc_url = override_url or config.get("doc_url")
    if not doc_url:
        raise ValueError("doc_url is missing in config.")

    with sync_playwright() as playwright:
        context = launch_persistent_context(playwright, config, base_dir, force_headless)
        page = context.pages[0] if context.pages else context.new_page()
        page.goto(doc_url, wait_until="domcontentloaded")

        print("Browser opened.")
        print("Use WeChat scan login manually, then make sure the document page is accessible.")
        print("When the document is ready, press Enter here to save and close.")
        input()

        export_storage_state(context, config, base_dir)
        context.close()

    print("Login state saved.")
    return 0


def perform_extraction_with_page(
    config: Dict[str, Any],
    base_dir: Path,
    context: BrowserContext,
    page: Page,
    cli_keywords: Sequence[str],
    cli_limit: Optional[int],
    output_prefix: Optional[str] = None,
    progress: Optional[Callable[[str], None]] = None,
) -> Dict[str, Any]:
    def emit(message: str) -> None:
        if progress:
            progress(message)

    doc_url = config.get("doc_url")
    if not doc_url:
        raise ValueError("doc_url is missing in config.")

    keywords = load_keywords(config, base_dir, cli_keywords)
    if not keywords:
        raise ValueError("No keywords found. Add them to config.json or keywords.txt.")

    output_dir = resolve_path(base_dir, str(config.get("output_dir", "output")))
    ensure_dir(output_dir)

    selectors = config.get("selectors", {})
    search_wait_ms = int(config.get("search_wait_ms", 1200))
    ready_timeout_ms = int(config.get("ready_timeout_ms", 15000))
    max_rows_per_keyword = int(cli_limit or config.get("max_rows_per_keyword", 500))
    quark_domains = config.get("quark_domains", ["pan.quark.cn"])

    ensure_document_page_ready(
        page,
        doc_url=str(doc_url),
        selectors=selectors,
        ready_timeout_ms=ready_timeout_ms,
    )

    if is_login_page(page):
        screenshot_path, html_path = write_debug_snapshot(page, output_dir, "login_required")
        raise RuntimeError(
            "The persistent session is not logged in. Run `python main.py login --config config.json` first. "
            "Debug files: {}, {}".format(screenshot_path, html_path)
        )

    rows: List[Dict[str, Any]] = []

    for keyword in keywords:
        emit("Searching keyword: {}".format(keyword))
        try:
            fill_search_keyword(page, selectors, keyword, search_wait_ms)
        except Exception as exc:
            screenshot_path, html_path = write_debug_snapshot(
                page, output_dir, "search_failed_{}".format(keyword)
            )
            emit(
                "Search failed for keyword {}. Debug files: {}, {}. Error: {}".format(
                    keyword, screenshot_path, html_path, exc
                )
            )
            continue

        if global_find_has_no_data(page):
            emit("No results for keyword: {}".format(keyword))
            continue

        _current_index, total_matches = global_find_match_counts(page)
        search_results = collect_global_find_results(page)
        if total_matches <= 0 or not search_results:
            emit("No usable results collected for keyword: {}".format(keyword))
            continue

        emit("Matched: {} Collected: {}".format(total_matches, len(search_results)))

        hard_limit = min(len(search_results), max_rows_per_keyword)
        keyword_row_count = 0
        keyword_match_with_link_count = 0

        for match_index, item in enumerate(search_results[:hard_limit], start=1):
            record_id = item["record_id"]

            payload: Optional[Dict[str, Any]] = None
            read_error: Optional[Exception] = None
            try:
                payload = read_record_by_id(page, record_id)
            except Exception as exc:
                read_error = exc

            if not payload and select_global_find_result(page, item["raw_id"], search_wait_ms):
                try:
                    payload = read_current_selected_record(page)
                except Exception as exc:
                    read_error = exc

            if not payload:
                screenshot_path, html_path = write_debug_snapshot(
                    page, output_dir, "record_read_failed_{}_{}".format(keyword, record_id)
                )
                emit(
                    "Record read failed for keyword {} record {} ({}). Debug files: {}, {}. Error: {}".format(
                        keyword,
                        record_id,
                        item["title"],
                        screenshot_path,
                        html_path,
                        read_error,
                    )
                )
                continue

            actual_record_id = str(payload.get("recordId") or record_id)
            title = item["title"] or extract_record_title_from_payload(payload)
            links = extract_quark_links_from_record_payload(payload, quark_domains)

            if not links and select_global_find_result(page, item["raw_id"], search_wait_ms):
                try:
                    selected_payload = read_current_selected_record(page)
                    if selected_payload:
                        payload = selected_payload
                        actual_record_id = str(payload.get("recordId") or actual_record_id)
                        title = item["title"] or extract_record_title_from_payload(payload)
                        links = extract_quark_links_from_record_payload(payload, quark_domains)
                except Exception as exc:
                    read_error = exc

            if not links:
                screenshot_path, html_path = write_debug_snapshot(
                    page, output_dir, "record_link_missing_{}_{}".format(keyword, record_id)
                )
                emit(
                    "No Quark link found for keyword {} record {} ({} / {}). Debug files: {}, {}. Last error: {}".format(
                        keyword,
                        actual_record_id,
                        item["raw_id"],
                        title,
                        screenshot_path,
                        html_path,
                        read_error,
                    )
                )
                continue

            keyword_match_with_link_count += 1
            for link in links:
                keyword_row_count += 1
                rows.append(
                    {
                        "keyword": keyword,
                        "match_index": match_index,
                        "result_raw_id": item["raw_id"],
                        "record_id": actual_record_id,
                        "match_context": title,
                        "quark_url": link,
                        "source_page_url": page.url,
                    }
                )

        emit(
            "Extracted links for keyword {}: {} rows from {} matched results".format(
                keyword,
                keyword_row_count,
                keyword_match_with_link_count,
            )
        )

    export_storage_state(context, config, base_dir)
    json_path, csv_path = save_results(output_dir, rows, output_prefix=output_prefix)
    return {
        "rows": rows,
        "json_path": json_path,
        "csv_path": csv_path,
        "row_count": len(rows),
        "keywords": list(keywords),
        "output_dir": output_dir,
    }


def perform_extraction(
    config: Dict[str, Any],
    base_dir: Path,
    force_headless: bool,
    cli_keywords: Sequence[str],
    cli_limit: Optional[int],
    output_prefix: Optional[str] = None,
    progress: Optional[Callable[[str], None]] = None,
) -> Dict[str, Any]:
    with sync_playwright() as playwright:
        context = launch_persistent_context(playwright, config, base_dir, force_headless)
        page = context.pages[0] if context.pages else context.new_page()
        try:
            return perform_extraction_with_page(
                config=config,
                base_dir=base_dir,
                context=context,
                page=page,
                cli_keywords=cli_keywords,
                cli_limit=cli_limit,
                output_prefix=output_prefix,
                progress=progress,
            )
        finally:
            context.close()


def run_extract(
    config: Dict[str, Any],
    base_dir: Path,
    force_headless: bool,
    cli_keywords: Sequence[str],
    cli_limit: Optional[int],
) -> int:
    result = perform_extraction(
        config=config,
        base_dir=base_dir,
        force_headless=force_headless,
        cli_keywords=cli_keywords,
        cli_limit=cli_limit,
        progress=print,
    )
    print("Done.")
    print("JSON:", result["json_path"])
    print("CSV:", result["csv_path"])
    print("Rows:", result["row_count"])
    return 0


def main() -> int:
    configure_stdio()
    args = parse_args()
    config_path = Path(args.config).resolve()
    base_dir = config_path.parent
    config = load_config(config_path)

    if args.command == "login":
        return run_login(
            config=config,
            base_dir=base_dir,
            force_headless=args.headless,
            override_url=args.url,
        )

    if args.command == "extract":
        return run_extract(
            config=config,
            base_dir=base_dir,
            force_headless=args.headless,
            cli_keywords=args.keyword,
            cli_limit=args.limit,
        )

    raise ValueError("Unsupported command: {}".format(args.command))


if __name__ == "__main__":
    raise SystemExit(main())
