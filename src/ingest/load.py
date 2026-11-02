"""Scrape allowlisted public pages into artifacts/raw/.

Only URLs on `config.sources.allowlist` are fetched (Groww / AMFI / SEBI / AMC).
For each allowlisted page: HTTP GET (httpx, timeout, polite delay), save
`<page_id>.html`, `<page_id>.txt` and `<page_id>.json` under `artifacts/raw/`.
Non-200 or network failures are recorded and skipped, never crashing the batch.
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx
from bs4 import BeautifulSoup

from src.config_loader import get_config, resolve_path
from src.models import RawPage, page_id_from_url

DROP_TAGS = ("script", "style", "noscript", "nav", "footer")

USER_AGENT = (
    "GrowwMfFaqAssistant/0.1 (+https://github.com/) research prototype; "
    "fetches only allowlisted official pages"
)

DEFAULT_TIMEOUT = 15.0
DEFAULT_POLITE_DELAY = 1.0


def now_utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _clean_lines(text: str) -> str:
    lines = (line.strip() for line in text.splitlines())
    return "\n".join(line for line in lines if line)


def extract_text(html: str) -> str:
    """Visible text: drop script/style/nav/footer, keep table contents."""
    soup = BeautifulSoup(html, "lxml")
    for tag in soup.find_all(DROP_TAGS):
        tag.decompose()
    return _clean_lines(soup.get_text(separator="\n"))


def _allowlist_entries(config: dict[str, Any]) -> list[dict[str, str]]:
    entries = config.get("sources", {}).get("allowlist", [])
    if not entries:
        raise ValueError("config.sources.allowlist is empty - nothing to scrape")
    return entries


def scrape_allowlist() -> tuple[list[RawPage], list[dict[str, Any]]]:
    """Fetch every allowlisted page. Returns (successful pages, failures)."""
    config = get_config()
    entries = _allowlist_entries(config)
    source_cfg = config.get("sources", {})
    timeout = float(source_cfg.get("timeout", DEFAULT_TIMEOUT))
    polite_delay = float(source_cfg.get("polite_delay", DEFAULT_POLITE_DELAY))
    raw_dir = resolve_path(str(config.get("artifacts", {}).get("raw_dir", "artifacts/raw")))
    raw_dir.mkdir(parents=True, exist_ok=True)

    pages: list[RawPage] = []
    failures: list[dict[str, Any]] = []

    with httpx.Client(follow_redirects=True, timeout=timeout, headers={"User-Agent": USER_AGENT}) as client:
        for i, entry in enumerate(entries):
            url = str(entry["url"])
            page_id = str(entry.get("page_id") or page_id_from_url(url))
            scheme_or_amc = str(entry.get("scheme_or_amc", ""))

            record: dict[str, Any] = {
                "page_id": page_id,
                "url": url,
                "fetched_at": now_utc_iso(),
                "http_status": 0,
                "scheme_or_amc": scheme_or_amc,
            }
            try:
                response = client.get(url)
                record["http_status"] = response.status_code
                if response.status_code == 200:
                    html_path = raw_dir / f"{page_id}.html"
                    text_path = raw_dir / f"{page_id}.txt"
                    json_path = raw_dir / f"{page_id}.json"

                    html_path.write_text(response.text, encoding="utf-8")
                    text_path.write_text(extract_text(response.text), encoding="utf-8")

                    record["text_path"] = str(text_path.relative_to(resolve_path(".")))
                    json_path.write_text(
                        json.dumps(record, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
                    )
                    pages.append(
                        RawPage(
                            page_id=page_id,
                            url=url,
                            fetched_at=record["fetched_at"],
                            http_status=response.status_code,
                            scheme_or_amc=scheme_or_amc,
                            text_path=record["text_path"],
                            html_path=str(html_path.relative_to(resolve_path("."))),
                        )
                    )
                    print(f"[200] {page_id} <- {url}")
                else:
                    failures.append({**record, "error": f"HTTP {response.status_code}"})
                    print(f"[{response.status_code}] {page_id} <- {url} (skipped)")
            except httpx.HTTPError as exc:
                failures.append({**record, "error": f"{type(exc).__name__}: {exc}"})
                print(f"[ERR] {page_id} <- {url} ({type(exc).__name__}, skipped)")

            if i < len(entries) - 1 and polite_delay > 0:
                time.sleep(polite_delay)

    failures_path = raw_dir / "failures.json"
    if failures:
        failures_path.write_text(
            json.dumps(failures, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )
    elif failures_path.exists():
        failures_path.unlink()

    return pages, failures


def load_allowlist() -> list[RawPage]:
    """Scrape all allowlisted pages; non-200s are skipped, not raised."""
    pages, failures = scrape_allowlist()
    print(f"Scraped {len(pages)} page(s); skipped {len(failures)} failure(s).")
    return pages
