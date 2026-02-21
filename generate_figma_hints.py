"""
generate_figma_hints.py — Auto-generate figma_hints for design system components.

figma_hints are short substrings that match against Figma layer names during the
DS mapping step. A hint like "btn" will match any layer named "PrimaryBtn",
"icon-btn-outline", etc. They are ONLY needed for `components[]` entries —
not for layout/content/utilities sections, which are CSS utility class doc pages
that don't get matched to Figma nodes.

Usage:
    python generate_figma_hints.py <design_system>
    python generate_figma_hints.py <design_system> --overwrite

    <design_system>  Name matching design_systems/{name}_catalog.json
    --overwrite      Re-generate hints even for components that already have them
                     (default: skip components with non-empty figma_hints)

Example:
    python generate_figma_hints.py primeng
    python generate_figma_hints.py primeng --overwrite
"""

import json
import os
import sys
import time
from typing import Any, Dict, List

from dotenv import load_dotenv
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI

load_dotenv()

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

DS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "design_systems")
LLM_MODEL = "gpt-4o"
SLEEP_BETWEEN_CALLS = 0.3    # seconds between LLM calls
MAX_HINTS = 10               # upper bound on generated hints per component

SYSTEM_PROMPT = """\
You are an expert at Figma design systems and layer naming conventions.
Output ONLY valid JSON — no markdown fences, no explanation."""

USER_TEMPLATE = """\
Design system: {ds_name}
Component name: {name}
Selector: {selector}
Description: {description}

Generate a list of short lowercase substrings that a designer would use when
naming this component's layer in Figma. These are matched case-insensitively
against Figma layer names (e.g., "PrimaryBtn", "icon-btn-small", "CardTile").

Rules:
- 4–{max_hints} hints, ordered by likelihood
- Each hint is 2–20 characters, lowercase, no spaces (use hyphens if needed)
- Include the component's most common names, abbreviations, and variants
- Include synonyms a designer might use (e.g., "cta" for button, "tile" for card)
- Do NOT include generic words like "frame", "group", "layer", "component"
- Do NOT include the design system prefix (e.g., do not include "p-" or "mds-")

Output ONLY a JSON array of strings:
["hint1", "hint2", "hint3"]"""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _load_catalog(design_system: str) -> Dict:
    path = os.path.join(DS_DIR, f"{design_system}_catalog.json")
    if not os.path.exists(path):
        print(f"Error: {path} not found.")
        print(f"Copy design_systems/template_catalog.json to {path} and fill in the fields.")
        sys.exit(1)
    with open(path) as f:
        return json.load(f)


def _save_catalog(design_system: str, catalog: Dict) -> None:
    path = os.path.join(DS_DIR, f"{design_system}_catalog.json")
    with open(path, "w") as f:
        json.dump(catalog, f, indent=2)
    print(f"Saved: {path}")


def _generate_hints_for_component(
    llm: ChatOpenAI,
    ds_name: str,
    component: Dict,
) -> List[str]:
    """Call the LLM to generate figma_hints for one component.

    Returns the list of hints, or the existing hints on failure.
    """
    user_content = USER_TEMPLATE.format(
        ds_name=ds_name,
        name=component.get("name", ""),
        selector=component.get("selector", ""),
        description=component.get("description", ""),
        max_hints=MAX_HINTS,
    )

    try:
        response = llm.invoke([
            SystemMessage(content=SYSTEM_PROMPT),
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

        hints = json.loads(raw)
        if not isinstance(hints, list):
            raise ValueError(f"Expected list, got {type(hints)}")

        # Sanitise: lowercase strings only, strip whitespace
        hints = [str(h).strip().lower() for h in hints if h and str(h).strip()]
        return hints[:MAX_HINTS]

    except json.JSONDecodeError as exc:
        print(f"    Warning: JSON parse error: {exc}")
        return component.get("figma_hints", [])
    except Exception as exc:
        print(f"    Warning: LLM call failed: {exc}")
        return component.get("figma_hints", [])


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def generate_hints(design_system: str, overwrite: bool = False) -> None:
    print(f"\n{'='*60}")
    print(f"Generating figma_hints for: {design_system}")
    print(f"Mode: {'overwrite all' if overwrite else 'skip existing'}")
    print(f"{'='*60}\n")

    catalog = _load_catalog(design_system)
    ds_name = catalog.get("name", design_system)
    components: List[Dict[str, Any]] = catalog.get("components", [])

    if not components:
        print("No components found in catalog.")
        return

    llm = ChatOpenAI(
        model=LLM_MODEL,
        temperature=0.3,   # slight variation produces more diverse synonyms
        api_key=os.getenv("OPENAI_API_KEY"),
    )

    updated = 0
    skipped = 0

    for i, component in enumerate(components):
        name = component.get("name", f"component_{i}")
        existing = component.get("figma_hints", [])

        if existing and not overwrite:
            print(f"  [{i+1}/{len(components)}] {name}: skipped ({len(existing)} existing hints)")
            skipped += 1
            continue

        print(f"  [{i+1}/{len(components)}] {name}: generating...", end=" ", flush=True)
        hints = _generate_hints_for_component(llm, ds_name, component)
        component["figma_hints"] = hints
        print(f"{len(hints)} hints → {hints}")
        updated += 1
        time.sleep(SLEEP_BETWEEN_CALLS)

    _save_catalog(design_system, catalog)

    print(f"\n{'='*60}")
    print(f"Done.  Updated: {updated}  Skipped: {skipped}")
    print(f"{'='*60}\n")


if __name__ == "__main__":
    args = sys.argv[1:]
    if not args:
        print("Usage: python generate_figma_hints.py <design_system> [--overwrite]")
        sys.exit(1)

    ds = args[0]
    overwrite_flag = "--overwrite" in args
    generate_hints(ds, overwrite=overwrite_flag)
