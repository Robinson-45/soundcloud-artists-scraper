import argparse
import json
import logging
from pathlib import Path
from typing import Any, Dict, List

from extractors.soundcloud_parser import SoundCloudArtistScraper
from extractors.utils import load_json_file, setup_logging
from outputs.exporters import export_to_json

def resolve_project_root() -> Path:
    # src/main.py -> src -> project root
    return Path(__file__).resolve().parents[1]

def load_runtime_settings() -> Dict[str, Any]:
    """
    Load optional scraper settings from config/settings.example.json.
    These act as defaults and can be overridden by CLI or input JSON.
    """
    root = resolve_project_root()
    settings_path = root / "src" / "config" / "settings.example.json"
    if not settings_path.exists():
        return {}

    try:
        return load_json_file(settings_path)
    except Exception as exc:  # noqa: BLE001
        logging.getLogger(__name__).warning(
            "Failed to load settings from %s: %s", settings_path, exc
        )
        return {}

def parse_args() -> argparse.Namespace:
    root = resolve_project_root()
    default_input = root / "data" / "input.sample.json"
    default_output = root / "data" / "results.json"

    parser = argparse.ArgumentParser(
        description="SoundCloud Artists Scraper - fetch artist metadata."
    )
    parser.add_argument(
        "-i",
        "--input",
        type=str,
        default=str(default_input),
        help="Path to input JSON file describing profiles/keywords.",
    )
    parser.add_argument(
        "-o",
        "--output",
        type=str,
        default=str(default_output),
        help="Path to output JSON file where scraped artists will be stored.",
    )
    parser.add_argument(
        "--log-level",
        type=str,
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Logging level.",
    )
    return parser.parse_args()

def _normalize_input_config(raw: Dict[str, Any]) -> Dict[str, Any]:
    """
    Normalize and validate the structure of the input config.
    Expected structure:

    {
      "profiles": ["https://soundcloud.com/user1", ...],
      "keywords": ["lofi", "trap", ...],
      "maxItemsPerKeyword": 20
    }
    """
    cfg: Dict[str, Any] = {}
    profiles = raw.get("profiles") or []
    if not isinstance(profiles, list):
        raise ValueError("`profiles` must be a list of SoundCloud profile URLs.")
    cfg["profiles"] = [p for p in profiles if isinstance(p, str) and p.strip()]

    keywords = raw.get("keywords") or []
    if not isinstance(keywords, list):
        raise ValueError("`keywords` must be a list of search keywords.")
    cfg["keywords"] = [k for k in keywords if isinstance(k, str) and k.strip()]

    max_items_per_keyword = raw.get("maxItemsPerKeyword", 20)
    try:
        max_items_per_keyword = int(max_items_per_keyword)
    except (TypeError, ValueError) as exc:  # noqa: PERF203
        raise ValueError("`maxItemsPerKeyword` must be an integer.") from exc
    if max_items_per_keyword <= 0:
        raise ValueError("`maxItemsPerKeyword` must be > 0.")
    cfg["maxItemsPerKeyword"] = max_items_per_keyword

    return cfg

def _deduplicate_artists(artists: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Deduplicate artists by user ID or permalink URL.
    """
    seen_ids = set()
    seen_urls = set()
    result: List[Dict[str, Any]] = []

    for artist in artists:
        artist_id = artist.get("id")
        permalink_url = artist.get("permalink_url")

        key = None
        if artist_id is not None:
            key = ("id", artist_id)
        elif permalink_url:
            key = ("url", permalink_url)

        if key is None:
            # No stable key, just append (these should be rare).
            result.append(artist)
            continue

        if key in seen_ids or key in seen_urls:
            continue

        if key[0] == "id":
            seen_ids.add(key)
        else:
            seen_urls.add(key)

        result.append(artist)

    return result

def main() -> None:
    args = parse_args()
    setup_logging(args.log_level)
    logger = logging.getLogger("soundcloud_scraper")

    input_path = Path(args.input)
    output_path = Path(args.output)

    logger.info("Loading input configuration from %s", input_path)
    if not input_path.exists():
        raise SystemExit(f"Input file not found: {input_path}")

    try:
        raw_input_cfg = load_json_file(input_path)
        input_cfg = _normalize_input_config(raw_input_cfg)
    except Exception as exc:  # noqa: BLE001
        logger.error("Failed to parse input configuration: %s", exc)
        raise SystemExit(1) from exc

    settings = load_runtime_settings()

    scraper = SoundCloudArtistScraper(
        base_url=settings.get("base_url", "https://soundcloud.com"),
        timeout=settings.get("timeout", 15),
        max_retries=settings.get("max_retries", 3),
        backoff_factor=settings.get("backoff_factor", 0.5),
        user_agent=settings.get(
            "user_agent",
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0 Safari/537.36",
        ),
        proxy=settings.get("proxy"),
    )

    all_artists: List[Dict[str, Any]] = []

    # Profiles
    profiles: List[str] = input_cfg["profiles"]
    if profiles:
        logger.info("Fetching %d direct profile(s)...", len(profiles))
        for url in profiles:
            artist = scraper.fetch_artist_profile(url)
            if artist:
                all_artists.append(artist)
            else:
                logger.warning("No artist data extracted for profile: %s", url)

    # Keywords
    keywords: List[str] = input_cfg["keywords"]
    max_items_per_keyword = input_cfg["maxItemsPerKeyword"]
    if keywords:
        logger.info(
            "Searching artists for %d keyword(s) (up to %d per keyword)...",
            len(keywords),
            max_items_per_keyword,
        )
        for keyword in keywords:
            try:
                artists = scraper.search_artists(keyword, max_items=max_items_per_keyword)
                logger.info(
                    "Found %d artist(s) for keyword '%s'", len(artists), keyword
                )
                all_artists.extend(artists)
            except Exception as exc:  # noqa: BLE001
                logger.error(
                    "Error while searching artists for keyword '%s': %s", keyword, exc
                )

    if not all_artists:
        logger.warning("No artists scraped. Nothing to export.")
    else:
        deduped = _deduplicate_artists(all_artists)
        logger.info(
            "Collected %d artists (after deduplication from %d raw records).",
            len(deduped),
            len(all_artists),
        )
        export_to_json(deduped, output_path)
        logger.info("Exported artist data to %s", output_path)

    logger.debug("Exiting normally.")

if __name__ == "__main__":
    main()