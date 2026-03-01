"""
doc_knowledge_builder.py — One-time CLI tool to build CSS utility class knowledge.

Usage:
    python doc_knowledge_builder.py <design_system>

Reads:  design_systems/{design_system}_catalog.json  (layout/content/utilities sections)
        design_systems/{design_system}_docs/          (preferred — local .md files from
                                                       scrape_ds_docs.py; no network needed)
Writes: design_systems/{design_system}_knowledge.json

Source priority for each doc page:
  1. Local .md file at design_systems/{name}_docs/{section}/{slug}.md
     (produced by scrape_ds_docs.py — handles JS-rendered sites)
  2. URL fetch via DocScraper (fallback when no local file exists)

NOTE: Only the `layout`, `content`, and `utilities` sections are processed.
The `components` section contains component *definitions* (not CSS class pages)
and is handled at runtime via fetch_component_docs() which also prefers local files.

The knowledge file is consumed at runtime by figma_to_angular_agent.py (Phase 1
utility class research) to let the code-gen LLM use DS utility classes instead
of writing equivalent custom CSS.
"""

import json
import os
import sys
import time
from typing import Any, Dict, List, Optional

from dotenv import load_dotenv
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI

from doc_scraper import DocScraper

load_dotenv()

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

DS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "design_systems")
BUILDER_CACHE_DIR = os.path.join(DS_DIR, "builder_cache")  # separate from runtime cache
LLM_MODEL = "gpt-4o"
MAX_CHARS_PER_PAGE = 50_000   # full content needed to find all class names
SLEEP_BETWEEN_CALLS = 0.5     # rate-limit between LLM calls (seconds)

EXTRACTION_SYSTEM_PROMPT = (
    "You are a CSS utility class extractor. "
    "You read documentation and output ONLY valid JSON — no markdown, no explanation."
)

COMPONENT_METADATA_SYSTEM = (
    "You are an Angular library API expert. "
    "Read documentation and output ONLY valid JSON — no markdown, no explanation."
)

COMPONENT_METADATA_TEMPLATE = """\
Given this Angular component documentation for "{name}" (from package "{import_path}"):

{doc_text}

Extract:
1. The exact TypeScript import statement to include in an Angular component file.
2. The Angular module/class names to add to the @Component imports array.
3. Any attribute directive selectors this module exports (e.g. pButton, pInputText), with a brief description.
4. CSS classes ALWAYS applied to the element regardless of variant (e.g. ["lmn-btn"]).
   Use empty array [] if the component is a custom element tag that handles its own styling.
5. A map from variant/modifier/size name → additional CSS classes for that variant.
   Use lowercase keys matching what a Figma variant value would be (e.g. "primary", "large").
   Use empty object {{}} if variants are communicated via component inputs (e.g. [severity]="primary").
6. A one-sentence inner_html_note describing what, if anything, goes inside the component's tags.
   Examples:
     - "Pass options via [options] input (string[]); no inner tags needed."
     - "Use native <button> elements with mtSegment directive as direct children."
     - "Use ng-content named slots (header, content, footer) with native HTML."
     - "" (empty string) if the component is self-contained with no inner markup.

Return ONLY this JSON:
{{
  "import_statement": "import {{ XModule }} from '{import_path}/x';",
  "component_classes": ["XModule"],
  "directives": [
    {{"selector": "xDirective", "description": "One-line description"}}
  ],
  "base_classes": ["lmn-btn"],
  "variant_class_map": {{
    "primary":   ["lmn-btn-primary"],
    "secondary": ["lmn-btn-secondary"],
    "large":     ["lmn-btn-lg"],
    "small":     ["lmn-btn-sm"]
  }},
  "inner_html_note": "Pass options via [options] input; no inner tags needed."
}}
Rules:
- import_statement: single exact import line for this specific component
- component_classes: array of Angular class name strings only
- directives: attribute selectors used in HTML markup (not component tags)
- If no directives, use []
- base_classes: CSS classes applied to the native HTML element itself (not component inputs)
- variant_class_map: keys lowercase; values are arrays of CSS class strings
- If no base classes, use []
- If no variant classes, use {{}}
- inner_html_note: single sentence; use "" if no inner markup is needed
Output ONLY valid JSON."""

