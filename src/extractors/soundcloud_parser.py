import json
import logging
import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional
from urllib.parse import quote, urljoin

from bs4 import BeautifulSoup

from .utils import LOGGER_NAME, http_get, safe_get

@dataclass
class ScraperConfig:
    base_url: str = "https://soundcloud.com"
    timeout: int = 15
    max_retries: int = 3
    backoff_factor: float = 0.5
    user_agent: Optional[str] = None
    proxy: Optional[str] = None

class SoundCloudArtistScraper:
    """
    High-level scraper for SoundCloud artist profiles and searches.

    The implementation relies on parsing SoundCloud's web pages, specifically
    JSON hydration blocks embedded in script tags. The structure of these
    blocks can evolve over time, so parsing is written to be defensive.
    """

    def __init__(
        self,
        base_url: str = "https://soundcloud.com",
        timeout: int = 15,
        max_retries: int = 3,
        backoff_factor: float = 0.5,
        user_agent: Optional[str] = None,
        proxy: Optional[str] = None,
    ) -> None:
        self.config = ScraperConfig(
            base_url=base_url.rstrip("/"),
            timeout=timeout,
            max_retries=max_retries,
            backoff_factor=backoff_factor,
            user_agent=user_agent,
            proxy=proxy,
        )
        self.logger = logging.getLogger(LOGGER_NAME)

    # -------------------------
    # Public API
    # -------------------------

    def fetch_artist_profile(self, profile_url: str) -> Optional[Dict[str, Any]]:
        """
        Fetch a single artist profile from a SoundCloud user URL.
        """
        normalized_url = self._normalize_profile_url(profile_url)
        self.logger.info("Fetching artist profile: %s", normalized_url)

        resp = http_get(
            normalized_url,
            timeout=self.config.timeout,
            max_retries=self.config.max_retries,
            backoff_factor=self.config.backoff_factor,
            user_agent=self.config.user_agent,
            proxy=self.config.proxy,
        )

        html = resp.text
        hydration = self._extract_hydration_blocks(html)
        user_block = self._find_user_block(hydration)

        if user_block is not None:
            artist = self._normalize_artist(user_block, normalized_url)
            self.logger.debug("Extracted artist from hydration: %s", artist)
            return artist

        # Fallback: minimal info from meta tags.
        self.logger.warning(
            "No hydration user block found for %s, using meta tag fallback.",
            normalized_url,
        )
        soup = BeautifulSoup(html, "html.parser")
        artist = self._parse_from_meta_tags(soup, normalized_url)
        self.logger.debug("Extracted artist from meta tags: %s", artist)
        return artist

    def search_artists(self, keyword: str, max_items: int = 20) -> List[Dict[str, Any]]:
        """
        Search SoundCloud for artists by keyword. Returns a list of artist dicts.
        """
        q = keyword.strip()
        if not q:
            raise ValueError("Keyword cannot be empty.")

        self.logger.info(
            "Searching SoundCloud artists for keyword '%s' (max %d items)",
            q,
            max_items,
        )
        search_url = f"{self.config.base_url}/search/people?q={quote(q)}"
        resp = http_get(
            search_url,
            timeout=self.config.timeout,
            max_retries=self.config.max_retries,
            backoff_factor=self.config.backoff_factor,
            user_agent=self.config.user_agent,
            proxy=self.config.proxy,
        )
        html = resp.text
        hydration = self._extract_hydration_blocks(html)
        user_entries = self._find_user_list(hydration)

        artists: List[Dict[str, Any]] = []

        # Primary attempt: users embedded in hydration.
        for user_data in user_entries:
            if len(artists) >= max_items:
                break
            artist = self._normalize_artist(user_data)
            if artist:
                artists.append(artist)

        if len(artists) >= max_items:
            return artists[:max_items]

        # Secondary attempt: parse profile URLs from search result HTML and fetch individually.
        self.logger.debug(
            "Hydration-based search yielded %d users, using HTML fallback.",
            len(artists),
        )
        soup = BeautifulSoup(html, "html.parser")
        profile_links = self._extract_profile_links_from_search_page(soup)
        for url in profile_links:
            if len(artists) >= max_items:
                break
            try:
                artist = self.fetch_artist_profile(url)
            except Exception as exc:  # noqa: BLE001
                self.logger.warning("Failed to fetch artist %s: %s", url, exc)
                continue
            if artist:
                artists.append(artist)

        return artists[:max_items]

    # -------------------------
    # Internal helpers
    # -------------------------

    def _normalize_profile_url(self, url: str) -> str:
        url = url.strip()
        if not url.startswith("http"):
            url = urljoin(self.config.base_url + "/", url.lstrip("/"))
        return url

    def _extract_hydration_blocks(self, html: str) -> List[Dict[str, Any]]:
        """
        Extract the JSON assigned to window.__sc_hydration, which typically
        contains structured data for the page, including user objects.
        """
        pattern = re.compile(r"window\.__sc_hydration\s*=\s*(\[[^\n]+?\]);", re.DOTALL)
        match = pattern.search(html)
        if not match:
            # Some variants may use longer multi-line JSON; fallback to more lenient search.
            pattern_loose = re.compile(