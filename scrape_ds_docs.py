"""
scrape_ds_docs.py — One-time Selenium-based documentation scraper.

Renders JavaScript-heavy documentation sites in a real browser, extracts the
main content from every page listed in the catalog (layout/content/utilities
sections and all three URL types per component), and saves each page as a local
Markdown file.

Once saved, both doc_knowledge_builder.py and the runtime agent read these
local files instead of making any network requests — solving the problem of
JavaScript-rendered pages that requests.get() cannot see.

Usage:
    python scrape_ds_docs.py <design_system>
    python scrape_ds_docs.py <design_system> --delay 3      # JS wait seconds (default: 4)
    python scrape_ds_docs.py <design_system> --overwrite    # re-scrape existing files
    python scrape_ds_docs.py <design_system> --components-only  # skip layout/content/utilities
    python scrape_ds_docs.py <design_system> --pages-only       # skip components

Requirements (install once):
    pip install selenium html2text beautifulsoup4

Browser:
    Chrome or Chromium must be installed. Selenium 4.x auto-downloads
    the matching chromedriver via Selenium Manager — no manual setup needed.

Reads:  design_systems/{name}_catalog.json
Writes: design_systems/{name}_docs/
            ├── layout/{slug}.md
            ├── content/{slug}.md
            ├── utilities/{slug}.md
            └── components/{component_name}.md   (overview + api + usage combined)
"""

import os
import sys
import time
from typing import Dict, List, Optional

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

DS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "design_systems")
DEFAULT_JS_WAIT = 4       # seconds to wait after page load for JS to render
DEFAULT_PAGE_DELAY = 1.5  # seconds between page fetches (rate limiting)


# ---------------------------------------------------------------------------
# Browser setup
# ---------------------------------------------------------------------------