EXTRACTION_USER_TEMPLATE = """\
Given this documentation page about "{title}":

{scraped_text}

Extract ALL CSS utility class names with descriptions.
Return ONLY JSON:
{{
  "classes": {{ "class-name": "12-word max description" }},
  "examples": ["<div class=\\"...\\">...</div>"]
}}
Rules:
- CSS utility classes only (flex, gap-3, text-primary, p-4, shadow-sm, etc.)
- NOT component selectors (p-button, mat-card) — those are in the catalog
- NOT HTML elements (div, span) as class names
- Max 3 short examples
Output ONLY valid JSON, no markdown."""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _load_catalog(design_system: str) -> Dict:
    path = os.path.join(DS_DIR, f"{design_system}_catalog.json")
    if not os.path.exists(path):
        print(f"Error: {path} not found.")
        print(f"Copy design_systems/template_catalog.json to {path} and fill in URLs.")
        sys.exit(1)
    with open(path) as f:
        return json.load(f)


def _save_knowledge(design_system: str, knowledge: Dict) -> str:
    path = os.path.join(DS_DIR, f"{design_system}_knowledge.json")
    with open(path, "w") as f:
        json.dump(knowledge, f, indent=2)
    return path


def _save_catalog(design_system: str, catalog: Dict) -> str:
    path = os.path.join(DS_DIR, f"{design_system}_catalog.json")
    with open(path, "w") as f:
        json.dump(catalog, f, indent=2)
    return path


def _extract_classes_from_page(
    llm: ChatOpenAI,
    title: str,
    scraped_text: str,
) -> Dict[str, Any]:
    """Call the LLM to extract {className: description} pairs from a doc page.

    Returns a dict with keys 'classes' (dict) and 'examples' (list).
    On any failure, returns the empty-result sentinel so the builder continues.
    """
    if not scraped_text.strip():
        print(f"    Skipping '{title}' — empty page content")
        return {"classes": {}, "examples": []}

    user_content = EXTRACTION_USER_TEMPLATE.format(
        title=title,
        scraped_text=scraped_text[:MAX_CHARS_PER_PAGE],
    )

    try:
        response = llm.invoke([
            SystemMessage(content=EXTRACTION_SYSTEM_PROMPT),
            HumanMessage(content=user_content),
        ])
        raw = response.content.strip()

        # Strip accidental markdown fences
        if raw.startswith("```"):
            parts = raw.split("```")
            raw = parts[1] if len(parts) >= 2 else raw
            if raw.startswith("json"):
                raw = raw[4:]
            raw = raw.strip()

        result = json.loads(raw)
        classes = result.get("classes", {})
        examples = result.get("examples", [])

        if not isinstance(classes, dict):
            classes = {}
        if not isinstance(examples, list):
            examples = []

        print(f"    Found {len(classes)} class(es) on '{title}'")
        return {"classes": classes, "examples": examples[:3]}

    except json.JSONDecodeError as exc:
        print(f"    Warning: JSON parse error for '{title}': {exc}")
        return {"classes": {}, "examples": []}
    except Exception as exc:
        print(f"    Warning: LLM call failed for '{title}': {exc}")
        return {"classes": {}, "examples": []}


def _url_to_slug(url: str, title: str) -> str:
    """Derive a slug from a URL — must match the logic in scrape_ds_docs.py."""
    import re
    slug = url.rstrip("/").rsplit("/", 1)[-1] if url else ""
    slug = slug.split("#")[0]
    slug = slug or title.lower().replace(" ", "-")
    slug = re.sub(r"[^\w\-]", "", slug)
    return slug or "page"


def _read_local_page(design_system: str, section_name: str, slug: str) -> Optional[str]:
    """Return content of the local .md file if it exists, else None."""
    path = os.path.join(DS_DIR, f"{design_system}_docs", section_name, f"{slug}.md")
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            return f.read()
    return None


