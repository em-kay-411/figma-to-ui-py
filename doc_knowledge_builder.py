"""
doc_knowledge_builder.py — One-time CLI tool to scrape DS documentation pages
and extract CSS utility class knowledge into a structured JSON file.

Usage:
    python doc_knowledge_builder.py <design_system>

Reads:  design_systems/{design_system}_catalog.json
        (sections: layout, content, utilities — each entry has title + url)
Writes: design_systems/{design_system}_knowledge.json

NOTE: Only the `layout`, `content`, and `utilities` sections are scraped.
The `components` section contains component *definitions* (not CSS class pages)
and is handled separately by the pipeline at runtime via fetch_component_docs().

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


def _process_section(
    scraper: DocScraper,
    llm: ChatOpenAI,
    section_name: str,
    entries: List[Dict],
) -> Dict[str, Any]:
    """Scrape + extract classes for all pages in one section.

    Returns a dict keyed by a URL-derived slug for each page entry.
    """
    section_data: Dict[str, Any] = {}

    for entry in entries:
        title = entry.get("title", "Untitled")
        url = entry.get("url", "")
        if not url:
            print(f"  Skipping '{title}' — no URL")
            continue

        # Derive a slug from the URL path (last non-empty segment)
        slug = url.rstrip("/").rsplit("/", 1)[-1] or title.lower().replace(" ", "-")

        print(f"  [{section_name}] {title} → {url}")
        scraped_text = scraper.fetch(url, max_chars=MAX_CHARS_PER_PAGE)

        extracted = _extract_classes_from_page(llm, title, scraped_text)
        time.sleep(SLEEP_BETWEEN_CALLS)

        section_data[slug] = {
            "url": url,
            "title": title,
            "classes": extracted["classes"],
            "examples": extracted["examples"],
        }

    return section_data


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
        section_data = _process_section(scraper, llm, section_name, entries)
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
        print("Usage: python doc_knowledge_builder.py <design_system>")
        print("  e.g. python doc_knowledge_builder.py primeng")
        sys.exit(1)

    build_knowledge(sys.argv[1])
