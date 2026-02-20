"""
doc_scraper.py — On-demand documentation scraper with disk caching.

Fetches documentation URLs for design system components and caches the
extracted text locally so subsequent runs don't require network access.
"""

import hashlib
import os
import re
from html.parser import HTMLParser
from typing import Optional


class HTMLTextExtractor(HTMLParser):
    """Strip HTML and collect human-readable text, skipping boilerplate tags."""

    SKIP_TAGS = {"script", "style", "nav", "header", "footer", "noscript", "svg"}

    def __init__(self):
        super().__init__()
        self._skip_depth: int = 0
        self._parts: list[str] = []

    def handle_starttag(self, tag: str, attrs):
        if tag.lower() in self.SKIP_TAGS:
            self._skip_depth += 1

    def handle_endtag(self, tag: str):
        if tag.lower() in self.SKIP_TAGS and self._skip_depth > 0:
            self._skip_depth -= 1

    def handle_data(self, data: str):
        if self._skip_depth == 0:
            stripped = data.strip()
            if stripped:
                self._parts.append(stripped)

    def get_text(self) -> str:
        raw = " ".join(self._parts)
        # Collapse runs of whitespace
        return re.sub(r"\s+", " ", raw).strip()


class DocScraper:
    """Fetch documentation pages and return plain text (with disk caching).

    Usage::

        scraper = DocScraper()
        text = scraper.fetch("https://primeng.org/button#api")  # cached after first call
    """

    def __init__(self, cache_dir: str = "design_systems/cache"):
        self.cache_dir = cache_dir
        os.makedirs(self.cache_dir, exist_ok=True)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def fetch(self, url: str, max_chars: int = 3000) -> str:
        """Return cached plain-text content for *url*, fetching if needed.

        Returns an empty string on any failure (network error, parse error,
        etc.) so callers can treat it as non-fatal optional context.
        """
        cache_path = self._cache_path(url)

        # Cache hit
        if os.path.exists(cache_path):
            try:
                with open(cache_path, "r", encoding="utf-8") as fh:
                    return fh.read()
            except OSError:
                pass  # fall through to re-fetch

        # Cache miss — fetch
        text = self._fetch_and_strip(url, max_chars)
        if text:
            try:
                with open(cache_path, "w", encoding="utf-8") as fh:
                    fh.write(text)
            except OSError as exc:
                print(f"DocScraper: could not write cache for {url}: {exc}")

        return text

    def clear_cache(self) -> None:
        """Delete all cached files in *cache_dir*."""
        for filename in os.listdir(self.cache_dir):
            if filename.endswith(".txt"):
                try:
                    os.remove(os.path.join(self.cache_dir, filename))
                except OSError:
                    pass
        print(f"DocScraper: cache cleared ({self.cache_dir})")

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _cache_path(self, url: str) -> str:
        key = hashlib.md5(url.encode("utf-8")).hexdigest()
        return os.path.join(self.cache_dir, f"{key}.txt")

    def _fetch_and_strip(self, url: str, max_chars: int) -> str:
        try:
            import requests  # optional dependency; already required by main module

            resp = requests.get(
                url,
                timeout=10,
                headers={"User-Agent": "figma-to-angular-agent/1.0 (doc scraper)"},
            )
            resp.raise_for_status()

            extractor = HTMLTextExtractor()
            extractor.feed(resp.text)
            text = extractor.get_text()

            if len(text) > max_chars:
                text = text[:max_chars]

            return text

        except Exception as exc:
            print(f"DocScraper: failed to fetch {url}: {exc}")
            return ""