def _process_section(
    scraper: DocScraper,
    llm: ChatOpenAI,
    section_name: str,
    entries: List[Dict],
    design_system: str = "",
) -> Dict[str, Any]:
    """Read + extract CSS classes for all pages in one section.

    For each entry, checks for a local .md file first (from scrape_ds_docs.py).
    Falls back to URL fetch via DocScraper if no local file exists.

    Returns a dict keyed by URL-derived slug for each page entry.
    """
    section_data: Dict[str, Any] = {}

    for entry in entries:
        title = entry.get("title", "Untitled")
        url   = entry.get("url", "").strip()
        slug  = _url_to_slug(url, title)

        # ── Prefer local .md file (produced by scrape_ds_docs.py) ────
        local_text = _read_local_page(design_system, section_name, slug) if design_system else None

        if local_text:
            print(f"  [{section_name}] '{title}': reading local file")
            page_text = local_text
        elif url:
            print(f"  [{section_name}] '{title}': fetching {url}")
            page_text = scraper.fetch(url, max_chars=MAX_CHARS_PER_PAGE)
        else:
            print(f"  Skipping '{title}' — no local file and no URL")
            continue

        extracted = _extract_classes_from_page(llm, title, page_text)
        time.sleep(SLEEP_BETWEEN_CALLS)

        section_data[slug] = {
            "url": url,
            "title": title,
            "classes": extracted["classes"],
            "examples": extracted["examples"],
        }

    return section_data


def _extract_angular_metadata_from_doc(
    llm: ChatOpenAI,
    name: str,
    doc_text: str,
    import_path: str,
) -> Dict[str, Any]:
    """Call LLM to extract import_statement, component_classes, directives.

    Returns a dict with those three keys.
    On failure, returns safe empty values so the caller can continue.
    """
    if not doc_text.strip():
        print(f"    Skipping '{name}' — empty doc content")
        return {"import_statement": "", "component_classes": [], "directives": [], "base_classes": [], "variant_class_map": {}, "inner_html_note": ""}

    user_content = COMPONENT_METADATA_TEMPLATE.format(
        name=name,
        import_path=import_path,
        doc_text=doc_text[:MAX_CHARS_PER_PAGE],
    )
    try:
        response = llm.invoke([
            SystemMessage(content=COMPONENT_METADATA_SYSTEM),
            HumanMessage(content=user_content),
        ])
        raw = response.content.strip()
        if raw.startswith("```"):
            parts = raw.split("```")
            raw = parts[1] if len(parts) >= 2 else raw
            if raw.startswith("json"):
                raw = raw[4:]
            raw = raw.strip()
        result = json.loads(raw)
        return {
            "import_statement": result.get("import_statement", ""),
            "component_classes": result.get("component_classes", []),
            "directives": result.get("directives", []),
            "base_classes": result.get("base_classes", []),
            "variant_class_map": result.get("variant_class_map", {}),
            "inner_html_note": result.get("inner_html_note", ""),
        }
    except json.JSONDecodeError as exc:
        print(f"    Warning: JSON parse error for '{name}': {exc}")
    except Exception as exc:
        print(f"    Warning: LLM call failed for '{name}': {exc}")
    return {"import_statement": "", "component_classes": [], "directives": [], "base_classes": [], "variant_class_map": {}, "inner_html_note": ""}


