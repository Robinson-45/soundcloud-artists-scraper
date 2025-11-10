import json
import logging
import time
from pathlib import Path
from typing import Any, Dict, Optional

import requests
from requests import Response
from requests.exceptions import RequestException

LOGGER_NAME = "soundcloud_scraper"

def setup_logging(level_name: str = "INFO") -> None:
    """
    Configure root logging for the scraper.
    Safe to call multiple times.
    """
    level = getattr(logging, level_name.upper(), logging.INFO)
    if logging.getLogger().handlers:
        # Already configured, just set level.
        logging.getLogger().setLevel(level)
        return

    logging.basicConfig(
        level=level,
        format="%(asctime)s | %(name)s | %(levelname)s | %(message)s",
    )

def _build_headers(user_agent: Optional[str] = None) -> Dict[str, str]:
    headers = {
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.5",
        "Connection": "keep-alive",
    }
    if user_agent:
        headers["User-Agent"] = user_agent
    return headers

def http_get(
    url: str,
    timeout: int = 15,
    max_retries: int = 3,
    backoff_factor: float = 0.5,
    user_agent: Optional[str] = None,
    proxy: Optional[str] = None,
) -> Response:
    """
    Make a GET request with basic retry logic.

    Raises RequestException on repeated failure.
    """
    logger = logging.getLogger(LOGGER_NAME)
    headers = _build_headers(user_agent)
    proxies = {"http": proxy, "https": proxy} if proxy else None

    attempt = 0
    last_exc: Optional[Exception] = None
    while attempt < max_retries:
        attempt += 1
        try:
            logger.debug("GET %s (attempt %d)", url, attempt)
            resp = requests.get(
                url,
                headers=headers,
                timeout=timeout,
                proxies=proxies,
            )
            logger.debug("Received response %s for %s", resp.status_code, url)
            resp.raise_for_status()
            return resp
        except RequestException as exc:
            last_exc = exc
            logger.warning(
                "Request to %s failed on attempt %d/%d: %s",
                url,
                attempt,
                max_retries,
                exc,
            )
            if attempt >= max_retries:
                break
            sleep_for = backoff_factor * (2 ** (attempt - 1))
            logger.debug("Sleeping for %.2f seconds before retrying...", sleep_for)
            time.sleep(sleep_for)

    assert last_exc is not None
    raise last_exc

def load_json_file(path: Path) -> Any:
    """
    Load JSON from file and return the parsed object.
    """
    logger = logging.getLogger(LOGGER_NAME)
    logger.debug("Loading JSON from %s", path)
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)

def save_json_file(path: Path, data: Any) -> None:
    """
    Save data as pretty-printed JSON file, creating directories if necessary.
    """
    logger = logging.getLogger(LOGGER_NAME)
    logger.debug("Saving JSON to %s", path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def safe_get(obj: Dict[str, Any], key: str, default: Any = None) -> Any:
    """
    Helper to safely access dictionary values.
    """
    if not isinstance(obj, dict):
        return default
    return obj.get(key, default)