def _setup_driver():
    """Create a headless Chrome WebDriver instance.

    Selenium 4.6+ includes Selenium Manager which automatically downloads
    the matching chromedriver — no manual ChromeDriver installation needed
    as long as Chrome/Chromium is installed on the system.
    """
    try:
        from selenium import webdriver
        from selenium.webdriver.chrome.options import Options
    except ImportError:
        print("Error: selenium is not installed. Run: pip install selenium")
        sys.exit(1)

    options = Options()
    options.add_argument("--headless=new")           # headless mode (Chrome 112+)
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-gpu")
    options.add_argument("--window-size=1920,1080")
    options.add_argument("--disable-extensions")
    options.add_argument(
        "--user-agent=Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    )
    # Suppress DevTools / logging noise
    options.add_experimental_option("excludeSwitches", ["enable-logging"])
    options.add_argument("--log-level=3")

    driver = webdriver.Chrome(options=options)
    driver.set_page_load_timeout(30)
    return driver


# ---------------------------------------------------------------------------
# Page scraping
# ---------------------------------------------------------------------------

def _scrape_url(driver, url: str, js_wait: int) -> str:
    """Navigate to URL, wait for JS rendering, return cleaned Markdown text.

    Returns empty string on any failure so the caller can skip gracefully.
    """
    try:
        import html2text
        from bs4 import BeautifulSoup
    except ImportError:
        print("Error: html2text or beautifulsoup4 not installed.")
        print("Run: pip install html2text beautifulsoup4")
        sys.exit(1)

    try:
        driver.get(url)
        time.sleep(js_wait)   # wait for JS to render page content

        html = driver.page_source
        soup = BeautifulSoup(html, "html.parser")

        # Remove boilerplate elements that pollute the text
        for tag in soup.find_all([
            "script", "style", "noscript", "svg", "iframe",
            "nav", "header", "footer", "aside",
        ]):
            tag.decompose()

        # Remove elements commonly used for cookie banners, version selectors, etc.
        for cls in ["cookie", "banner", "version-select", "sidebar", "toc",
                    "breadcrumb", "pagination", "social", "ad-"]:
            for el in soup.find_all(class_=lambda c: c and cls in c.lower()):
                el.decompose()

        # Find the main content area — try common semantic selectors
        main = None
        for selector in [
            "main", "article",
            "[role='main']",
            ".docs-content", ".doc-content", ".content-body",
            ".documentation", ".page-content", ".main-content",
            "#content", "#main", "#docs",
        ]:
            try:
                main = soup.select_one(selector)
                if main:
                    break
            except Exception:
                continue

        if main is None:
            main = soup.find("body") or soup

        # Convert the selected HTML to Markdown
        converter = html2text.HTML2Text()
        converter.ignore_links = False
        converter.ignore_images = True
        converter.ignore_emphasis = False
        converter.body_width = 0          # no hard line wrapping
        converter.protect_links = False
        converter.wrap_links = False

        markdown = converter.handle(str(main)).strip()

        # Collapse 3+ consecutive blank lines → 2
        import re
        markdown = re.sub(r"\n{3,}", "\n\n", markdown)

        return markdown

    except Exception as exc:
        print(f"    Warning: failed to scrape {url}: {exc}")
        return ""


# ---------------------------------------------------------------------------
# Slug helper (must match the slug logic in doc_knowledge_builder.py)
# ---------------------------------------------------------------------------

def _url_to_slug(url: str, title: str) -> str:
    """Derive a filesystem-safe slug from a URL or title."""
    slug = url.rstrip("/").rsplit("/", 1)[-1] if url else ""
    slug = slug.split("#")[0]   # strip anchors (e.g. #api)
    slug = slug or title.lower().replace(" ", "-")
    # Remove characters that are unsafe in filenames
    import re
    slug = re.sub(r"[^\w\-]", "", slug)
    return slug or "page"


# ---------------------------------------------------------------------------
# Section scraping (layout / content / utilities)
# ---------------------------------------------------------------------------

def _scrape_section(
    driver,
    design_system: str,
    section_name: str,
    entries: List[Dict],
    docs_dir: str,
    overwrite: bool,
    js_wait: int,
    page_delay: float,
) -> None:
    section_dir = os.path.join(docs_dir, section_name)
    os.makedirs(section_dir, exist_ok=True)

    for entry in entries:
        title = entry.get("title", "Untitled")
        url   = entry.get("url", "").strip()

        if not url:
            print(f"  [{section_name}] '{title}': no URL — skipping")
            continue

        slug     = _url_to_slug(url, title)
        out_path = os.path.join(section_dir, f"{slug}.md")

        if os.path.exists(out_path) and not overwrite:
            print(f"  [{section_name}] '{title}': already saved — skip (use --overwrite to redo)")
            continue

        print(f"  [{section_name}] '{title}' ← {url}")
        content = _scrape_url(driver, url, js_wait)

        if content:
            header = f"# {title}\nSource: {url}\n\n"
            with open(out_path, "w", encoding="utf-8") as f:
                f.write(header + content)
            print(f"    Saved: {out_path} ({len(content):,} chars)")
        else:
            print(f"    Warning: empty content — file not written")

        time.sleep(page_delay)


# ---------------------------------------------------------------------------
# Component scraping (combines overview + api + usage into one .md)
# ---------------------------------------------------------------------------

def _scrape_component(
    driver,
    component: Dict,
    docs_dir: str,
    overwrite: bool,
    js_wait: int,
    page_delay: float,
) -> None:
    name  = component.get("name", "unknown")
    urls  = component.get("urls", {})

    comp_dir = os.path.join(docs_dir, "components")
    os.makedirs(comp_dir, exist_ok=True)
    out_path = os.path.join(comp_dir, f"{name}.md")

    if os.path.exists(out_path) and not overwrite:
        print(f"  [component] '{name}': already saved — skip")
        return

    sections = []
    doc_types = [
        ("overview", "Overview"),
        ("api",      "API Reference"),
        ("usage",    "Usage Examples"),
    ]

    for doc_type, label in doc_types:
        url = urls.get(doc_type, "").strip()
        if not url:
            continue

        print(f"  [component] '{name}/{doc_type}' ← {url}")
        content = _scrape_url(driver, url, js_wait)

        if content:
            sections.append(f"# {name.title()} — {label}\nSource: {url}\n\n{content}")
            print(f"    OK ({len(content):,} chars)")
        else:
            print(f"    Warning: empty content for {doc_type}")

        time.sleep(page_delay)

    if sections:
        combined = "\n\n---\n\n".join(sections)
        with open(out_path, "w", encoding="utf-8") as f:
            f.write(combined)
        print(f"  Saved: {out_path}")
    else:
        print(f"  [component] '{name}': no URLs configured — skipped")


# ---------------------------------------------------------------------------
# Catalog loader
# ---------------------------------------------------------------------------

def _load_catalog(design_system: str) -> Dict:
    import json
    path = os.path.join(DS_DIR, f"{design_system}_catalog.json")
    if not os.path.exists(path):
        print(f"Error: {path} not found.")
        print(f"Copy design_systems/template_catalog.json to {path} and fill in the fields.")
        sys.exit(1)
    with open(path) as f:
        return json.load(f)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def scrape_docs(
    design_system: str,
    js_wait: int = DEFAULT_JS_WAIT,
    page_delay: float = DEFAULT_PAGE_DELAY,
    overwrite: bool = False,
    components_only: bool = False,
    pages_only: bool = False,
) -> None:
    catalog  = _load_catalog(design_system)
    ds_name  = catalog.get("name", design_system)
    docs_dir = os.path.join(DS_DIR, f"{design_system}_docs")
    os.makedirs(docs_dir, exist_ok=True)

    print(f"\n{'='*60}")
    print(f"Scraping docs for: {ds_name}")
    print(f"Output directory:  {docs_dir}")
    print(f"JS wait:           {js_wait}s per page")
    print(f"Page delay:        {page_delay}s between requests")
    print(f"Overwrite:         {overwrite}")
    print(f"{'='*60}\n")

    driver = _setup_driver()
    print("Browser started (headless Chrome)\n")

    try:
        # ── Layout / content / utilities pages ────────────────────────
        if not components_only:
            for section in ("layout", "content", "utilities"):
                entries = catalog.get(section, [])
                if not entries:
                    continue
                print(f"\n--- Section: {section} ({len(entries)} page(s)) ---")
                _scrape_section(
                    driver, design_system, section, entries,
                    docs_dir, overwrite, js_wait, page_delay,
                )

        # ── Components ────────────────────────────────────────────────
        if not pages_only:
            components = catalog.get("components", [])
            if components:
                print(f"\n--- Components ({len(components)} entries) ---")
                for component in components:
                    _scrape_component(
                        driver, component, docs_dir, overwrite, js_wait, page_delay,
                    )

    finally:
        driver.quit()
        print("\nBrowser closed.")

    print(f"\n{'='*60}")
    print(f"Done. All docs saved to: {docs_dir}")
    print(f"\nNext steps:")
    print(f"  1. Run: python doc_knowledge_builder.py {design_system}")
    print(f"     (reads local .md files → builds knowledge.json)")
    print(f"  2. Run: python run_agent.py {design_system}")
    print(f"{'='*60}\n")


if __name__ == "__main__":
    args = sys.argv[1:]
    if not args:
        print("Usage: python scrape_ds_docs.py <design_system> [options]")
        print("Options:")
        print("  --delay N          Seconds to wait for JS rendering (default: 4)")
        print("  --overwrite        Re-scrape pages that already have local files")
        print("  --components-only  Skip layout/content/utilities sections")
        print("  --pages-only       Skip components")
        sys.exit(1)

    ds = args[0]
    js_wait    = DEFAULT_JS_WAIT
    page_delay = DEFAULT_PAGE_DELAY
    overwrite  = "--overwrite"        in args
    comp_only  = "--components-only"  in args
    pages_only = "--pages-only"       in args

    if "--delay" in args:
        idx = args.index("--delay")
        if idx + 1 < len(args):
            try:
                js_wait = int(args[idx + 1])
            except ValueError:
                print(f"Invalid --delay value: {args[idx + 1]}")
                sys.exit(1)

    scrape_docs(
        design_system=ds,
        js_wait=js_wait,
        page_delay=page_delay,
        overwrite=overwrite,
        components_only=comp_only,
        pages_only=pages_only,
    )