def enrich_catalog_components(design_system: str, overwrite: bool = False) -> None:
    """Populate import_statement, component_classes, directives for each catalog component.

    Source priority for each component's doc text:
      1. design_systems/{ds}_docs/components/{name}.md  (from scrape_ds_docs.py)
      2. The component's 'api' URL fetched via DocScraper (fallback)

    Skips components that already have import_statement populated unless --overwrite.
    Writes results directly back to {design_system}_catalog.json.
    """
    print(f"\n{'='*60}")
    print(f"Enriching catalog components for: {design_system}")
    print(f"{'='*60}\n")

    catalog = _load_catalog(design_system)
    import_path = catalog.get("import_path", "")

    scraper = DocScraper(cache_dir=BUILDER_CACHE_DIR)
    llm = ChatOpenAI(model=LLM_MODEL, temperature=0.0, api_key=os.getenv("OPENAI_API_KEY"))

    components = catalog.get("components", [])
    updated = skipped = 0

    for comp in components:
        name = comp.get("name", "")
        if not name:
            continue

        if not overwrite and comp.get("import_statement") and "base_classes" in comp and "inner_html_note" in comp:
            print(f"  [{name}] skipped (already populated; use --overwrite to redo)")
            skipped += 1
            continue

        # Prefer local scraped .md (same path scrape_ds_docs.py writes)
        doc_text = _read_local_page(design_system, "components", name)
        if doc_text:
            print(f"  [{name}] reading local component doc")
        else:
            api_url = (comp.get("urls") or {}).get("api") or (comp.get("urls") or {}).get("overview")
            if api_url:
                print(f"  [{name}] fetching {api_url}")
                doc_text = scraper.fetch(api_url, max_chars=MAX_CHARS_PER_PAGE)
            else:
                print(f"  [{name}] skipped — no local file and no URL")
                continue

        metadata = _extract_angular_metadata_from_doc(llm, name, doc_text, import_path)
        time.sleep(SLEEP_BETWEEN_CALLS)

        comp["import_statement"] = metadata["import_statement"]
        comp["component_classes"] = metadata["component_classes"]
        comp["directives"] = metadata["directives"]
        comp["base_classes"] = metadata["base_classes"]
        comp["variant_class_map"] = metadata["variant_class_map"]
        comp["inner_html_note"] = metadata["inner_html_note"]
        updated += 1
        print(f"    → {comp['import_statement']} | base_classes: {comp['base_classes']} | inner: '{comp['inner_html_note']}'")

    _save_catalog(design_system, catalog)
    print(f"\nEnriched {updated} component(s), skipped {skipped}")
    print(f"Catalog saved: design_systems/{design_system}_catalog.json\n")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def build_knowledge(design_system: str) -> None:
    print(f"\n{'='*60}")
    print(f"Building knowledge base for: {design_system}")
    print(f"{'='*60}\n")

    catalog = _load_catalog(design_system)
    ds_name = catalog.get("name", design_system)

    scraper = DocScraper(cache_dir=BUILDER_CACHE_DIR)
    llm = ChatOpenAI(
        model=LLM_MODEL,
        temperature=0.0,
        api_key=os.getenv("OPENAI_API_KEY"),
    )

    # Only scrape layout / content / utilities — these are CSS utility class pages.
    # `components` entries are component definitions, not utility class pages;
    # they are fetched on demand at runtime via fetch_component_docs().
    valid_sections = ("layout", "content", "utilities")
    knowledge_sections: Dict[str, Any] = {}

    total_classes = 0
    total_pages = 0

    for section_name in valid_sections:
        entries = catalog.get(section_name, [])
        if not entries:
            continue

        print(f"\nProcessing section: {section_name} ({len(entries)} page(s))")
        section_data = _process_section(scraper, llm, section_name, entries, design_system)
        knowledge_sections[section_name] = section_data

        section_classes = sum(len(v.get("classes", {})) for v in section_data.values())
        total_classes += section_classes
        total_pages += len(section_data)
        print(f"  → {len(section_data)} page(s), {section_classes} class(es)")

    knowledge: Dict[str, Any] = {
        "name": ds_name,
        "built_from": f"{design_system}_catalog.json",
        "sections": knowledge_sections,
    }

    output_path = _save_knowledge(design_system, knowledge)
    print(f"\n{'='*60}")
    print(f"Knowledge base saved: {output_path}")
    print(f"Total pages processed: {total_pages}")
    print(f"Total utility classes: {total_classes}")
    print(f"{'='*60}\n")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python doc_knowledge_builder.py <design_system> [--enrich-catalog] [--all] [--overwrite]")
        print("  default            Build utility class knowledge ({ds}_knowledge.json)")
        print("  --enrich-catalog   Populate import/directive metadata in {ds}_catalog.json")
        print("  --all              Run both modes")
        print("  --overwrite        Re-extract even if fields already exist")
        sys.exit(1)

    design_system = sys.argv[1]
    flags = set(sys.argv[2:])
    overwrite = "--overwrite" in flags

    if "--enrich-catalog" in flags:
        enrich_catalog_components(design_system, overwrite=overwrite)
    elif "--all" in flags:
        build_knowledge(design_system)
        enrich_catalog_components(design_system, overwrite=overwrite)
    else:
        build_knowledge(design_system)  # unchanged default behaviour
