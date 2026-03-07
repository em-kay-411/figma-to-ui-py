# figma_to_angular_agent.py — Web-Catalog DS Mapper
#
# Changes from previous version:
#   - Replaced compodoc-based flow with user-curated catalog JSON
#   - TEXT nodes always use native HTML (h1–h6, p) — decided deterministically
#   - Component matching uses scored hints from catalog entries
#   - Ambiguous nodes resolved with a single batch LLM call
#   - Only 1–2 LLM calls per run (IR gen + optional repair)

from typing import TypedDict, List, Dict, Any, Optional, Literal
from dataclasses import dataclass, field
import json
import os
import re
import base64
import requests
import time
import threading
import concurrent.futures
from enum import Enum

from langchain_core.messages import HumanMessage, AIMessage, SystemMessage, ToolMessage
from langchain_core.tools import tool as lc_tool
from langchain_openai import ChatOpenAI
from langgraph.graph import StateGraph, END
from pydantic import BaseModel, Field
from dotenv import load_dotenv

from doc_scraper import DocScraper

# Load environment variables from .env file
load_dotenv()

# Standard HTML element names — used to detect directive-based DS components
# (catalog entries whose selector is a native tag, e.g. "button" or "input")
_NATIVE_HTML_TAGS: frozenset = frozenset({
    "a", "abbr", "address", "article", "aside", "audio", "b", "blockquote",
    "br", "button", "canvas", "caption", "cite", "code", "col", "colgroup",
    "data", "datalist", "dd", "del", "details", "dfn", "dialog", "div", "dl",
    "dt", "em", "embed", "fieldset", "figcaption", "figure", "footer", "form",
    "h1", "h2", "h3", "h4", "h5", "h6", "header", "hr", "i", "iframe", "img",
    "input", "ins", "kbd", "label", "legend", "li", "main", "map", "mark",
    "menu", "meter", "nav", "ol", "optgroup", "option", "output", "p",
    "picture", "pre", "progress", "q", "s", "samp", "section", "select",
    "small", "source", "span", "strong", "sub", "summary", "sup", "table",
    "tbody", "td", "template", "textarea", "tfoot", "th", "thead", "time",
    "tr", "u", "ul", "video",
})

# Delimiters used in Figma layer names to separate component/variant tokens
# e.g.  "Button / Primary / Large"  →  ["Button", "Primary", "Large"]
#        "btn-primary-lg"            →  ["btn", "primary", "lg"]
_VARIANT_TOKEN_RE = re.compile(r'[/\-_\s,|]+')

# ============================================================================
# CONFIGURATION & PREREQUISITES
# ============================================================================

class Config:
    LLM_MODEL = "gpt-4o"
    LLM_TEMPERATURE = 0.1
    MAX_REPAIR_ATTEMPTS = 2
    OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
    # Chunk size for processing large Figma trees (in nodes)
    MAX_NODES_PER_CHUNK = 50
    # Maximum JSON size per LLM call (characters)
    MAX_JSON_SIZE = 100000
    # Enable screenshot analysis for better styling
    USE_SCREENSHOT_ANALYSIS = True
    # Path to design system mappings directory
    DS_MAPPINGS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "design_systems")
    # Optimization flags
    ENABLE_TREE_PRUNING = True   # Optimization B: prune structural noise before IR generation
    TIER23_BATCH_SIZE = 50       # Max nodes per parallel LLM sub-batch (Tier 2+3)
    FAST_MODE = False            # Optimization D: lower thresholds, larger chunks, no Tier 2+3 LLM


# ============================================================================
# DESIGN SYSTEM CATALOG FUNCTIONS
# ============================================================================

def load_ds_catalog(design_system: str) -> Optional[Dict]:
    """Load design_systems/{design_system}_catalog.json (catalog format).

    Args:
        design_system: Design system name (e.g., 'primeng').
                       Maps to design_systems/{design_system}_catalog.json.

    Returns:
        Parsed catalog dict, or None if the file doesn't exist.
    """
    path = os.path.join(Config.DS_MAPPINGS_DIR, f"{design_system}_catalog.json")
    if os.path.exists(path):
        with open(path) as f:
            return json.load(f)
    return None


def _build_ds_catalog_from_catalog(catalog: Dict) -> Dict:
    """Build the DS_CATALOG structure from a catalog JSON for use in validation."""
    components = {}
    for c in catalog.get("components", []):
        components[c["selector"]] = {
            "name": c["name"],
            "selector": c["selector"],
            "inputs": {},
            "outputs": {},
            "description": c.get("description", ""),
        }
    return {"components": components, "directives": {}, "modules": {}}


def build_catalog_code_gen_prompt(catalog: Dict) -> str:
    """Build the component mapping guide section for code generation from a catalog."""
    name = catalog.get("name", "Design System")
    custom_tags: List[str] = []
    directive_lines: List[str] = []
    component_lines: List[str] = []

    for c in catalog.get("components", []):
        selector = c["selector"]
        desc = c.get("description", "")[:80]
        hints = ", ".join(c.get("figma_hints", [c["name"]]))
        dir_selectors = [d["selector"] for d in c.get("directives", []) if d.get("selector")]
        dir_note = f" [directives: {', '.join(dir_selectors)}]" if dir_selectors else ""
        note = c.get("inner_html_note", "")
        inner_note = f" | inner: {note}" if note else ""

        if selector.lower() in _NATIVE_HTML_TAGS:
            # Directive-based: native HTML element + DS directives/classes
            directive_lines.append(
                f"- {hints} → <{selector}> (native HTML + {dir_note.strip()} + resolved_classes) ({desc}){inner_note}"
            )
        else:
            custom_tags.append(selector)
            component_lines.append(f"- {hints} → <{selector}> ({desc}){dir_note}{inner_note}")

    lines = [f"{name.upper()} COMPONENT MAPPING GUIDE - Use these aggressively:"]
    lines.extend(component_lines)

    if directive_lines:
        lines.append("")
        lines.append("DIRECTIVE-BASED COMPONENTS (use native HTML tag + directives from resolved_directives):")
        lines.extend(directive_lines)

    # Whitelist: the ONLY non-HTML custom element tags allowed
    if custom_tags:
        lines.append("")
        lines.append(f"ALLOWED CUSTOM ELEMENT TAGS — COMPLETE WHITELIST (every {name} tag you may use):")
        lines.append("  " + "  ".join(f"<{t}>" for t in custom_tags))
        lines.append("DO NOT use any other custom element tag. NEVER invent child tags like")
        lines.append("<mt-option>, <mt-segment>, <p-item>, <ds-child>, or any unlisted variant.")

    return "\n".join(lines)


def build_ds_enforcement_system_prompt(
    catalog: Dict,
    design_system: str,
    intent_category: Optional[str] = None,
) -> str:
    """Build a shared DS enforcement block for codegen, refine, and repair prompts."""
    ds_name = catalog.get("name", design_system)
    components = catalog.get("components", [])
    ds_prefix = catalog.get("prefix", "")

    all_selectors = [c["selector"] for c in components if c.get("selector")]
    custom_selectors = [s for s in all_selectors if s.lower() not in _NATIVE_HTML_TAGS]

    selector_list = "\n".join(f"  - {s}" for s in all_selectors) or "  (none)"
    custom_list = "  ".join(f"<{s}>" for s in custom_selectors) if custom_selectors else "(none)"

    intent_rule = ""
    if intent_category == "DATA_LOGIC_BEHAVIOR":
        intent_rule = (
            "\n### Intent-specific rule:\n"
            "Only modify TypeScript logic. Do NOT touch the HTML template or SCSS."
        )
    elif intent_category == "VISUAL_STYLE":
        intent_rule = (
            "\n### Intent-specific rule:\n"
            "Focus on SCSS and utility class changes. Preserve component structure and selectors."
        )
    elif intent_category == "LAYOUT_STRUCTURAL":
        intent_rule = (
            "\n### Intent-specific rule:\n"
            "Focus on layout properties (flex, grid, padding, gap). Keep component selectors unchanged."
        )
    elif intent_category == "ACCESSIBILITY_PROPERTY":
        intent_rule = (
            "\n### Intent-specific rule:\n"
            "Add ARIA attributes and semantic HTML improvements. Do not change visual output."
        )

    prefix_note = f"- NEVER invent child tags like <{ds_prefix}-item>, <{ds_prefix}-option> (unless listed above)\n" if ds_prefix else ""

    return f"""## DESIGN SYSTEM ENFORCEMENT RULES (Mandatory)

You are working with the {ds_name} design system. The following rules are NON-NEGOTIABLE:

### Allowed component selectors (exhaustive list):
{selector_list}

### Custom element tag whitelist:
{custom_list}

### FORBIDDEN patterns:
- NEVER emit a custom element tag not in the whitelist above
- NEVER use style="..." inline attributes for typography or layout
- NEVER use native <select>, <input type="date">, <button> where a DS equivalent exists
{prefix_note}
### SCSS rules:
- Write ONLY layout-level properties: flex, grid, padding, gap, margin, background-color, border, box-shadow
- Typography → use utility classes or leave to DS default styles
- Pixel overrides ONLY when no DS utility class exists
{intent_rule}"""


def build_catalog_import_example(catalog: Dict) -> str:
    """Build the import example code block from a catalog."""
    example = catalog.get("component_example", {})
    imports_example = example.get("imports_example", "// Import design system modules as needed")
    decorator_imports = example.get("decorator_imports", "CommonModule, ...")
    prefix = catalog.get("prefix", "ds")
    name = catalog.get("name", "DesignSystem")
    return f"""```typescript
import {{ Component, ChangeDetectionStrategy }} from '@angular/core';
import {{ CommonModule }} from '@angular/common';
// Import ALL {name} modules that you use in the template
{imports_example}
// ... etc for every {prefix}-* element used

@Component({{
  selector: 'app-component-name',
  standalone: true,
  imports: [{decorator_imports}],
  templateUrl: './component-name.component.html',
  styleUrls: ['./component-name.component.scss'],
  changeDetection: ChangeDetectionStrategy.OnPush
}})
export class ComponentNameComponent {{ }}
```"""


# ============================================================================
# TEXT NODE CLASSIFICATION (deterministic, no LLM)
# ============================================================================

def classify_figma_text_as_html(figma_node: Dict) -> str:
    """Return the HTML heading/paragraph tag for a TEXT Figma node.

    Decision is based on fontSize + fontWeight from the node's textStyle.
    No LLM involved — fully deterministic.
    """
    ts = figma_node.get("styling", {}).get("textStyle", {})
    fs = ts.get("fontSize", 14)
    fw = ts.get("fontWeight", 400)
    if fs >= 48 or (fs >= 36 and fw >= 700):
        return "h1"
    if fs >= 32 or (fs >= 24 and fw >= 700):
        return "h2"
    return "p"   # h3/h4/h5 gone — utility classes drive the visual size


def _build_figma_node_lookup(figma_node: Dict, lookup: Optional[Dict] = None) -> Dict:
    """Recursively build a flat {node_id: node} lookup from the Figma tree."""
    if lookup is None:
        lookup = {}
    node_id = figma_node.get("id")
    if node_id:
        lookup[node_id] = figma_node
    for child in figma_node.get("children", []):
        _build_figma_node_lookup(child, lookup)
    return lookup


def _build_parent_name_map(nodes: List[Dict], parent_name: str = "", result: Optional[Dict] = None) -> Dict:
    """Recursively build a {node_id → parent_name} map for every node in the IR tree."""
    if result is None:
        result = {}
    for n in nodes:
        result[n.get("id", "")] = parent_name
        _build_parent_name_map(n.get("children", []), n.get("name", ""), result)
    return result


def _infer_native_html_tag(ir_node: Dict) -> str:
    """Return a sensible native HTML tag for an IR node that has no DS component match."""
    type_map = {
        "container": "div",
        "header": "header",
        "footer": "footer",
        "nav": "nav",
        "list": "ul",
        "form": "form",
        "image": "img",
        "divider": "hr",
        "button": "button",
        "link": "a",
        "text": "p",
        "card": "div",
        "toolbar": "div",
    }
    return type_map.get(ir_node.get("type", ""), "div")


# ============================================================================
# CATALOG-BASED COMPONENT SCORING
# ============================================================================

def _score_catalog_match(
    ir_node: Dict,
    figma_node_name: str,
    entry: Dict,
    figma_orig_node: Optional[Dict] = None,
) -> int:
    """Return a 0–100 score for how well a catalog entry matches this IR node.

    Signals (cumulative, capped at 100):
      +70  exact word-boundary hit in layer name          (very strong)
      +45  substring hit in layer name                    (strong; only if no exact hit)
      +25  IR semantic type maps to this entry name       (existing)
      +15  Figma node type (INSTANCE/FRAME/…) in entry's figma_node_types
      + 5  node is a Figma INSTANCE (likely a real component)
      +15  any child node's name contains a catalog hint  (structural context)
    """
    score = 0
    ir_type = ir_node.get("type", "")
    name_lower = figma_node_name.lower()
    hints = entry.get("figma_hints", [])

    # ── Hint matching — word boundary beats substring ─────────────────────
    exact_hit = False
    for hint in hints:
        hl = hint.lower()
        if re.search(rf"\b{re.escape(hl)}\b", name_lower):
            score += 70
            exact_hit = True
            break
    if not exact_hit:
        for hint in hints:
            if hint.lower() in name_lower:
                score += 45
                break

    # ── IR semantic type alignment ─────────────────────────────────────────
    _type_to_entry = {
        "button": "button", "card": "card", "input": "inputtext",
        "checkbox": "checkbox", "radio": "radiobutton", "toggle": "toggleswitch",
        "tab": "tabs", "table": "table", "dialog": "dialog", "chip": "chip",
        "badge": "badge", "icon": "icon", "avatar": "avatar", "divider": "divider",
        "select": "select", "slider": "slider", "progress": "progressbar",
    }
    if _type_to_entry.get(ir_type) == entry.get("name"):
        score += 25

    # ── Figma node type alignment ──────────────────────────────────────────
    if figma_orig_node:
        figma_type = figma_orig_node.get("type", "")
        allowed = entry.get("figma_node_types", [])
        if figma_type and allowed and figma_type in allowed:
            score += 15
        if figma_type == "INSTANCE":
            score += 5   # INSTANCE nodes are almost certainly component instances

    # ── Children hint analysis ─────────────────────────────────────────────
    children = ir_node.get("children", [])
    if children:
        children_text = " ".join(c.get("name", "").lower() for c in children[:10])
        for hint in hints:
            if hint.lower() in children_text:
                score += 15
                break

    return min(score, 100)


def _resolve_ambiguous_with_llm(ambiguous_nodes: List[Dict], catalog_entries: List[Dict]) -> List[Dict]:
    """Single batch LLM call to resolve ambiguous node → component mappings.

    Returns a list of mapping dicts (same shape as definite_mappings entries).
    Falls back to native HTML on any failure.
    """
    if not ambiguous_nodes:
        return []

    llm = ChatOpenAI(model=Config.LLM_MODEL, temperature=0.0, api_key=Config.OPENAI_API_KEY)

    catalog_summary = [
        {
            "name": c["name"],
            "selector": c["selector"],
            "description": c.get("description", ""),
            "hints": c.get("figma_hints", []),
        }
        for c in catalog_entries
    ]

    nodes_payload = [
        {
            "id":          item["ir_node"].get("id"),
            "figma_type":  item.get("figma_type", ""),
            "ir_type":     item["ir_node"].get("type"),
            "name":        item["ir_node"].get("name"),
            "parent_name": item.get("parent_name", ""),
            "children": [
                {"name": c.get("name"), "type": c.get("type")}
                for c in item["ir_node"].get("children", [])[:8]
            ],
            "inner_text":  (item["ir_node"].get("properties") or {}).get("innerText", "")[:100],
            "has_shadow":  bool(item["ir_node"].get("styling", {}).get("effects")),
            "has_fill":    bool(item["ir_node"].get("styling", {}).get("fills")),
            "candidates": [
                {
                    "score":       s,
                    "name":        e["name"],
                    "selector":    e["selector"],
                    "description": e.get("description", ""),
                    "hints":       e.get("figma_hints", []),
                }
                for s, e in item["top3"]
            ],
        }
        for item in ambiguous_nodes
    ]

    prompt = f"""You are mapping Figma design nodes to design system components.

Available components:
{json.dumps(catalog_summary, indent=2)}

For each node below, choose the best matching component selector, or use a plain
HTML tag ("div", "section", "ul", etc.) if nothing fits.

Return a JSON array — one object per node:
[{{"id": "<node_id>", "ds_component": "<component_name_or_native>", "ds_selector": "<selector_or_html_tag>", "reasoning": "<one sentence>"}}]

Nodes to resolve:
{json.dumps(nodes_payload, indent=2)}

Output ONLY valid JSON array, no markdown fences or explanation."""

    try:
        METRICS.record_llm_call(len(prompt), 0, "ambiguous_resolution")
        response = llm.invoke([
            SystemMessage(content="You map UI nodes to design system components. Output ONLY valid JSON."),
            HumanMessage(content=prompt),
        ])
        # Update the metrics entry with actual output size
        if METRICS.log_trace:
            METRICS.log_trace[-1]["message"] += f" / {len(response.content)} out chars"

        resolutions = _parse_llm_json_response(response.content)
        if isinstance(resolutions, list):
            return [
                {
                    "figma_node_id": r.get("id", ""),
                    "ds_component": r.get("ds_component", "native"),
                    "ds_selector": r.get("ds_selector", "div"),
                    "inputs": {},
                }
                for r in resolutions
            ]
    except Exception as exc:
        print(f"Warning: Ambiguous node LLM resolution failed: {exc}")

    # Fallback: native HTML for all ambiguous nodes
    return [
        {
            "figma_node_id": item["ir_node"].get("id", ""),
            "ds_component": "native",
            "ds_selector": _infer_native_html_tag(item["ir_node"]),
            "inputs": {},
        }
        for item in ambiguous_nodes
    ]


def _resolve_uncertain_nodes_parallel(
    uncertain_nodes: List[Dict],
    catalog_entries: List[Dict],
) -> List[Dict]:
    """Resolve Tier 2 + Tier 3 nodes via parallel batched LLM calls.

    Splits uncertain_nodes into TIER23_BATCH_SIZE sub-batches and runs them
    concurrently. Each thread creates its own ChatOpenAI instance so there is
    no shared mutable state between workers (only METRICS, which is lock-protected).
    """
    if not uncertain_nodes:
        return []

    batch_size = Config.TIER23_BATCH_SIZE
    batches = [uncertain_nodes[i:i + batch_size]
               for i in range(0, len(uncertain_nodes), batch_size)]
    max_workers = min(8, len(batches))
    print(f"  Resolving {len(uncertain_nodes)} nodes: {len(batches)} sub-batch(es), "
          f"{max_workers} worker(s)")

    results_by_idx: Dict[int, List[Dict]] = {}

    def process_batch(idx: int, batch: List[Dict]) -> tuple:
        try:
            return idx, _resolve_ambiguous_with_llm(batch, catalog_entries)
        except Exception as exc:
            print(f"  Warning: sub-batch {idx+1} failed ({exc}), using native fallback")
            return idx, [
                {
                    "figma_node_id": item["ir_node"].get("id", ""),
                    "ds_component": "native",
                    "ds_selector": _infer_native_html_tag(item["ir_node"]),
                    "inputs": {},
                }
                for item in batch
            ]

    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {
            executor.submit(process_batch, idx, batch): idx
            for idx, batch in enumerate(batches)
        }
        for future in concurrent.futures.as_completed(futures):
            idx, resolved = future.result()
            results_by_idx[idx] = resolved

    # Reconstruct in original order
    ordered: List[Dict] = []
    for i in range(len(batches)):
        ordered.extend(results_by_idx.get(i, []))
    return ordered


# ============================================================================
# PIPELINE METRICS & LOGGING
# ============================================================================

class PipelineMetrics:
    """Tracks LLM call counts, character usage, step timings, and log trace."""

    def __init__(self):
        self._lock = threading.Lock()
        self.reset()

    def reset(self):
        self.llm_calls: int = 0
        self.llm_total_input_chars: int = 0
        self.llm_total_output_chars: int = 0
        self.step_timings: Dict[str, Dict[str, float]] = {}
        self.log_trace: List[Dict[str, Any]] = []

    def record_llm_call(self, input_chars: int = 0, output_chars: int = 0, step: str = ""):
        with self._lock:
            self.llm_calls += 1
            self.llm_total_input_chars += input_chars
            self.llm_total_output_chars += output_chars
            self._log(step or "llm_call", f"LLM call #{self.llm_calls}: {input_chars} in / {output_chars} out chars")

    def start_step(self, step_name: str):
        self.step_timings[step_name] = {"start": time.time(), "end": 0.0}
        self._log(step_name, "Step started")

    def end_step(self, step_name: str):
        if step_name in self.step_timings:
            self.step_timings[step_name]["end"] = time.time()
            elapsed = self.step_timings[step_name]["end"] - self.step_timings[step_name]["start"]
            self._log(step_name, f"Step completed in {elapsed:.2f}s")

    def _log(self, step: str, message: str):
        # Callers that already hold self._lock may call this directly.
        # Callers that do NOT hold the lock must acquire it first.
        self.log_trace.append({
            "step": step,
            "message": message,
            "timestamp": time.time(),
        })

    def summary(self) -> str:
        lines = [
            "",
            "=" * 60,
            "PIPELINE METRICS SUMMARY",
            "=" * 60,
            f"Total LLM calls:        {self.llm_calls}",
            f"Total input chars:      {self.llm_total_input_chars:,}",
            f"Total output chars:     {self.llm_total_output_chars:,}",
            "",
            "Step Timings:",
        ]
        for step_name, t in self.step_timings.items():
            elapsed = t["end"] - t["start"] if t["end"] else time.time() - t["start"]
            lines.append(f"  {step_name:30s}  {elapsed:8.2f}s")

        total_time = sum(
            t["end"] - t["start"] for t in self.step_timings.values() if t["end"]
        )
        lines.append(f"  {'TOTAL':30s}  {total_time:8.2f}s")

        lines.append("")
        lines.append("Log Trace:")
        for entry in self.log_trace:
            lines.append(f"  [{entry['step']}] {entry['message']}")
        lines.append("=" * 60)
        return "\n".join(lines)


METRICS = PipelineMetrics()


# ============================================================================
# SCREENSHOT ANALYSIS
# ============================================================================

def fetch_image_as_base64(source: str) -> Optional[str]:
    """Fetch an image from URL or local file path and convert to base64."""
    try:
        if os.path.isfile(source):
            print(f"Loading image from local file: {source}")
            with open(source, "rb") as f:
                image_data = base64.b64encode(f.read()).decode("utf-8")
            ext = os.path.splitext(source)[1].lower()
            if ext in [".jpg", ".jpeg"]:
                return f"data:image/jpeg;base64,{image_data}"
            elif ext == ".gif":
                return f"data:image/gif;base64,{image_data}"
            elif ext == ".webp":
                return f"data:image/webp;base64,{image_data}"
            else:
                return f"data:image/png;base64,{image_data}"

        elif source.startswith("http://") or source.startswith("https://"):
            print(f"Fetching image from URL: {source[:50]}...")
            response = requests.get(source, timeout=30)
            response.raise_for_status()
            image_data = base64.b64encode(response.content).decode("utf-8")
            content_type = response.headers.get("content-type", "image/png")
            if "jpeg" in content_type or "jpg" in content_type:
                return f"data:image/jpeg;base64,{image_data}"
            elif "gif" in content_type:
                return f"data:image/gif;base64,{image_data}"
            elif "webp" in content_type:
                return f"data:image/webp;base64,{image_data}"
            else:
                return f"data:image/png;base64,{image_data}"
        else:
            print(f"Warning: Invalid image source: {source}")
            return None

    except Exception as e:
        print(f"Warning: Failed to load image from {source}: {e}")
        return None


def analyze_screenshot_for_styling(image_url: str, design_context: str) -> Optional[Dict]:
    """Use GPT-4 Vision to analyze a screenshot and extract styling details."""
    if not Config.USE_SCREENSHOT_ANALYSIS:
        return None

    image_data = fetch_image_as_base64(image_url)
    if not image_data:
        return None

    llm = ChatOpenAI(model="gpt-4o", temperature=0.1, max_tokens=2000)

    analysis_prompt = """Analyze this Figma design screenshot and provide detailed CSS styling recommendations.

Focus on:
1. **Colors**: Exact background colors, text colors, accent colors (provide hex codes)
2. **Spacing**: Estimate padding, margins, gaps between elements (in pixels)
3. **Typography**: Font sizes, weights, line heights for different text elements
4. **Layout**: Flexbox/grid patterns, alignment, spacing distribution
5. **Visual effects**: Shadows, borders, border-radius, gradients
6. **Overall style**: Is it minimal, bold, card-based, etc.?

Design context from Figma JSON:
{context}

Provide your analysis as a JSON object with this structure:
{{
  "colors": {{
    "background": "#...",
    "primary": "#...",
    "secondary": "#...",
    "text": "#...",
    "textSecondary": "#..."
  }},
  "spacing": {{
    "containerPadding": "..px",
    "sectionGap": "..px",
    "elementGap": "..px"
  }},
  "typography": {{
    "headingLarge": {{"fontSize": "..px", "fontWeight": "..", "lineHeight": ".."}},
    "headingMedium": {{"fontSize": "..px", "fontWeight": "..", "lineHeight": ".."}},
    "body": {{"fontSize": "..px", "fontWeight": "..", "lineHeight": ".."}}
  }},
  "effects": {{
    "borderRadius": "..px",
    "shadow": "...",
    "borders": "..."
  }},
  "layout": {{
    "mainDirection": "row|column",
    "contentWidth": "..px",
    "alignment": "..."
  }}
}}

Output ONLY the JSON object, no markdown or explanation."""

    try:
        print("Analyzing screenshot with GPT-4 Vision...")
        messages = [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": analysis_prompt.format(context=design_context[:2000])},
                    {"type": "image_url", "image_url": {"url": image_data, "detail": "high"}},
                ],
            }
        ]

        response = llm.invoke(messages)
        METRICS.record_llm_call(len(design_context[:2000]), len(response.content), "screenshot_analysis")
        content = response.content.strip()

        if content.startswith("```"):
            content = content.split("```")[1]
            if content.startswith("json"):
                content = content[4:]
            content = content.strip()

        styling_analysis = json.loads(content)
        print("Screenshot analysis complete.")
        return styling_analysis

    except Exception as e:
        print(f"Warning: Screenshot analysis failed: {e}")
        return None


# ============================================================================
# DATA MODELS
# ============================================================================

class LayoutType(str, Enum):
    FLEX_ROW = "flex-row"
    FLEX_COLUMN = "flex-column"
    GRID = "grid"
    ABSOLUTE = "absolute"
    STACK = "stack"


class IRNodeType(str, Enum):
    CONTAINER = "container"
    TEXT = "text"
    BUTTON = "button"
    INPUT = "input"
    IMAGE = "image"
    ICON = "icon"
    CARD = "card"
    LIST = "list"
    TOOLBAR = "toolbar"
    DIVIDER = "divider"
    CHIP = "chip"
    BADGE = "badge"
    TAB = "tab"
    MENU = "menu"
    DIALOG = "dialog"
    FORM_FIELD = "form-field"
    SELECT = "select"
    CHECKBOX = "checkbox"
    RADIO = "radio"
    TOGGLE = "toggle"
    SLIDER = "slider"
    PROGRESS = "progress"
    STEPPER = "stepper"
    TABLE = "table"
    EXPANSION_PANEL = "expansion-panel"
    SIDENAV = "sidenav"
    NAV = "nav"
    HEADER = "header"
    FOOTER = "footer"
    LINK = "link"
    AVATAR = "avatar"
    FORM = "form"
    UNKNOWN = "unknown"


@dataclass
class IRNode:
    id: str
    type: IRNodeType
    name: str
    layout: LayoutType
    properties: Dict[str, Any]
    children: List["IRNode"]
    constraints: Dict[str, Any]
    styling: Dict[str, Any]


class GeneratedFile(BaseModel):
    path: str
    content: str
    file_type: str


class DSComponentMapping(BaseModel):
    figma_node_id: str
    ds_component: str
    ds_selector: str
    inputs: Dict[str, str] = {}
    outputs: Dict[str, str] = {}
    children_slot: Optional[str] = None


class GeneratedAngularArtifact(BaseModel):
    component_name: str
    files: List[GeneratedFile]
    ds_components_used: List[DSComponentMapping]
    imports: List[str]
    unresolved_nodes: List[Dict[str, str]] = []


class ValidationError(BaseModel):
    file_path: str
    error_type: str
    message: str
    line: Optional[int] = None
    suggestion: Optional[str] = None


# ============================================================================
# STATE DEFINITION
# ============================================================================

class AgentState(TypedDict):
    figma_json: Dict[str, Any]
    original_figma_json: Optional[Dict[str, Any]]
    ds_catalog: Dict[str, Any]           # Built from catalog, used in validation
    ds_config: Optional[Dict[str, Any]]  # Catalog metadata (name, prefix, etc.)
    ds_catalog_entries: Optional[List[Dict]]  # Raw component list from catalog
    design_tokens: Optional[Dict[str, Any]]
    figma_screenshots: Optional[Dict[str, str]]
    ir_tree: Optional[List[Any]]
    component_mappings: Optional[List[DSComponentMapping]]
    generated: Optional[GeneratedAngularArtifact]
    validation_errors: List[ValidationError]
    repair_attempt: int
    messages: List[Any]
    ds_knowledge: Optional[Dict[str, Any]]   # pre-built utility class knowledge
    phase1_research_context: Optional[str]   # Phase 1 doc research output


# ============================================================================
# GLOBAL STATE (populated by run_figma_to_angular)
# ============================================================================

DS_CATALOG: Dict = {}
DESIGN_TOKENS: Dict = {}
DOC_KNOWLEDGE: Dict = {}          # populated at startup from {name}_knowledge.json
DS_CATALOG_ENTRY_MAP: Dict = {}   # {name_lower → entry, selector → entry} from catalog
DS_DOCS_DIR: str = ""             # path to design_systems/{name}_docs/ if it exists


def load_ds_knowledge(design_system: str) -> Optional[Dict]:
    """Load design_systems/{design_system}_knowledge.json — non-fatal if missing."""
    path = os.path.join(Config.DS_MAPPINGS_DIR, f"{design_system}_knowledge.json")
    if os.path.exists(path):
        with open(path) as f:
            data = json.load(f)
        n = sum(
            len(e.get("classes", {}))
            for sec in data.get("sections", {}).values()
            for e in sec.values()
        )
        print(f"Loaded knowledge: {data.get('name')} ({n} utility classes)")
        return data
    print(f"No knowledge file: {path} (utility class lookup disabled)")
    return None


# ============================================================================
# WORKFLOW NODES
# ============================================================================

# Regex for detecting spacer/padding/separator layer names (used in pruning)
_SPACER_NAME_RE = re.compile(
    r'\b(spacer|padding|gap|separator)\b', re.IGNORECASE
)


def _prune_figma_tree(node: Dict) -> Optional[Dict]:
    """Recursively prune structural noise from a cleaned Figma tree.

    Rules (post-order — children pruned before parent):
    1. Remove RECTANGLE/VECTOR nodes matching spacer name pattern or < 4px on both axes.
    2. Remove FRAME nodes matching spacer name pattern.
    3. Collapse FRAME/GROUP with 1 child and no visual styling
       (no fills, strokes, effects, cornerRadius, clipsContent, text).
    4. Deduplicate sibling groups of >=4 children with same type+child-type signature;
       keep first + annotate _repeat_count.
    """
    node_type = node.get("type", "")
    node_name = node.get("name", "") or ""
    styling   = node.get("styling") or {}
    props     = node.get("properties") or {}

    # Rule 1: remove spacer shapes
    if node_type in ("RECTANGLE", "VECTOR"):
        if _SPACER_NAME_RE.search(node_name):
            return None
        bbox = props.get("absoluteBoundingBox") or {}
        if bbox.get("width", 999) < 4 and bbox.get("height", 999) < 4:
            return None

    # Rule 2: remove spacer frames
    if node_type == "FRAME" and _SPACER_NAME_RE.search(node_name):
        return None

    # Recurse into children first
    pruned_children = [
        r for c in node.get("children", [])
        if (r := _prune_figma_tree(c)) is not None
    ]
    node = {**node, "children": pruned_children}

    # Rule 3: collapse single-child wrappers with no visual styling
    is_visual = (
        styling.get("fills") or styling.get("strokes") or styling.get("effects")
        or styling.get("cornerRadius") or props.get("clipsContent") or props.get("text")
    )
    if node_type in ("FRAME", "GROUP") and len(pruned_children) == 1 and not is_visual:
        return pruned_children[0]

    # Rule 4: deduplicate repeated sibling groups
    if len(pruned_children) >= 4:
        def sig(c: Dict) -> str:
            return c.get("type", "") + "|" + "|".join(
                gc.get("type", "") for gc in c.get("children", [])[:5]
            )
        first_type = pruned_children[0].get("type", "")
        first_sig  = sig(pruned_children[0])
        if all(c.get("type") == first_type and sig(c) == first_sig
               for c in pruned_children[1:]):
            kept = {**pruned_children[0], "_repeat_count": len(pruned_children) - 1}
            node = {**node, "children": [kept]}
            print(f"    Deduplicated '{node_name}': 1 of {len(pruned_children)} "
                  f"{first_type} kept (_repeat_count={len(pruned_children)-1})")

    return node


def ingest_figma_node(state: AgentState) -> AgentState:
    """Step 1: Clean and normalize Figma JSON."""
    METRICS.start_step("ingest_figma")
    figma = state["figma_json"]

    def clean_node(node: Dict) -> Optional[Dict]:
        if node.get("visible") is False:
            return None

        cleaned = {
            "id": node.get("id"),
            "name": node.get("name"),
            "type": node.get("type"),
            "children": [],
            "properties": {},
            "constraints": {},
            "styling": {},
            "layout": {},
        }

        # Layout properties
        for key in [
            "layoutMode", "primaryAxisSizingMode", "primaryAxisAlignItems",
            "counterAxisAlignItems", "layoutWrap", "layoutAlign", "layoutGrow",
            "layoutSizingHorizontal", "layoutSizingVertical",
            "paddingTop", "paddingBottom", "paddingLeft", "paddingRight",
            "itemSpacing", "counterAxisSpacing",
        ]:
            if key in node:
                cleaned["layout"][key] = node[key]

        # Positioning / sizing
        for key in ["absoluteBoundingBox", "absoluteRenderBounds"]:
            if key in node:
                cleaned["properties"][key] = node[key]

        if "constraints" in node:
            cleaned["constraints"] = node["constraints"]

        # Styling
        for key in ["fills", "background", "backgroundColor", "strokes",
                    "strokeWeight", "strokeAlign", "effects", "blendMode",
                    "cornerRadius", "rectangleCornerRadii", "opacity"]:
            if key in node:
                cleaned["styling"][key] = node[key]

        # Text properties
        if "characters" in node:
            cleaned["properties"]["text"] = node["characters"]
        if "style" in node:
            cleaned["styling"]["textStyle"] = node["style"]
        if "characterStyleOverrides" in node:
            cleaned["properties"]["characterStyleOverrides"] = node["characterStyleOverrides"]
        if "lineTypes" in node:
            cleaned["properties"]["lineTypes"] = node["lineTypes"]

        # Misc
        for key in ["interactions", "boundVariables", "componentPropertyReferences",
                    "clipsContent", "scrollBehavior", "componentProperties"]:
            if key in node:
                cleaned["properties"][key] = node[key]

        for child in node.get("children", []):
            cleaned_child = clean_node(child)
            if cleaned_child:
                cleaned["children"].append(cleaned_child)

        # Drop empty dicts (keep id/name/type always)
        cleaned = {k: v for k, v in cleaned.items() if v or k in ("id", "name", "type")}
        return cleaned

    if "document" in figma:
        cleaned_figma = clean_node(figma["document"])
        state["figma_metadata"] = {
            "components": figma.get("components", {}),
            "componentSets": figma.get("componentSets", {}),
            "styles": figma.get("styles", {}),
            "name": figma.get("name"),
            "version": figma.get("version"),
            "lastModified": figma.get("lastModified"),
        }
    else:
        cleaned_figma = clean_node(figma)
        state["figma_metadata"] = {}

    def count_nodes(n):
        return 1 + sum(count_nodes(c) for c in n.get("children", []))

    node_count = count_nodes(cleaned_figma) if cleaned_figma else 0
    original_size = len(json.dumps(figma))
    cleaned_size = len(json.dumps(cleaned_figma))

    print(f"Figma tree cleaned:")
    print(f"  - Root: {cleaned_figma.get('name', 'Unknown')} (Type: {cleaned_figma.get('type', 'Unknown')})")
    print(f"  - Total nodes: {node_count}")
    print(f"  - Original size: {original_size:,} chars")
    print(f"  - Cleaned size:  {cleaned_size:,} chars ({100*cleaned_size//original_size}%)")

    state["figma_json"] = cleaned_figma
    state["messages"].append(
        SystemMessage(content=f"Figma tree cleaned. Root: {cleaned_figma.get('name', 'Unknown')}, {node_count} nodes")
    )
    METRICS.end_step("ingest_figma")
    return state


def prune_figma_tree_node(state: AgentState) -> AgentState:
    """Step 1b: Prune structural noise before IR generation. Pure Python — no LLM.

    Skipped when Config.ENABLE_TREE_PRUNING is False.
    Runs up to 5 passes until the node count stabilises (handles cascading collapses).
    """
    if not Config.ENABLE_TREE_PRUNING:
        return state

    METRICS.start_step("prune_figma_tree")
    figma_tree = state["figma_json"]

    def count_nodes(n: Dict) -> int:
        return 1 + sum(count_nodes(c) for c in n.get("children", []))

    before = count_nodes(figma_tree)
    print(f"Tree pruning: {before} nodes before...")

    pruned = figma_tree
    for pass_num in range(5):
        next_pruned = _prune_figma_tree(pruned)
        if next_pruned is None:
            break
        after_pass = count_nodes(next_pruned)
        if after_pass == count_nodes(pruned):
            pruned = next_pruned
            break
        pruned = next_pruned
        print(f"  Pass {pass_num + 1}: {after_pass} nodes")

    after = count_nodes(pruned)
    reduction = 100 * (before - after) // before if before else 0
    print(f"  Pruned {before - after} nodes ({reduction}% reduction). Remaining: {after}")
    state["figma_json"] = pruned
    METRICS.end_step("prune_figma_tree")
    return state


def _flatten_figma_tree(node: Dict, parent_id: Optional[str] = None, depth: int = 0) -> List[Dict]:
    """Flatten Figma tree into a list of nodes with parent references."""
    nodes = []
    flat_node = {
        "id": node.get("id"),
        "name": node.get("name"),
        "type": node.get("type"),
        "parent_id": parent_id,
        "depth": depth,
        "has_children": bool(node.get("children")),
        "child_count": len(node.get("children", [])),
    }
    for key in ["properties", "constraints", "styling", "layout"]:
        if key in node and node[key]:
            flat_node[key] = node[key]
    nodes.append(flat_node)
    for child in node.get("children", []):
        nodes.extend(_flatten_figma_tree(child, node.get("id"), depth + 1))
    return nodes


def _create_compact_tree_representation(node: Dict, max_depth: int = 10) -> Dict:
    """Create a compact tree representation preserving structure."""
    if max_depth <= 0:
        return {"id": node.get("id"), "name": node.get("name"), "type": node.get("type"), "truncated": True}

    compact = {
        "id": node.get("id"),
        "name": node.get("name"),
        "type": node.get("type"),
    }
    if node.get("layout"):
        compact["layout"] = node["layout"]
    props = node.get("properties", {})
    if props.get("text"):
        compact["text"] = props["text"]
    # Preserve componentProperties so variant data survives IR generation
    if props.get("componentProperties"):
        compact["componentProperties"] = props["componentProperties"]
    if node.get("styling"):
        styling = node["styling"]
        compact_styling = {}
        if styling.get("fills"):
            compact_styling["fills"] = styling["fills"][:1]
        if styling.get("textStyle"):
            ts = styling["textStyle"]
            compact_styling["textStyle"] = {
                k: ts[k] for k in ["fontSize", "fontWeight", "fontFamily", "textAlignHorizontal"]
                if k in ts
            }
        if compact_styling:
            compact["styling"] = compact_styling
    if node.get("children"):
        compact["children"] = [
            _create_compact_tree_representation(child, max_depth - 1)
            for child in node["children"]
        ]
    return compact


def _chunk_nodes(nodes: List[Dict], chunk_size: int) -> List[List[Dict]]:
    return [nodes[i:i + chunk_size] for i in range(0, len(nodes), chunk_size)]


def _parse_llm_json_response(content: str) -> Any:
    """Parse JSON from LLM response, handling markdown formatting."""
    content = content.strip()
    if content.startswith("```"):
        parts = content.split("```")
        if len(parts) >= 2:
            content = parts[1]
            if content.startswith("json"):
                content = content[4:]
            content = content.strip()
    return json.loads(content)


def _extract_ir_nodes(data: Any) -> List[Dict]:
    """Extract a list of IR node dicts from an LLM response.

    The LLM is asked to return a JSON array but sometimes wraps it in an
    object, e.g. {"nodes": [...]} or {"ir_tree": [...]}.  Without this
    unwrapping the whole wrapper dict ends up as a single fake node, causing
    every downstream read of "type", "name", and "layout" to return None.

    Handles three cases:
      - Already a list  → return as-is
      - A dict with a recognisable list-valued key → return that list
      - A dict with no recognisable key → treat the dict as a single node
    """
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        for key in ("nodes", "ir_tree", "tree", "components", "result", "data", "children"):
            val = data.get(key)
            if isinstance(val, list) and val:
                return val
        # Single-node dict — wrap it
        return [data]
    return []

def build_ir_node(state: AgentState) -> AgentState:
    """Step 2: Convert Figma JSON to IR using chunked processing for large trees."""
    METRICS.start_step("build_ir")
    if Config.FAST_MODE:
        Config.MAX_NODES_PER_CHUNK = 100
    llm = ChatOpenAI(model=Config.LLM_MODEL, temperature=Config.LLM_TEMPERATURE)

    figma_tree = state["figma_json"]
    compact_tree = _create_compact_tree_representation(figma_tree)
    compact_json = json.dumps(compact_tree, indent=2)

    print(f"Original Figma tree size: {len(json.dumps(figma_tree))} chars")
    print(f"Compact tree size:        {len(compact_json)} chars")

    if len(compact_json) <= Config.MAX_JSON_SIZE:
        ir_data = _process_single_tree(llm, compact_tree, state)
    else:
        print("Tree too large, using chunked processing...")
        flat_nodes = _flatten_figma_tree(figma_tree)
        print(f"Flattened to {len(flat_nodes)} nodes")
        ir_data = _process_chunked_tree(llm, flat_nodes, figma_tree, state)

    if ir_data:
        state["ir_tree"] = ir_data
        state["messages"].append(AIMessage(content=f"Generated IR with {len(ir_data)} nodes"))
    else:
        state["ir_tree"] = []

    METRICS.end_step("build_ir")
    return state


def _process_single_tree(llm, compact_tree: Dict, state: AgentState) -> List[Dict]:
    """Process entire Figma tree in a single LLM call."""
    system_prompt = """You are an expert at analyzing Figma design trees and converting them to semantic UI components.

Convert this Figma tree to semantic UI primitives (IR - Intermediate Representation).

For EACH node in the tree (including all nested children), identify:
1. Semantic type: button, text, input, card, icon, image, container, list, header, footer, nav, form, divider, avatar, badge, chip, dialog, menu, tab, table, link
2. Layout type: flex-row, flex-column, grid, absolute, stack
3. Key properties (text content, colors, sizing, spacing)
4. Preserve the EXACT hierarchy - each node's children array must contain its child nodes

CRITICAL: You must process the ENTIRE tree structure. Do not skip any nodes.
- If a node has children, include them in the children array
- Preserve the parent-child relationships exactly as they appear in the input
- Include ALL nodes, even deeply nested ones

Output as a JSON array with the root node(s). Example structure:
[
  {
    "id": "1:2",
    "type": "container",
    "name": "MainContainer",
    "layout": "flex-column",
    "properties": {},
    "constraints": {},
    "styling": {"backgroundColor": "#fff"},
    "children": [
      {
        "id": "1:3",
        "type": "text",
        "name": "Title",
        "layout": "flex-row",
        "properties": {"text": "Welcome"},
        "children": [],
        "constraints": {},
        "styling": {"color": "#000", "fontSize": 24}
      }
    ]
  }
]

IMPORTANT:
- Output ONLY valid JSON, no markdown formatting or explanations
- Process ALL nodes in the tree, not just top-level ones
- Maintain exact tree structure with proper nesting"""

    messages = [
        SystemMessage(content=system_prompt),
        HumanMessage(content=f"Convert this Figma tree to IR. Process ALL nodes:\n\n{json.dumps(compact_tree, indent=2)}"),
    ]

    try:
        print("Invoking LLM for full tree IR generation...")
        input_chars = len(json.dumps(compact_tree))
        print(f"  Sending {input_chars} chars to LLM")
        response = llm.invoke(messages)
        METRICS.record_llm_call(input_chars, len(response.content), "build_ir")
        print(f"LLM response received. Length: {len(response.content)} chars")

        ir_data = _extract_ir_nodes(_parse_llm_json_response(response.content))

        total_nodes = _count_ir_nodes(ir_data)
        print(f"Generated IR with {total_nodes} total nodes")
        return ir_data

    except json.JSONDecodeError as e:
        state["validation_errors"].append(
            ValidationError(file_path="ir_tree", error_type="parse_error",
                            message=f"Failed to parse IR JSON: {str(e)}")
        )
        return []
    except Exception as e:
        state["validation_errors"].append(
            ValidationError(file_path="ir_tree", error_type="generation_error",
                            message=f"IR generation failed: {str(e)}")
        )
        return []


def _count_ir_nodes(ir_nodes: List[Dict]) -> int:
    count = 0
    for node in ir_nodes:
        count += 1
        if node.get("children"):
            count += _count_ir_nodes(node["children"])
    return count


def _process_single_chunk(
    chunk: List[Dict],
    chunk_idx: int,
    total_chunks: int,
    system_prompt: str,
) -> tuple:
    """Process one flat-node chunk via a dedicated LLM instance.

    Returns (chunk_idx, node_dict, error_msg_or_None).
    Creating a per-thread ChatOpenAI instance avoids any shared mutable state.
    """
    print(f"Processing chunk {chunk_idx+1}/{total_chunks} ({len(chunk)} nodes)...")
    llm = ChatOpenAI(
        model=Config.LLM_MODEL,
        temperature=Config.LLM_TEMPERATURE,
        api_key=Config.OPENAI_API_KEY,
    )
    messages = [
        SystemMessage(content=system_prompt),
        HumanMessage(content=f"Convert these {len(chunk)} Figma nodes to IR:\n\n"
                              f"{json.dumps(chunk, indent=2)}"),
    ]
    try:
        response = llm.invoke(messages)
        METRICS.record_llm_call(len(json.dumps(chunk)), len(response.content), "build_ir")
        chunk_ir = _extract_ir_nodes(_parse_llm_json_response(response.content))
        return chunk_idx, {n["id"]: n for n in chunk_ir if n.get("id")}, None
    except Exception as exc:
        return chunk_idx, {}, f"Chunk {chunk_idx+1}/{total_chunks} failed: {exc}"


def _process_chunked_tree(llm, flat_nodes: List[Dict], original_tree: Dict, state: AgentState) -> List[Dict]:
    """Process large Figma tree in parallel chunks and reconstruct the hierarchy."""
    chunks = _chunk_nodes(flat_nodes, Config.MAX_NODES_PER_CHUNK)

    system_prompt = """You are an expert at analyzing Figma design nodes and converting them to semantic UI components.

Convert these Figma nodes to semantic UI primitives (IR - Intermediate Representation).

For EACH node, identify:
1. Semantic type: button, text, input, card, icon, image, container, list, header, footer, nav, form, divider, avatar, badge, chip, dialog, menu, tab, table, link
2. Layout type (string): flex-row, flex-column, grid, absolute, stack
3. Key properties (text content, colors, sizing, spacing)

Note: These are flattened nodes from a larger tree. Each node has:
- id: unique identifier
- parent_id: ID of parent node (null for root)
- depth: nesting level
- has_children: whether it has child nodes

Output as a JSON array with ONE entry per input node. Each entry MUST use this exact structure:
[
  {
    "id": "1:2",
    "type": "container",
    "name": "MainContainer",
    "layout": "flex-column",
    "properties": {},
    "constraints": {},
    "styling": {"backgroundColor": "#fff"},
    "children": []
  }
]

IMPORTANT:
- Output ONLY valid JSON, no markdown formatting or explanations
- Include ALL input nodes in output — never skip any
- "layout" must be a string (flex-row, flex-column, grid, absolute, stack), never a dict
- "children" is always an empty array [] — hierarchy is reconstructed separately
- "properties" must include "text" for TEXT nodes (e.g. {"text": "Hello"})"""

    max_workers = min(8, len(chunks))
    print(f"Split into {len(chunks)} chunk(s), {max_workers} parallel worker(s)")

    all_ir_nodes: Dict[str, Dict] = {}
    chunk_errors: List[str] = []

    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {
            executor.submit(_process_single_chunk, chunk, idx, len(chunks), system_prompt): idx
            for idx, chunk in enumerate(chunks)
        }
        for future in concurrent.futures.as_completed(futures):
            chunk_idx, node_dict, error = future.result()
            all_ir_nodes.update(node_dict)
            if error:
                chunk_errors.append(error)

    for msg in chunk_errors:
        print(f"Warning: {msg}")
        state["validation_errors"].append(
            ValidationError(file_path="ir_tree", error_type="chunk_error", message=msg)
        )

    print(f"Reconstructing hierarchy from {len(all_ir_nodes)} IR nodes...")
    return _reconstruct_ir_hierarchy(all_ir_nodes, original_tree)


def _reconstruct_ir_hierarchy(ir_nodes: Dict[str, Dict], original_tree: Dict) -> List[Dict]:
    """Reconstruct IR tree hierarchy based on original Figma tree structure."""
    def build_node(figma_node: Dict) -> Optional[Dict]:
        node_id = figma_node.get("id")
        ir_node = ir_nodes.get(node_id) or {
            "id": node_id,
            "type": "container",
            "name": figma_node.get("name", "Unknown"),
            "layout": "flex-column",
            "properties": {},
            "constraints": {},
            "styling": {},
        }
        ir_node.pop("parent_id", None)
        ir_node["children"] = [
            build_node(child)
            for child in figma_node.get("children", [])
            if (child_ir := build_node(child)) is not None
        ]
        return ir_node

    root_ir = build_node(original_tree)
    return [root_ir] if root_ir else []


def _flatten_ir_nodes(ir_nodes: List[Dict], parent_id: Optional[str] = None) -> List[Dict]:
    """Flatten IR tree to a list of nodes for batch processing."""
    flat = []
    for node in ir_nodes:
        flat_node = {k: v for k, v in node.items() if k != "children"}
        flat_node["parent_id"] = parent_id
        flat_node["has_children"] = bool(node.get("children"))
        flat.append(flat_node)
        if node.get("children"):
            flat.extend(_flatten_ir_nodes(node["children"], node.get("id")))
    return flat


# ============================================================================
# STEP 3: MAP TO DESIGN SYSTEM (catalog-based, deterministic-first)
# ============================================================================

def map_to_design_system_node(state: AgentState) -> AgentState:
    """Step 3: Map IR nodes to DS components using catalog scoring + tiered LLM fallback.

    TEXT nodes are always classified as native HTML (h1–h6 / p) — no LLM involved.
    Non-text nodes are scored against the catalog using 6 signals, then tiered:
      - score ≥ 70  → definite deterministic match  (≥ 40 in fast mode)
      - score 30–69 → Tier 2: queued for parallel batch LLM resolution
      - score 1–29  → Tier 3: queued for parallel batch LLM resolution
      - score = 0   → native HTML fallback  (fast mode: everything below threshold is native)
    Tier 2 + Tier 3 nodes are merged and resolved via _resolve_uncertain_nodes_parallel.
    """
    METRICS.start_step("map_to_ds")

    if not state.get("ir_tree"):
        state["validation_errors"].append(
            ValidationError(file_path="mappings", error_type="no_ir",
                            message="No IR tree available for mapping")
        )
        state["component_mappings"] = []
        return state

    catalog_entries: List[Dict] = state.get("ds_catalog_entries") or []
    figma_lookup = _build_figma_node_lookup(state.get("figma_json") or {})
    scraper = DocScraper()

    # Build parent-name map for richer LLM context
    parent_name_map = _build_parent_name_map(state["ir_tree"])

    flat_ir_nodes = _flatten_ir_nodes(state["ir_tree"])
    print(f"Mapping {len(flat_ir_nodes)} IR nodes "
          f"(catalog: {len(catalog_entries)} components)...")

    definite_mappings: List[Dict] = []
    ambiguous_nodes: List[Dict] = []       # score 30–69
    low_confidence_nodes: List[Dict] = []  # score 1–29

    for ir_node in flat_ir_nodes:
        node_id = ir_node.get("id", "")
        node_type = ir_node.get("type", "")
        node_name = ir_node.get("name", "")

        # ── TEXT nodes → native HTML, always ──────────────────────────────
        if node_type == "text":
            figma_node = figma_lookup.get(node_id, {})
            html_tag = classify_figma_text_as_html(figma_node)
            definite_mappings.append({
                "figma_node_id": node_id,
                "ds_component": "native",
                "ds_selector": html_tag,
                "inputs": {},
            })
            continue

        # ── No catalog → native HTML fallback ─────────────────────────────
        if not catalog_entries:
            definite_mappings.append({
                "figma_node_id": node_id,
                "ds_component": "native",
                "ds_selector": _infer_native_html_tag(ir_node),
                "inputs": {},
            })
            continue

        # ── Score catalog entries (6 signals) ─────────────────────────────
        figma_orig_node = figma_lookup.get(node_id)
        scored = sorted(
            [
                (s, entry)
                for entry in catalog_entries
                if (s := _score_catalog_match(ir_node, node_name, entry, figma_orig_node)) > 0
            ],
            key=lambda x: x[0],
            reverse=True,
        )

        if scored and scored[0][0] >= 70:
            # Tier 1: definite deterministic match
            best_entry = scored[0][1]
            api_url = best_entry.get("urls", {}).get("api", "")
            if api_url:
                scraper.fetch(api_url)  # prime cache; context not used directly here
            print(f"  Definite match: '{node_name}' → {best_entry['name']} (score {scored[0][0]})")
            definite_mappings.append({
                "figma_node_id": node_id,
                "ds_component": best_entry["name"],
                "ds_selector": best_entry["selector"],
                "inputs": {},
            })
        elif not Config.FAST_MODE and scored and scored[0][0] >= 30:
            # Tier 2: medium ambiguity → batch LLM with rich context
            ambiguous_nodes.append({
                "ir_node":     ir_node,
                "figma_type":  figma_orig_node.get("type", "") if figma_orig_node else "",
                "parent_name": parent_name_map.get(node_id, ""),
                "top3":        scored[:3],
            })
        elif not Config.FAST_MODE and scored:
            # Tier 3: low confidence → parallel batched LLM
            low_confidence_nodes.append({
                "ir_node":     ir_node,
                "figma_type":  figma_orig_node.get("type", "") if figma_orig_node else "",
                "parent_name": parent_name_map.get(node_id, ""),
                "top3":        scored[:3],
            })
        else:
            # Score = 0, or fast mode below threshold → native HTML immediately
            definite_mappings.append({
                "figma_node_id": node_id,
                "ds_component": "native",
                "ds_selector": _infer_native_html_tag(ir_node),
                "inputs": {},
            })

    # ── Tier 2 + 3: Merged parallel batch resolution ──────────────────────
    all_uncertain = ambiguous_nodes + low_confidence_nodes
    if all_uncertain:
        print(f"Resolving {len(ambiguous_nodes)} ambiguous + {len(low_confidence_nodes)} "
              f"low-confidence nodes via parallel batched LLM calls...")
        definite_mappings.extend(
            _resolve_uncertain_nodes_parallel(all_uncertain, catalog_entries)
        )

    # ── Build DSComponentMapping objects ──────────────────────────────────
    try:
        state["component_mappings"] = [DSComponentMapping(**m) for m in definite_mappings]
        print(f"Total mappings: {len(state['component_mappings'])}")
        state["messages"].append(
            AIMessage(content=f"Mapped {len(state['component_mappings'])} nodes to DS components")
        )
    except Exception as e:
        state["validation_errors"].append(
            ValidationError(file_path="mappings", error_type="mapping_error",
                            message=f"Failed to create mappings: {str(e)}")
        )
        state["component_mappings"] = []

    METRICS.end_step("map_to_ds")
    return state


# ============================================================================
# HELPER FUNCTIONS FOR CODE GENERATION
# ============================================================================

def _collect_descendant_texts(node: Dict) -> List[str]:
    """Recursively collect text from all descendant TEXT-type nodes."""
    texts = []
    for child in node.get("children", []):
        if child.get("type") == "TEXT":
            text = child.get("properties", {}).get("text") or child.get("characters")
            if text:
                texts.append(text)
        texts.extend(_collect_descendant_texts(child))
    return texts


def _build_design_structure_for_codegen(figma_json: Dict, ir_tree: List[Dict],
                                        mappings: List, max_depth: int = 8) -> Dict:
    """Build a comprehensive design structure for code generation using Figma data."""

    def extract_node_info(node: Dict, depth: int = 0) -> Optional[Dict]:
        if depth > max_depth:
            return {"name": node.get("name"), "truncated": True}

        info: Dict[str, Any] = {
            "name": node.get("name"),
            "type": node.get("type"),
        }

        layout = node.get("layout", {})
        if layout:
            for lk, ik in [("layoutMode", "layoutMode"), ("primaryAxisAlignItems", "mainAxisAlign"),
                            ("counterAxisAlignItems", "crossAxisAlign"), ("layoutWrap", "wrap"),
                            ("layoutSizingHorizontal", "sizingH"), ("layoutSizingVertical", "sizingV")]:
                if layout.get(lk):
                    info[ik] = layout[lk]

        props = node.get("properties", {})
        if props.get("text"):
            info["text"] = props["text"]

        if props.get("absoluteBoundingBox"):
            box = props["absoluteBoundingBox"]
            info["width"] = box.get("width")
            info["height"] = box.get("height")

        spacing_style: Dict[str, Any] = {}
        padding_top = layout.get("paddingTop") or node.get("paddingTop")
        padding_bottom = layout.get("paddingBottom") or node.get("paddingBottom")
        padding_left = layout.get("paddingLeft") or node.get("paddingLeft")
        padding_right = layout.get("paddingRight") or node.get("paddingRight")
        if any([padding_top, padding_bottom, padding_left, padding_right]):
            spacing_style["padding"] = (
                f"{int(padding_top or 0)}px {int(padding_right or 0)}px "
                f"{int(padding_bottom or 0)}px {int(padding_left or 0)}px"
            )

        item_spacing = layout.get("itemSpacing") or node.get("itemSpacing")
        if item_spacing:
            spacing_style["gap"] = f"{int(item_spacing)}px"

        corner_radius = node.get("cornerRadius")
        if corner_radius:
            spacing_style["borderRadius"] = f"{int(corner_radius)}px"

        corner_radii = node.get("rectangleCornerRadii")
        if corner_radii and len(corner_radii) == 4:
            spacing_style["borderRadius"] = (
                f"{int(corner_radii[0])}px {int(corner_radii[1])}px "
                f"{int(corner_radii[2])}px {int(corner_radii[3])}px"
            )

        styling = node.get("styling", {})
        style_info: Dict[str, Any] = {}
        if styling:
            if styling.get("fills"):
                fill = styling["fills"][0] if styling["fills"] else {}
                if fill.get("type") == "SOLID" and isinstance(fill.get("color"), dict):
                    color = fill["color"]
                    r, g, b = int(color.get("r", 0) * 255), int(color.get("g", 0) * 255), int(color.get("b", 0) * 255)
                    opacity = fill.get("opacity", 1)
                    style_info["backgroundColor"] = (
                        f"rgba({r},{g},{b},{opacity:.2f})" if opacity < 1 else f"rgb({r},{g},{b})"
                    )

            if styling.get("textStyle"):
                ts = styling["textStyle"]
                for key, css in [("fontSize", "fontSize"), ("fontWeight", "fontWeight"),
                                  ("fontFamily", "fontFamily"), ("lineHeightPx", "lineHeight"),
                                  ("letterSpacing", "letterSpacing")]:
                    if ts.get(key):
                        style_info[css] = f"{int(ts[key])}px" if key in ("fontSize", "lineHeightPx", "letterSpacing") else ts[key]
                if ts.get("textAlignHorizontal"):
                    style_info["textAlign"] = ts["textAlignHorizontal"].lower()

            if styling.get("effects"):
                for effect in styling["effects"]:
                    if effect.get("type") == "DROP_SHADOW" and effect.get("visible", True):
                        color = effect.get("color", {})
                        r, g, b = int(color.get("r", 0) * 255), int(color.get("g", 0) * 255), int(color.get("b", 0) * 255)
                        a = color.get("a", 1)
                        offset = effect.get("offset", {})
                        style_info["boxShadow"] = (
                            f"{int(offset.get('x',0))}px {int(offset.get('y',0))}px "
                            f"{int(effect.get('radius',0))}px rgba({r},{g},{b},{a:.2f})"
                        )
                        break

            if styling.get("opacity") is not None and styling["opacity"] < 1:
                style_info["opacity"] = styling["opacity"]

        style_info.update(spacing_style)
        if style_info:
            info["style"] = style_info

        children = node.get("children", [])
        if children:
            info["children"] = [
                child_info
                for child in children
                if (child_info := extract_node_info(child, depth + 1)) is not None
            ]

        if not info.get("text") and children:
            collected_texts = _collect_descendant_texts(node)
            if collected_texts:
                info["innerText"] = " ".join(collected_texts)

        return info

    if figma_json and figma_json.get("children"):
        return extract_node_info(figma_json)

    if ir_tree:
        def ir_to_structure(ir_node: Dict, depth: int = 0) -> Dict:
            if depth > max_depth:
                return {"name": ir_node.get("name"), "truncated": True}
            info = {
                "name": ir_node.get("name"),
                "type": ir_node.get("type"),
                "layout": ir_node.get("layout"),
            }
            props = ir_node.get("properties", {})
            if props.get("text"):
                info["text"] = props["text"]
            if ir_node.get("styling"):
                info["style"] = ir_node["styling"]
            children = ir_node.get("children", [])
            if children:
                info["children"] = [ir_to_structure(c, depth + 1) for c in children]
            if not info.get("text") and children:
                collected_texts = _collect_descendant_texts(ir_node)
                if collected_texts:
                    info["innerText"] = " ".join(collected_texts)
            return info

        return ir_to_structure(ir_tree[0])

    return {"name": "Unknown", "type": "container", "children": []}


def _extract_variant_tokens(
    node_name: str,
    ir_props: Dict,
    raw_figma_node: Optional[Dict],
) -> List[str]:
    """Collect variant descriptor tokens from all available sources.

    Walks every string value inside componentProperties (any type, not just VARIANT)
    and the full IR properties dict, regardless of nesting depth.  Also splits the
    Figma layer name on common delimiters.

    Returns a deduplicated list of lowercase tokens (≥2 chars, in discovery order).
    """
    seen: set = set()
    tokens: List[str] = []

    def add(t: str) -> None:
        t = t.lower().strip()
        if t and len(t) >= 2 and t not in seen:
            seen.add(t)
            tokens.append(t)

    def collect_strings(obj: Any) -> None:
        """Recursively extract every string leaf, skip pure-numeric strings."""
        if isinstance(obj, str):
            if obj.strip() and not obj.strip().lstrip("-").replace(".", "", 1).isdigit():
                add(obj)
        elif isinstance(obj, dict):
            for v in obj.values():
                collect_strings(v)
        elif isinstance(obj, (list, tuple)):
            for item in obj:
                collect_strings(item)

    # Source 1: componentProperties from the IR node (all types)
    collect_strings((ir_props or {}).get("componentProperties", {}))

    # Source 2: componentProperties from the raw Figma node (all types)
    collect_strings((raw_figma_node or {}).get("componentProperties", {}))

    # Source 3: remaining IR properties (skip layout/geometry keys to reduce noise)
    _SKIP_PROPS = {"text", "componentProperties", "absoluteBoundingBox", "absoluteRenderBounds"}
    for key, val in (ir_props or {}).items():
        if key not in _SKIP_PROPS:
            collect_strings(val)

    # Source 4: layer name parts (e.g. "Button / Primary / Large" → primary, large)
    for part in _VARIANT_TOKEN_RE.split(node_name):
        add(part.strip())

    return tokens


def _match_variant_classes(
    tokens: List[str],
    variant_map: Dict[str, List[str]],
) -> List[str]:
    """Match variant tokens against the catalog's variant_class_map.

    Three-tier matching (each catalog key used at most once):
      Tier 1 — Exact:       token == catalog_key
      Tier 2 — Word match:  token is a whole word inside catalog_key
      Tier 3 — Substring:   token contained in catalog_key (or vice versa),
                            only for tokens/keys ≥ 4 chars

    Returns the accumulated list of CSS class strings.
    """
    if not variant_map or not tokens:
        return []

    # Pre-normalise keys once
    norm: Dict[str, List[str]] = {k.lower(): v for k, v in variant_map.items()}
    used_keys: set = set()
    result: List[str] = []

    # ── Tier 1: exact match ──────────────────────────────────────────────────
    for token in tokens:
        if token in norm and token not in used_keys:
            result.extend(norm[token])
            used_keys.add(token)

    # ── Tier 2: word-boundary match ──────────────────────────────────────────
    for token in tokens:
        for key_lower, classes in norm.items():
            if key_lower in used_keys:
                continue
            key_words = set(_VARIANT_TOKEN_RE.split(key_lower))
            if token in key_words:
                result.extend(classes)
                used_keys.add(key_lower)

    # ── Tier 3: substring match (tokens/keys ≥ 4 chars) ─────────────────────
    for token in tokens:
        if len(token) < 4:
            continue
        for key_lower, classes in norm.items():
            if key_lower in used_keys:
                continue
            if token in key_lower or (len(key_lower) >= 4 and key_lower in token):
                result.extend(classes)
                used_keys.add(key_lower)

    return result


def _build_component_hierarchy_context(ir_tree: List[Dict], mappings: List,
                                       figma_lookup: Optional[Dict] = None,
                                       max_depth: int = 5) -> List[Dict]:
    """Build a context object representing the component hierarchy for code generation."""
    mapping_by_id = {}
    for m in mappings:
        m_dict = m.model_dump() if hasattr(m, "model_dump") else (m.dict() if hasattr(m, "dict") else m)
        mapping_by_id[m_dict.get("figma_node_id")] = m_dict

    def build_node_context(ir_node: Dict, depth: int = 0) -> Dict:
        if depth > max_depth:
            return {"truncated": True, "id": ir_node.get("id")}
        node_id = ir_node.get("id")
        mapping = mapping_by_id.get(node_id, {})
        context_node = {
            "id": node_id,
            "name": ir_node.get("name"),
            "type": ir_node.get("type"),
            "layout": ir_node.get("layout"),
            "ds_component": mapping.get("ds_component"),
            "ds_selector": mapping.get("ds_selector"),
            "inputs": mapping.get("inputs", {}),
            "properties": ir_node.get("properties", {}),
            "styling": ir_node.get("styling", {}),
        }
        # Resolve HTML classes, directives, and inner_html_note from catalog
        resolved_classes: List[str] = []
        resolved_directives: List[str] = []
        inner_html_note: str = ""

        ds_comp = mapping.get("ds_component", "")
        if ds_comp and ds_comp != "native":
            cat_entry = DS_CATALOG_ENTRY_MAP.get(ds_comp.lower()) or {}
            # Base classes — always applied regardless of variant
            resolved_classes = list(cat_entry.get("base_classes", []))
            # Variant classes — multi-source, 3-tier matching
            variant_map = cat_entry.get("variant_class_map", {})
            if variant_map:
                raw_node = (figma_lookup or {}).get(node_id)
                tokens = _extract_variant_tokens(
                    node_name=ir_node.get("name", ""),
                    ir_props=ir_node.get("properties", {}),
                    raw_figma_node=raw_node,
                )
                resolved_classes.extend(_match_variant_classes(tokens, variant_map))
            # Directives — all selectors from the catalog entry's directives list
            resolved_directives = [
                d["selector"] for d in cat_entry.get("directives", []) if d.get("selector")
            ]
            # Inner HTML guidance from catalog
            inner_html_note = cat_entry.get("inner_html_note", "")
            # Inputs from catalog — overrides the empty mapping default
            catalog_inputs = cat_entry.get("inputs", [])
            if catalog_inputs:
                context_node["inputs"] = {inp: "" for inp in catalog_inputs}

        context_node["resolved_classes"] = resolved_classes
        context_node["resolved_directives"] = resolved_directives
        if inner_html_note:
            context_node["inner_html_note"] = inner_html_note

        if ir_node.get("children"):
            context_node["children"] = [
                build_node_context(child, depth + 1)
                for child in ir_node["children"]
            ]
        return context_node

    return [build_node_context(node) for node in ir_tree]


# ============================================================================
# PHASE 1: UTILITY CLASS RESEARCH (tool-calling loop)
# ============================================================================

_PHASE1_TOOLS_LIST: List = []   # populated after tool definitions
_MAX_TOOL_ITERATIONS = 10        # raised to cover both utility-class + component-docs research


@lc_tool
def search_doc_knowledge(query: str, section: str = "all") -> str:
    """Search the design system's CSS utility class documentation.

    Args:
        query:   What to look for (e.g. 'flex layout', 'shadow', 'text color').
        section: 'layout' | 'content' | 'utilities' | 'components' | 'all'

    Returns: JSON of matching class groups.
    """
    if not DOC_KNOWLEDGE:
        return json.dumps({"message": "No knowledge base loaded."})

    query_lower = query.lower()
    sections_map = DOC_KNOWLEDGE.get("sections", {})
    if section != "all":
        sections_map = {section: sections_map[section]} if section in sections_map else {}

    results = {}
    for sec_name, sec_data in sections_map.items():
        for entry_key, entry_data in sec_data.items():
            classes = entry_data.get("classes", {})
            title_match = (
                query_lower in entry_data.get("title", "").lower()
                or query_lower in entry_key
            )
            matched_classes = {
                cls: desc for cls, desc in classes.items()
                if query_lower in cls.lower() or query_lower in desc.lower()
            }
            if title_match or matched_classes:
                results[entry_key] = {
                    "section": sec_name,
                    "title": entry_data.get("title"),
                    "url":   entry_data.get("url"),
                    "classes": matched_classes or classes,
                    "examples": entry_data.get("examples", [])[:2],
                }

    if not results:
        return json.dumps({
            "message": (
                f"No matches for '{query}'. "
                "Try: layout, spacing, color, shadow, flex, grid."
            )
        })
    return json.dumps(results, indent=2)


@lc_tool
def fetch_doc_page(url: str) -> str:
    """Fetch the text of a documentation page (cached, up to 8000 chars).

    Use after search_doc_knowledge returns a URL you want to explore further.
    """
    try:
        scraper = DocScraper()
        return scraper.fetch(url, max_chars=8000)
    except Exception as exc:
        return f"Failed to fetch {url}: {exc}"


@lc_tool
def fetch_component_docs(component: str, doc_type: str = "api") -> str:
    """Fetch documentation for a specific design system component.

    Args:
        component: Component name or selector (e.g., 'button', 'p-button', 'card').
                   Case-insensitive lookup against the catalog.
        doc_type:  Which page to fetch — 'overview' | 'api' | 'usage'
                   (default: 'api').  Falls back to the next available URL type
                   if the requested one is absent for this component.

    Returns:
        Plain text of the documentation page (cached, up to 8000 chars), or an
        informative error message if the component or URL is not found.
    """
    if not DS_CATALOG_ENTRY_MAP:
        return "Component catalog not loaded."

    # Flexible lookup: try selector first, then name (both stored in the map)
    key = component.strip().lower()
    entry = DS_CATALOG_ENTRY_MAP.get(key)
    if entry is None:
        # Try stripping a common prefix (e.g. "p-button" → "button")
        stripped = key.split("-", 1)[-1] if "-" in key else key
        entry = DS_CATALOG_ENTRY_MAP.get(stripped)

    if entry is None:
        available = sorted(set(DS_CATALOG_ENTRY_MAP.keys()))
        return (
            f"Component '{component}' not found in catalog. "
            f"Available: {', '.join(available[:20])}"
        )

    urls: Dict[str, str] = entry.get("urls", {})

    # Try requested type, then fall back through the other two in priority order
    fallback_order = ["api", "overview", "usage"]
    candidates = [doc_type] + [t for t in fallback_order if t != doc_type]
    chosen_type, chosen_url = None, None
    for candidate in candidates:
        if urls.get(candidate):
            chosen_type, chosen_url = candidate, urls[candidate]
            break

    if not chosen_url:
        return (
            f"No URLs configured for component '{component}' in the catalog. "
            f"Add urls.overview / urls.api / urls.usage entries."
        )

    if chosen_type != doc_type:
        note = f"(Note: '{doc_type}' URL not set; returning '{chosen_type}' instead.) "
    else:
        note = ""

    comp_name = entry.get("name", component)

    # ── Prefer local .md file produced by scrape_ds_docs.py ──────────────────
    if DS_DOCS_DIR:
        local_path = os.path.join(DS_DOCS_DIR, "components", f"{comp_name}.md")
        if os.path.exists(local_path):
            try:
                with open(local_path, encoding="utf-8") as fh:
                    text = fh.read()
                if text:
                    return f"{note}# {comp_name} — (local docs)\nSource: {local_path}\n\n{text[:8000]}"
            except Exception as exc:
                pass  # fall through to URL fetch

    # ── Fall back to URL fetch via DocScraper (cached) ───────────────────────
    try:
        scraper = DocScraper()
        text = scraper.fetch(chosen_url, max_chars=8000)
        if not text:
            return f"{note}Fetched empty content from {chosen_url}"
        return f"{note}# {comp_name} — {chosen_type}\nSource: {chosen_url}\n\n{text}"
    except Exception as exc:
        return f"Failed to fetch {chosen_url}: {exc}"


_PHASE1_TOOLS_LIST = [search_doc_knowledge, fetch_doc_page, fetch_component_docs]


def _build_phase1_design_summary(ir_tree: List[Dict], max_items: int = 30) -> str:
    """Build a compact (~20-40 line) design outline to drive Phase 1 class searches."""
    lines: List[str] = []

    # Collect unique font sizes so Phase 1 can look up matching utility classes
    font_sizes: set = set()

    def _collect_sizes(node: Dict) -> None:
        ts = node.get("styling", {}).get("textStyle", {})
        if ts.get("fontSize"):
            font_sizes.add(int(ts["fontSize"]))
        for child in node.get("children", []):
            _collect_sizes(child)

    for n in ir_tree:
        _collect_sizes(n)
    if font_sizes:
        lines.append(f"Typography sizes in design: {sorted(font_sizes, reverse=True)} px")

    def walk(node: Dict, depth: int = 0) -> None:
        if len(lines) >= max_items:
            return
        t      = node.get("type", "?")
        name   = node.get("name", "")
        layout = node.get("layout", "")
        styling = node.get("styling", {})
        text   = (node.get("properties") or {}).get("text", "")
        indent = "  " * depth
        desc = f"{indent}- {t} [{name}]"
        if text:
            desc += f" text={text!r}"
        if layout:
            desc += f" layout={layout}"
        if styling.get("effects"):
            desc += " has-shadow"
        if styling.get("fills"):
            desc += " has-fill"
        if styling.get("textStyle"):
            desc += f" fs={styling['textStyle'].get('fontSize', '?')}"
        lines.append(desc)
        for child in node.get("children", []):
            walk(child, depth + 1)

    for n in ir_tree:
        walk(n)
    return "\n".join(lines) or "No IR tree available."


def _run_utility_class_research_phase(
    llm: Any,
    design_summary: str,
    ds_name: str,
    mapped_components: Optional[List[str]] = None,
) -> str:
    """Phase 1: LLM calls tools to gather utility classes AND component docs.

    Runs when either DOC_KNOWLEDGE is loaded (utility class research) or
    DS_CATALOG_ENTRY_MAP is populated (component docs research).

    Args:
        llm:               ChatOpenAI instance (no tools bound yet).
        design_summary:    Compact outline of the IR tree.
        ds_name:           Human-readable design system name.
        mapped_components: List of DS component names/selectors matched during
                           the mapping step (non-native only).  Used to drive
                           proactive component docs fetching.

    Returns a plain-text context block to embed in the code-gen prompt,
    or '' if there is nothing to research.
    """
    has_knowledge = bool(DOC_KNOWLEDGE)
    has_components = bool(mapped_components and DS_CATALOG_ENTRY_MAP)
    if not has_knowledge and not has_components:
        return ""

    tool_llm = llm.bind_tools(_PHASE1_TOOLS_LIST)

    # Build the components list section for the prompt
    if mapped_components:
        components_list = "\n".join(f"  - {c}" for c in sorted(set(mapped_components)))
        components_section = f"""
PART B — COMPONENT DOCUMENTATION
These {ds_name} components were matched to nodes in this design:
{components_list}

For EACH component above, call fetch_component_docs in this order:
  1. fetch_component_docs("<name>", "api")      — inputs, outputs, selectors
  2. fetch_component_docs("<name>", "usage")    — real usage examples
  3. fetch_component_docs("<name>", "overview") — only if api+usage are unclear
Skip doc types that won't add new information (e.g., if api already has examples).
"""
    else:
        components_section = ""

    utility_section = ""
    if has_knowledge:
        utility_section = """
PART A — UTILITY CLASS RESEARCH
Call search_doc_knowledge in this order (skip concerns absent from the design):
  1. search_doc_knowledge("flex", "layout")
  2. search_doc_knowledge("grid", "layout")          — only if design uses grid
  3. search_doc_knowledge("gap spacing", "layout")
  4. search_doc_knowledge("padding", "utilities")
  5. search_doc_knowledge("shadow", "utilities")
  6. search_doc_knowledge("background surface", "utilities")
  7. search_doc_knowledge("text color", "utilities")
  8. search_doc_knowledge("font size text", "content")
  9. search_doc_knowledge("font size", "utilities")
 10. search_doc_knowledge("font weight", "utilities")
 11. fetch_doc_page(url) only if a result needs deeper clarification

After the searches, produce a TYPOGRAPHY MAPPING TABLE:
For each unique font size listed in "Typography sizes in design" above, find the
closest utility class. Format exactly as:
  64px → <class or "no match — use SCSS">
  32px → <class or "no match — use SCSS">
  ...
"""

    system_prompt = f"""You are a {ds_name} documentation researcher.

TASK: Before the Angular component is generated, gather all information the
developer needs to use {ds_name} correctly for the design described below.
{utility_section}{components_section}
FINAL OUTPUT — two clearly labelled sections:

## Utility Classes
(Only if Part A applies — omit section if no knowledge base loaded)
- Layout: <classes and what they do>
- Spacing: <classes>
- Color/surface: <classes>
- Shadow: <classes>
- Typography: <class per font size — e.g. "64px → text-7xl, 32px → text-4xl, ...">

## Component APIs
(One subsection per component)
### <ComponentName> (<selector>)
- Key inputs: <input>[type] — description
- Usage example: <code snippet>
- Severity/variant classes: <if applicable>

RULES:
- Only report classes/APIs you actually found via tool calls. Do NOT invent.
- Be concise — the developer needs just enough to write correct code.
- If a tool returns an error or empty result, note it briefly and move on.

Design being implemented:
{design_summary}"""

    messages: List[Any] = [
        SystemMessage(content=system_prompt),
        HumanMessage(content="Research the documentation now, then provide your summary."),
    ]

    # Tool dispatcher map for clean routing
    tool_dispatch = {
        "search_doc_knowledge": search_doc_knowledge,
        "fetch_doc_page":       fetch_doc_page,
        "fetch_component_docs": fetch_component_docs,
    }

    for iteration in range(_MAX_TOOL_ITERATIONS):
        response = tool_llm.invoke(messages)
        messages.append(response)

        if not response.tool_calls:
            print(f"  Phase 1: done in {iteration + 1} iteration(s), "
                  f"context={len(response.content)} chars")
            return response.content

        print(f"  Phase 1 iteration {iteration + 1}: "
              f"{len(response.tool_calls)} tool call(s) "
              f"({', '.join(tc['name'] for tc in response.tool_calls)})")

        for tc in response.tool_calls:
            tool_fn = tool_dispatch.get(tc["name"])
            if tool_fn is not None:
                result = tool_fn.invoke(tc["args"])
            else:
                result = f"Unknown tool: {tc['name']}"
            messages.append(ToolMessage(content=str(result), tool_call_id=tc["id"]))

    # Hit iteration cap — use last AI message content
    last = next(
        (m.content for m in reversed(messages) if isinstance(m, AIMessage)),
        "",
    )
    print(f"  Phase 1: hit max iterations, using last AI content ({len(last)} chars)")
    return last


# ============================================================================
# STEP 4b: TYPOGRAPHY REFINEMENT HELPERS
# ============================================================================

class _RefinedFiles(BaseModel):
    html: str
    scss: str


def _flatten_knowledge_for_refinement() -> str:
    """Return a compact flat list of all utility classes from DOC_KNOWLEDGE."""
    if not DOC_KNOWLEDGE:
        return ""
    lines = []
    for sec_name, sec_data in DOC_KNOWLEDGE.get("sections", {}).items():
        for entry_key, entry_data in sec_data.items():
            classes = entry_data.get("classes", {})
            if classes:
                lines.append(f"[{sec_name}/{entry_key}]")
                for cls, desc in list(classes.items())[:60]:
                    lines.append(f"  .{cls} — {desc}")
    return "\n".join(lines)


def _run_typography_refinement(
    llm: Any,
    html_content: str,
    scss_content: str,
    ds_name: str,
) -> tuple:
    """Phase 2: Replace inline typography/layout styles with utility or SCSS classes."""
    class_reference = _flatten_knowledge_for_refinement()

    system = f"""You are an Angular template refactoring expert for {ds_name}.

TASK: Rewrite the HTML and SCSS so that NO inline style= attributes remain for
typography properties (font-size, font-weight, font-family, line-height,
letter-spacing, text-align, color, opacity) or for layout properties that have a
matching utility class (flex, gap, padding, margin, background-color,
border-radius, box-shadow).

RULES:
1. If a utility class for the value exists in AVAILABLE CLASSES — add it to class=""
2. If no utility class exists — move the style to a descriptive SCSS class
   (e.g. .hero-title, .body-small, .card-label) and reference that class in HTML
3. Do NOT change text content, component selectors, or Angular bindings
4. Return valid, complete HTML and SCSS (do not truncate)
5. Preserve all existing class="" values — only ADD to them, never remove

AVAILABLE UTILITY CLASSES FROM {ds_name.upper()} DOCS:
{class_reference or "(none loaded — move all remaining inline styles to named SCSS classes)"}
"""

    human = (
        f"Refactor this Angular template and SCSS.\n\n"
        f"=== HTML ===\n{html_content}\n\n"
        f"=== SCSS ===\n{scss_content}"
    )

    structured = llm.with_structured_output(_RefinedFiles)
    try:
        result = structured.invoke([SystemMessage(content=system), HumanMessage(content=human)])
        print(f"  Phase 2: refinement done "
              f"(html {len(html_content)}→{len(result.html)} chars, "
              f"scss {len(scss_content)}→{len(result.scss)} chars)")
        return result.html, result.scss
    except Exception as exc:
        print(f"  Warning: typography refinement failed ({exc}), using original output")
        return html_content, scss_content


# ============================================================================
# STEP 4: GENERATE ANGULAR CODE
# ============================================================================

def generate_angular_code_node(state: AgentState) -> AgentState:
    """Step 4: Generate Angular component code from the IR and mappings."""
    METRICS.start_step("generate_code")

    ir_tree = state.get("ir_tree", [])
    figma_json = state.get("figma_json", {})
    mappings = state.get("component_mappings") or []

    if not ir_tree and figma_json:
        print("Warning: IR tree empty, using Figma JSON directly for code generation")
        ir_tree = [figma_json]

    if not ir_tree:
        state["validation_errors"].append(
            ValidationError(file_path="generation", error_type="no_data",
                            message="No IR tree or Figma data available for code generation")
        )
        return state

    llm = ChatOpenAI(model=Config.LLM_MODEL, temperature=Config.LLM_TEMPERATURE)
    structured_llm = llm.with_structured_output(GeneratedAngularArtifact)

    # ── Phase 1: utility class + component docs research ──────────────────
    ds_config_pre = state.get("ds_config") or {}
    ds_name_pre = ds_config_pre.get("name", "the design system")

    # Collect non-native DS components matched in this design
    matched_ds_components: List[str] = sorted({
        (m.model_dump() if hasattr(m, "model_dump") else m)["ds_component"]
        for m in mappings
        if (m.model_dump() if hasattr(m, "model_dump") else m).get("ds_component", "native") != "native"
    })

    has_phase1_work = bool(DOC_KNOWLEDGE) or bool(matched_ds_components and DS_CATALOG_ENTRY_MAP)
    utility_classes_context = ""
    if has_phase1_work:
        try:
            METRICS.start_step("phase1_research")
            design_summary = _build_phase1_design_summary(ir_tree)
            print(f"Phase 1: researching utility classes + "
                  f"{len(matched_ds_components)} component(s): "
                  f"{', '.join(matched_ds_components) or 'none'}")
            utility_classes_context = _run_utility_class_research_phase(
                llm=llm,
                design_summary=design_summary,
                ds_name=ds_name_pre,
                mapped_components=matched_ds_components if matched_ds_components else None,
            )
            METRICS.end_step("phase1_research")
            METRICS.record_llm_call(
                len(design_summary), len(utility_classes_context), "phase1_research"
            )
        except Exception as exc:
            print(f"Warning: Phase 1 failed ({exc}), proceeding without utility context.")
            utility_classes_context = ""
    else:
        print("Phase 1 skipped (no knowledge.json and no DS components mapped).")

    # Store Phase 1 context in state for session persistence (Enhancement I/F)
    state["phase1_research_context"] = utility_classes_context

    # Derive component name
    root_name = "GeneratedComponent"
    raw_name = (figma_json.get("name") if figma_json else None) or (ir_tree[0].get("name") if ir_tree else None)
    if raw_name:
        clean_name = "".join(
            word.capitalize()
            for word in raw_name.replace("-", " ").replace("_", " ")
                                .replace("*", " ").replace("/", " ").split()
        )
        clean_name = "".join(c for c in clean_name if c.isalnum())
        if clean_name and clean_name != "Document":
            root_name = clean_name + "Component"

    design_structure = _build_design_structure_for_codegen(figma_json, ir_tree, mappings)

    # Screenshot analysis (optional)
    screenshot_styling = None
    screenshot_url = (state.get("figma_screenshots") or {}).get("main")
    if not screenshot_url:
        screenshot_url = (state.get("original_figma_json") or {}).get("thumbnailUrl")
    if screenshot_url and Config.USE_SCREENSHOT_ANALYSIS:
        print("Found screenshot URL, analyzing for styling...")
        screenshot_styling = analyze_screenshot_for_styling(
            screenshot_url, json.dumps(design_structure, indent=2)[:3000]
        )

    # DS metadata from state
    ds_config = state.get("ds_config") or {}
    catalog = ds_config.get("_catalog", {})
    ds_name = ds_config.get("name", "the design system")
    ds_prefix = ds_config.get("prefix", "ds")
    code_gen_mappings_section = build_catalog_code_gen_prompt(catalog) if catalog else ""
    import_example_section = build_catalog_import_example(catalog) if catalog else ""

    # Build the exhaustive whitelist of allowed custom element selectors for the system prompt
    _custom_selectors = [
        c["selector"] for c in (catalog.get("components", []) if catalog else [])
        if c.get("selector") and c["selector"].lower() not in _NATIVE_HTML_TAGS
    ]
    allowed_tags_str = "  ".join(f"<{s}>" for s in _custom_selectors) if _custom_selectors else "(none)"

    # Build research context section for the prompt (Phase 1 output)
    utility_classes_section = ""
    if utility_classes_context:
        utility_classes_section = f"""
## {ds_name.upper()} DOCUMENTATION RESEARCH (Phase 1 findings)
The following was gathered from the {ds_name} documentation before code generation.
USE THIS INFORMATION to write correct, idiomatic {ds_name} code:

{utility_classes_context}

MANDATORY RULES:
- Apply utility classes via the HTML `class` attribute FIRST; write SCSS only for styles
  not covered by a utility class found above
- Use component inputs/outputs EXACTLY as described in the Component APIs section above
- Exact Figma pixel values with no matching utility class may remain in SCSS
- Prefer `class="flex flex-column gap-3 p-4"` over custom SCSS that does the same thing
- The SCSS file should shrink significantly — only truly bespoke styles remain
"""

    system_prompt = f"""You are an expert Angular developer. Generate a complete Angular component from a Figma design.

CRITICAL INSTRUCTIONS:
1. Analyze the design structure carefully - it contains the EXACT layout and content from Figma
2. Generate code that MATCHES this specific design - NOT generic placeholder code
3. Every text node in the design should appear in your HTML template
4. Every container/frame should be represented with proper flexbox/grid layout
5. **MAXIMIZE {ds_name} component usage** - prefer {ds_name} components over raw HTML in every case

The design structure contains:
- name: The Figma layer name (DO NOT use as display text — names like "Button", "Base", "Frame" are internal Figma layer names)
- type: FRAME, TEXT, GROUP, COMPONENT, etc.
- layout: layoutMode (VERTICAL/HORIZONTAL), alignment info
- children: Nested child elements
- text: Actual text content for TEXT nodes
- innerText: Aggregated text from descendant TEXT nodes — use this as the element's display text
- styling: Colors, fonts, effects

CRITICAL TEXT RULES:
- When a node has an "innerText" field, use that as the element's text content (e.g., button label)
- When a node has a "text" field, use that as the element's text content
- NEVER use the node "name" as display text — names like "Button", "Base", "Frame 1037" are Figma layer names, NOT user-visible text
- Actual display text comes ONLY from "text" or "innerText" fields

## TEXT NODE RULES (ABSOLUTE):
- Every IR node with type="text" maps to a NATIVE HTML tag (p, h1, h2, span), never a DS component
- Use the ds_selector value from component_hierarchy_with_ds_mappings as the tag
- INLINE TYPOGRAPHY STYLES ARE FORBIDDEN:
    Do NOT write style="font-size: ...", style="font-weight: ...",
    style="line-height: ...", or style="letter-spacing: ..." on ANY element
- INSTEAD: apply typography utility classes from Phase 1 (e.g. class="text-7xl font-medium")
- If no utility class was found for a size, add a descriptive SCSS class
  (e.g. .hero-title, .card-subtitle) and define it in the SCSS file
- color/opacity for text: use a utility class if one was found, otherwise SCSS

{code_gen_mappings_section}
{utility_classes_section}
GENERATE:

1. TypeScript Component (standalone):
{import_example_section}

2. HTML Template - MUST include ALL text and structure from the design:
- Use {ds_name} components for every UI element where a matching component exists
- For ds_component="native" nodes: use the ds_selector value directly as the HTML tag
- For each node, resolved_classes and resolved_directives are pre-computed from the catalog:
  - If resolved_classes is non-empty: apply them as the element's class attribute
    (e.g. <button class="lmn-btn lmn-btn-primary">)
  - If resolved_directives is non-empty: emit each as a bare attribute on the element
    (e.g. <button mtButton class="lmn-btn lmn-btn-primary">)
  - These values are authoritative — use them exactly, do not guess or add extra classes
- DIRECTIVE-BASED COMPONENTS: When ds_selector is a native HTML element (button, input, select, etc.)
  and resolved_directives is non-empty, use the native tag + directives + classes. Do NOT wrap in a
  custom element. Example: <button mtButton class="mt-btn mt-btn-primary">Label</button>
- If a node has an inner_html_note in component_hierarchy_with_ds_mappings, follow it exactly
  for the component's inner content

CRITICAL — TAG RESTRICTIONS (read carefully):
- The ONLY {ds_name} custom element tags you may use are: {allowed_tags_str}
- NEVER invent child component tags. If a tag is not in the list above, do NOT use it.
  Bad examples: <mt-segment>, <mt-option>, <p-item>, <ds-tab-item> — these are hallucinations.
- For components that manage their own inner content (selects, dropdowns, segmented controls,
  autocompletes, tab groups): pass items/options as @Input() arrays or objects — do NOT
  add inner component tags or option elements. Close the tag with no children, or use
  only native HTML inside if the inner_html_note explicitly says to.

- Include ALL text content from the design
- Use utility classes from the {ds_name} documentation (listed above) for layout, spacing, and color
- Use flexbox fallback classes "flex-row"/"flex-column" ONLY if no {ds_name} layout utility class was found

3. SCSS Styles:
- Typography (font-size, font-weight, line-height, letter-spacing) MUST use
  utility classes OR named SCSS classes — NEVER inline style= attributes
- Layout (flex, gap, padding) → utility class first; custom SCSS only if no match
- .flex-row / .flex-column ONLY if no layout utility class was found

4. ds_components_used: Populate this array with EVERY {ds_name} component used in the template.
   For each component, include: figma_node_id, ds_component name, ds_selector, inputs, outputs.

IMPORTANT:
- DO NOT generate placeholder text like "Sample Component" or "This is a sample"
- USE the ACTUAL text content from the design structure
- PRESERVE the visual hierarchy exactly as shown in the design
- Apply the EXACT styling from the style properties (padding, gap, colors, fonts)
- MAXIMIZE {ds_name} component usage - every UI element should use a {ds_name} component if one exists
- The TypeScript file MUST import the module for every {ds_prefix}-* element used in the HTML"""

    if screenshot_styling:
        system_prompt += """

VISUAL STYLING FROM SCREENSHOT ANALYSIS:
You have been provided with a detailed styling analysis from the actual Figma screenshot.
Use these EXACT values in your SCSS:

Colors: Use the exact hex codes provided
Spacing: Use the exact padding and gap values
Typography: Match the font sizes, weights, and line heights
Effects: Apply shadows and border-radius as specified

This visual analysis takes precedence for styling."""

    design_json = json.dumps(design_structure, indent=2)
    if len(design_json) > Config.MAX_JSON_SIZE:
        design_json = json.dumps(design_structure)[:Config.MAX_JSON_SIZE]

    available_ds_components = list(DS_CATALOG.get("components", {}).keys())
    figma_lookup = _build_figma_node_lookup(figma_json) if figma_json else {}
    component_hierarchy = _build_component_hierarchy_context(ir_tree, mappings, figma_lookup=figma_lookup)

    context: Dict[str, Any] = {
        "component_name": root_name,
        "design_structure": design_structure,
        "component_hierarchy_with_ds_mappings": component_hierarchy,
        "mappings_count": len(mappings),
        "available_ds_components": available_ds_components,
    }
    if screenshot_styling:
        context["visual_styling_from_screenshot"] = screenshot_styling

    context_str = json.dumps(context, indent=2)
    print(f"\n=== CODE GENERATION CONTEXT ===")
    print(f"Component name: {root_name}")
    print(f"Context size:   {len(context_str)} chars")
    print(f"=== END CONTEXT ===\n")

    messages = [
        SystemMessage(content=system_prompt),
        HumanMessage(content=f"Generate Angular component '{root_name}' from this Figma design:\n\n{context_str}"),
    ]

    try:
        print(f"Generating Angular component: {root_name}...")
        generated = structured_llm.invoke(messages)
        METRICS.record_llm_call(
            len(context_str),
            sum(len(f.content) for f in generated.files),
            "generate_code",
        )
        state["generated"] = generated
        state["messages"].append(AIMessage(content=f"Generated {len(generated.files)} files for {root_name}"))
        print(f"Generated {len(generated.files)} files:")
        for f in generated.files:
            print(f"  - {f.path} ({len(f.content)} chars)")
    except Exception as e:
        state["validation_errors"].append(
            ValidationError(file_path="generation", error_type="generation_error",
                            message=f"Code generation failed: {str(e)}")
        )

    METRICS.end_step("generate_code")
    return state


# ============================================================================
# STEP 4b: REFINE TYPOGRAPHY
# ============================================================================

def _rewrite_ts_imports(
    ts_content: str,
    import_path_prefix: str,
    ds_import_statements: List[str],
    component_classes: List[str],
    all_ds_class_names: set,
) -> str:
    """Rewrite TypeScript: replace DS imports, fix @Component.imports array.

    Keeps @angular/* and other non-DS import lines intact.
    Drops stale DS import lines (matched by import_path_prefix in the from-path).
    Inserts correct sorted DS import statements.
    Rebuilds imports:[...] keeping non-DS class names + adding component_classes.
    """
    lines = ts_content.splitlines()
    non_ds_imports, body_lines = [], []
    past_imports = False

    for line in lines:
        stripped = line.strip()
        if not past_imports and stripped.startswith("import "):
            # Keep non-DS imports (Angular, etc.); drop existing DS-specific imports
            if (f"'{import_path_prefix}/" not in line and
                    f'"{import_path_prefix}/' not in line):
                non_ds_imports.append(line)
        else:
            past_imports = True
            body_lines.append(line)

    body_str = "\n".join(body_lines)

    # Rebuild imports: [...] — keep non-DS class names, append component_classes
    m = re.search(r'imports:\s*\[([^\]]*)\]', body_str, re.DOTALL)
    if m:
        existing = [c.strip() for c in m.group(1).split(',') if c.strip()]
        keep = [c for c in existing if c not in all_ds_class_names]
    else:
        keep = []
    if "CommonModule" not in keep:
        keep.insert(0, "CommonModule")

    seen = set(keep)
    for cls in component_classes:
        if cls not in seen:
            keep.append(cls)
            seen.add(cls)

    body_str = re.sub(
        r'imports:\s*\[([^\]]*)\]',
        f'imports: [{", ".join(keep)}]',
        body_str,
        flags=re.DOTALL,
    )

    ds_lines = sorted(ds_import_statements)
    return "\n".join(non_ds_imports + [""] + ds_lines + [""] + body_str.splitlines())

def refine_typography_node(state: AgentState) -> AgentState:
    """Step 4b: Post-process — replace inline typography with classes."""
    METRICS.start_step("refine_typography")
    generated = state.get("generated")
    if not generated or not generated.files:
        METRICS.end_step("refine_typography")
        return state

    ds_name = (state.get("ds_config") or {}).get("name", "the design system")
    llm = ChatOpenAI(model=Config.LLM_MODEL, temperature=0.0)

    html_file = next((f for f in generated.files if f.path.endswith(".html")), None)
    scss_file = next((f for f in generated.files if f.path.endswith(".scss")), None)

    if not html_file:
        METRICS.end_step("refine_typography")
        return state

    print("Phase 2: typography refinement...")
    html_out, scss_out = _run_typography_refinement(
        llm, html_file.content, scss_file.content if scss_file else "", ds_name
    )
    METRICS.record_llm_call(
        len(html_file.content) + len(scss_file.content if scss_file else ""),
        len(html_out) + len(scss_out),
        "refine_typography",
    )

    new_files = []
    for f in generated.files:
        if f.path == html_file.path:
            new_files.append(GeneratedFile(path=f.path, content=html_out))
        elif scss_file and f.path == scss_file.path:
            new_files.append(GeneratedFile(path=f.path, content=scss_out))
        else:
            new_files.append(f)

    state["generated"] = GeneratedAngularArtifact(
        component_name=generated.component_name,
        files=new_files,
        ds_components_used=generated.ds_components_used,
        imports=generated.imports,
        unresolved_nodes=generated.unresolved_nodes,
    )
    METRICS.end_step("refine_typography")
    return state


# ============================================================================
# STEP 4c: FIX IMPORTS / DIRECTIVES
# ============================================================================

def fix_imports_directives_node(state: AgentState) -> AgentState:
    """Step 4c: Deterministically fix TS imports and @Component.imports from catalog.

    1. Collects used DS selectors from ds_components_used.
    2. Scans the HTML file for directive attribute selectors (e.g. pButton, pInputText).
    3. Looks up import_statement and component_classes for each match in the catalog.
    4. Rewrites the TypeScript file with exact import lines and @Component.imports array.

    Skips gracefully if no catalog entries have import_statement populated
    (e.g. custom DS catalog that hasn't filled in the new fields yet).
    """
    METRICS.start_step("fix_imports")
    generated = state.get("generated")
    if not generated or not generated.files:
        METRICS.end_step("fix_imports")
        return state

    catalog_entries = state.get("ds_catalog_entries") or []
    if not any(e.get("import_statement") for e in catalog_entries):
        METRICS.end_step("fix_imports")
        return state

    ds_config = state.get("ds_config") or {}
    import_path_prefix = ds_config.get("import_path", "")
    entry_by_selector = {e["selector"]: e for e in catalog_entries if e.get("selector")}

    all_ds_class_names = {
        cls for e in catalog_entries for cls in e.get("component_classes", [])
    }

    html_file = next((f for f in generated.files if f.file_type == "html"), None)
    ts_file = next((f for f in generated.files if f.file_type == "typescript"), None)
    if not ts_file:
        METRICS.end_step("fix_imports")
        return state

    used_selectors = {
        m.ds_selector for m in (generated.ds_components_used or [])
        if m.ds_selector and m.ds_selector != "native"
    }

    import_statements: set = set()
    component_classes: list = []
    seen_classes: set = set()

    def _collect(entry: Dict):
        stmt = entry.get("import_statement")
        if stmt:
            import_statements.add(stmt)
        for cls in entry.get("component_classes", []):
            if cls not in seen_classes:
                component_classes.append(cls)
                seen_classes.add(cls)

    # Pass 1: components identified by DS mapper
    for sel in used_selectors:
        if sel in entry_by_selector:
            _collect(entry_by_selector[sel])

    # Pass 2: directives found in HTML (e.g. pButton, pInputText as attributes)
    if html_file:
        for entry in catalog_entries:
            for directive in entry.get("directives", []):
                dir_sel = directive.get("selector", "")
                if dir_sel and re.search(r'\b' + re.escape(dir_sel) + r'\b', html_file.content):
                    _collect(entry)
                    break  # one directive match per entry is sufficient

    if not import_statements:
        METRICS.end_step("fix_imports")
        return state

    new_ts = _rewrite_ts_imports(
        ts_file.content, import_path_prefix,
        list(import_statements), component_classes, all_ds_class_names,
    )

    new_files = [
        GeneratedFile(path=f.path, content=new_ts, file_type=f.file_type)
        if f.file_type == "typescript" else f
        for f in generated.files
    ]
    state["generated"] = GeneratedAngularArtifact(
        component_name=generated.component_name,
        files=new_files,
        ds_components_used=generated.ds_components_used,
        imports=component_classes,
        unresolved_nodes=generated.unresolved_nodes,
    )
    print(f"fix_imports: {len(import_statements)} DS import(s), classes: {component_classes}")
    METRICS.end_step("fix_imports")
    return state


# ============================================================================
# STEP 5: VALIDATE
# ============================================================================

def validate_node(state: AgentState) -> AgentState:
    """Step 5: Validate generated code."""
    METRICS.start_step("validate")
    errors = []
    generated = state.get("generated")

    print(f"Validating generated code (repair attempt: {state.get('repair_attempt', 0)})...")

    if not generated:
        errors.append(ValidationError(
            file_path="", error_type="missing_generation",
            message="No generated code to validate"
        ))
        state["validation_errors"] = errors
        return state

    if not generated.files:
        errors.append(ValidationError(
            file_path="", error_type="no_files",
            message="No files were generated"
        ))

    ds_config = state.get("ds_config") or {}
    ds_prefix = ds_config.get("prefix", "")
    ds_name = ds_config.get("name", "the design system")

    known_selectors = set(DS_CATALOG.get("components", {}).keys())
    known_selectors.update(DS_CATALOG.get("directives", {}).keys())
    standard_html = {
        "div", "span", "p", "h1", "h2", "h3", "h4", "h5", "h6",
        "button", "input", "img", "a", "ul", "li", "section",
        "header", "footer", "nav", "main", "article",
    }

    warnings = []
    for mapping in generated.ds_components_used:
        selector = mapping.ds_selector
        is_known_ds = selector in known_selectors or (ds_prefix and ds_prefix in selector)
        is_standard_html = selector in standard_html
        if not is_known_ds and not is_standard_html:
            warnings.append(f"Unknown selector: {selector}")

    if warnings:
        print(f"  Validation warnings: {warnings[:3]}")

    html_files = [
        f for f in generated.files
        if f.file_type in ("html", "template") or f.path.endswith(".html")
    ]
    for html_file in html_files:
        content = html_file.content
        ds_count = 0
        if ds_prefix:
            ds_count = (content.count(f"{ds_prefix}-") +
                        content.count(f"{ds_prefix}[") +
                        content.count(f"[{ds_prefix}"))
        for sel in list(known_selectors)[:50]:
            if sel in content:
                ds_count += content.count(sel)

        tag_count = max(content.count("<") - content.count("</") - content.count("<!--"), 1)
        ds_ratio = ds_count / tag_count
        print(f"  DS usage in {html_file.path}: {ds_count} {ds_name} refs / ~{tag_count} elements ({ds_ratio:.0%})")

        if ds_count < 2 and tag_count > 5:
            errors.append(ValidationError(
                file_path=html_file.path,
                error_type="low_ds_usage",
                message=(f"Low {ds_name} usage: only {ds_count} component references "
                         f"found in {tag_count} elements. Maximize {ds_name} component usage.")
            ))
            print(f"  WARNING: Low {ds_name} usage, will trigger repair")

    state["validation_errors"] = errors
    if errors:
        print(f"  Validation errors: {[e.message for e in errors]}")
    else:
        print("  Validation passed!")

    METRICS.end_step("validate")
    return state


# ============================================================================
# STEP 6: REPAIR
# ============================================================================

def repair_node(state: AgentState) -> AgentState:
    """Step 6: Repair code based on validation errors."""
    METRICS.start_step("repair")
    state["repair_attempt"] = state.get("repair_attempt", 0) + 1
    print(f"Attempting repair #{state['repair_attempt']}...")

    if not state.get("component_mappings") and not state.get("ir_tree"):
        print("  No mappings or IR tree available, skipping repair")
        return state

    llm = ChatOpenAI(model=Config.LLM_MODEL, temperature=Config.LLM_TEMPERATURE)
    structured_llm = llm.with_structured_output(GeneratedAngularArtifact)

    errors_summary = "\n".join([
        f"- {err.file_path}: {err.message}"
        for err in state.get("validation_errors", [])
    ])

    mappings = state.get("component_mappings") or []
    mappings_data = [
        (m.model_dump() if hasattr(m, "model_dump") else m.dict() if hasattr(m, "dict") else m)
        for m in mappings[:20]
    ]

    current_files = {
        f.path: f.content
        for f in (state.get("generated") or GeneratedAngularArtifact(
            component_name="", files=[], ds_components_used=[], imports=[]
        )).files
    }

    # Build repair hints from catalog
    ds_config = state.get("ds_config") or {}
    catalog = ds_config.get("_catalog", {})
    ds_name = ds_config.get("name", "the design system")

    enforcement = build_ds_enforcement_system_prompt(catalog, ds_name) if catalog else ""
    text_node_rule = (
        f"\n## TEXT NODE RULES (ABSOLUTE):\n"
        f"- type='text' nodes use native HTML tags (h1–h6, p) — NEVER a DS component\n"
        f"- Every {ds_name} module used in the template MUST be imported in the TypeScript file\n"
        f"- Populate ds_components_used with ALL {ds_name} components used"
    )

    repair_prompt = f"""Previous generation had errors:

{errors_summary}

Context (component mappings):
{json.dumps(mappings_data, indent=2)}

Current generated files:
{json.dumps(current_files, indent=2)[:50000]}

Fix ALL errors and regenerate complete Angular component code.
Generate proper TypeScript, HTML template, and SCSS files.
{text_node_rule}"""

    repair_messages = [HumanMessage(content=repair_prompt)]
    if enforcement:
        repair_messages = [SystemMessage(content=enforcement)] + repair_messages

    try:
        repair_input_chars = len(repair_prompt)
        repaired = structured_llm.invoke(repair_messages)
        METRICS.record_llm_call(
            repair_input_chars,
            sum(len(f.content) for f in repaired.files),
            "repair",
        )
        state["generated"] = repaired
        state["messages"].append(
            AIMessage(content=f"Repair attempt {state['repair_attempt']} complete")
        )
        print(f"  Repair generated {len(repaired.files)} files")
    except Exception as e:
        print(f"  Repair failed: {str(e)}")
        state["messages"].append(
            AIMessage(content=f"Repair attempt {state['repair_attempt']} failed: {str(e)}")
        )

    METRICS.end_step("repair")
    return state


def should_repair(state: AgentState) -> str:
    errors = state.get("validation_errors", [])
    repair_attempt = state.get("repair_attempt", 0)
    print(f"Checking repair: {len(errors)} errors, attempt {repair_attempt}/{Config.MAX_REPAIR_ATTEMPTS}")
    if not errors:
        print("  -> No errors, completing workflow")
        return "complete"
    if repair_attempt >= Config.MAX_REPAIR_ATTEMPTS:
        print(f"  -> Max repair attempts reached, completing with {len(errors)} unresolved errors")
        return "complete"
    print(f"  -> Attempting repair #{repair_attempt + 1}")
    return "repair"


# ============================================================================
# WORKFLOW GRAPH
# ============================================================================

def create_workflow() -> StateGraph:
    workflow = StateGraph(AgentState)

    workflow.add_node("ingest_figma", ingest_figma_node)
    workflow.add_node("prune_figma_tree", prune_figma_tree_node)   # Step 1b: structural pruning
    workflow.add_node("build_ir", build_ir_node)
    workflow.add_node("map_to_ds", map_to_design_system_node)
    workflow.add_node("generate_code", generate_angular_code_node)
    workflow.add_node("refine_typography", refine_typography_node)
    workflow.add_node("fix_imports", fix_imports_directives_node)
    workflow.add_node("validate", validate_node)
    workflow.add_node("repair", repair_node)

    workflow.set_entry_point("ingest_figma")
    workflow.add_edge("ingest_figma", "prune_figma_tree")   # → pruning (was → build_ir)
    workflow.add_edge("prune_figma_tree", "build_ir")        # → IR generation
    workflow.add_edge("build_ir", "map_to_ds")
    workflow.add_edge("map_to_ds", "generate_code")
    workflow.add_edge("generate_code", "refine_typography")
    workflow.add_edge("refine_typography", "fix_imports")
    workflow.add_edge("fix_imports", "validate")
    workflow.add_conditional_edges(
        "validate",
        should_repair,
        {"repair": "repair", "complete": END},
    )
    workflow.add_edge("repair", "refine_typography")

    print("create_workflow: before compile")
    return workflow.compile()


# ============================================================================
# GENERATION HELPERS (screenshot / prompt path + iterative refinement)
# ============================================================================

def generate_html_from_input(
    prompt: Optional[str] = None,
    screenshot_path: Optional[str] = None,
) -> tuple:
    """Step 1 of the screenshot/prompt path.

    Returns (html: str, css: str) describing the UI.
    At least one of prompt or screenshot_path must be provided.
    """
    llm = ChatOpenAI(
        model=Config.LLM_MODEL,
        temperature=0.1,
        api_key=Config.OPENAI_API_KEY,
    )

    system = """You are a UI developer. Given a design screenshot and/or description,
produce clean semantic HTML and CSS that faithfully reproduces the layout, content,
and visual structure. Use class names that reflect component semantics (e.g. btn,
card, input-field). Do NOT use inline styles — put all styles in the CSS block.

Return ONLY a JSON object:
{"html": "<full html string>", "css": "<full css string>"}"""

    content_parts: List[Any] = []
    if prompt:
        content_parts.append({"type": "text", "text": f"Design description: {prompt}"})
    if screenshot_path:
        b64 = fetch_image_as_base64(screenshot_path)
        if b64:
            content_parts.append({
                "type": "image_url",
                "image_url": {"url": b64, "detail": "high"},
            })
    if not content_parts:
        raise ValueError("At least one of prompt or screenshot_path must be provided")

    METRICS.record_llm_call(len(str(content_parts)), 0, "html_from_input")
    response = llm.invoke([
        SystemMessage(content=system),
        HumanMessage(content=content_parts),
    ])
    if METRICS.log_trace:
        METRICS.log_trace[-1]["message"] += f" / {len(response.content)} out chars"

    parsed = _parse_llm_json_response(response.content)
    return parsed.get("html", ""), parsed.get("css", "")


def html_to_figma_tree(html: str, css: str, name: str = "Generated Design") -> Dict:
    """Convert native HTML + CSS into a synthetic Figma-like JSON tree.

    The output matches the minimal Figma export format that ingest_figma_node
    and build_ir_node expect: document → PAGE → children tree.
    Each node has: id, name, type (FRAME/TEXT/INSTANCE/GROUP), children,
    and a styling block (fills, textStyle, effects, etc.) inferred from the CSS.
    """
    llm = ChatOpenAI(
        model=Config.LLM_MODEL,
        temperature=0.0,
        api_key=Config.OPENAI_API_KEY,
    )

    system = """You are converting an HTML/CSS document into a Figma-like JSON tree.

Rules:
- Root wrapper is a PAGE node with one FRAME child (the screen)
- Block elements → FRAME nodes
- Text nodes → TEXT nodes with textStyle (fontSize, fontWeight, color)
- Interactive elements (button, input, select, checkbox) → INSTANCE nodes
  with componentType matching the element type
- Use semantic names from class names or element types
- Extract fills (background-color), strokes (border), effects (box-shadow)
  from the CSS and embed them in each node's styling object
- Assign sequential integer ids as strings ("1", "2", ...)
- Every node must have: id, name, type, children ([] for leaves), styling {}

Return ONLY valid JSON — no markdown fences."""

    prompt = f"""HTML:
```html
{html}
```

CSS:
```css
{css}
```

Produce the full Figma-like JSON tree. Wrap it in the standard Figma export envelope:
{{
  "name": "{name}",
  "document": {{
    "id": "0",
    "name": "Document",
    "type": "DOCUMENT",
    "children": [{{
      "id": "1",
      "name": "Page 1",
      "type": "PAGE",
      "children": [ ... ]
    }}]
  }},
  "components": {{}},
  "styles": {{}},
  "schemaVersion": 0
}}"""

    METRICS.record_llm_call(len(system) + len(prompt), 0, "html_to_figma_tree")
    response = llm.invoke([
        SystemMessage(content=system),
        HumanMessage(content=prompt),
    ])
    if METRICS.log_trace:
        METRICS.log_trace[-1]["message"] += f" / {len(response.content)} out chars"
    return _parse_llm_json_response(response.content)


def generate_from_prompt(
    design_system: str,
    prompt: Optional[str] = None,
    screenshot_path: Optional[str] = None,
    design_tokens: Optional[Dict] = None,
) -> "GeneratedAngularArtifact":
    """Backward-compatible wrapper for run_agent.py. Returns artifact only."""
    artifact, _ = generate_angular_component(
        design_system=design_system,
        prompt=prompt,
        screenshot_path=screenshot_path,
        design_tokens=design_tokens,
    )
    return artifact


def generate_angular_component(
    design_system: str,
    figma_json: Optional[Dict] = None,
    screenshot_path: Optional[str] = None,
    prompt: Optional[str] = None,
    design_tokens: Optional[Dict] = None,
    fast_mode: bool = False,
) -> "tuple[GeneratedAngularArtifact, dict]":
    """Unified entry point for all generate paths.

    Accepts any combination of figma_json, screenshot_path, and prompt.
    Returns (artifact, pipeline_metadata) where pipeline_metadata contains
    phase1_research_context, ds_coverage, and other state for session persistence.
    """
    if not figma_json and not screenshot_path and not prompt:
        raise ValueError("At least one of figma_json, screenshot_path, or prompt is required")

    if figma_json is None:
        print("Generating HTML from input...")
        html, css = generate_html_from_input(prompt=prompt, screenshot_path=screenshot_path)
        print("Converting HTML to synthetic Figma tree...")
        figma_json = html_to_figma_tree(html, css, name=prompt or "Generated Design")

    figma_screenshots = {"main": screenshot_path} if screenshot_path else None

    artifact, final_state = run_figma_to_angular(
        figma_json=figma_json,
        design_tokens=design_tokens,
        figma_screenshots=figma_screenshots,
        design_system=design_system,
        fast_mode=fast_mode,
    )

    catalog = load_ds_catalog(design_system)
    pipeline_metadata = {
        "phase1_research_context": final_state.get("phase1_research_context") or "",
        "ds_coverage": compute_ds_coverage(artifact, design_system, catalog).as_dict() if catalog else {},
    }
    return artifact, pipeline_metadata


# ============================================================================
# DS COVERAGE SCORING (Enhancement E)
# ============================================================================

@dataclass
class DSCoverageScore:
    total_mappable_elements: int
    ds_mapped_elements: int
    coverage_pct: float
    uncovered_selectors: List[str]

    def as_dict(self) -> dict:
        return {
            "total_mappable_elements": self.total_mappable_elements,
            "ds_mapped_elements": self.ds_mapped_elements,
            "coverage_pct": round(self.coverage_pct, 1),
            "uncovered_selectors": self.uncovered_selectors,
        }


def compute_ds_coverage(
    artifact: "GeneratedAngularArtifact",
    design_system: str,
    catalog: Optional[Dict] = None,
) -> DSCoverageScore:
    """Compute DS coverage: ratio of mappable elements using DS components."""
    import html.parser as _html_parser

    if catalog is None:
        catalog = load_ds_catalog(design_system)
    if not catalog:
        return DSCoverageScore(0, 0, 0.0, [])

    ds_selectors = {c["selector"].lower() for c in catalog.get("components", [])}
    mappable_native = {"button", "select", "input", "textarea", "table", "a"}

    html_files = [
        f for f in artifact.files
        if f.file_type in ("html", "template") or f.path.endswith(".html")
    ]

    class _TagCounter(_html_parser.HTMLParser):
        def __init__(self):
            super().__init__()
            self.total = 0
            self.ds_count = 0
            self.uncovered: List[str] = []

        def handle_starttag(self, tag, attrs):
            tag_lower = tag.lower()
            if tag_lower in ds_selectors:
                self.total += 1
                self.ds_count += 1
            elif tag_lower in mappable_native:
                self.total += 1
                self.uncovered.append(tag_lower)

    total = 0
    ds_count = 0
    uncovered: List[str] = []
    for html_file in html_files:
        counter = _TagCounter()
        try:
            counter.feed(html_file.content)
        except Exception:
            pass
        total += counter.total
        ds_count += counter.ds_count
        uncovered.extend(counter.uncovered)

    pct = (ds_count / total * 100) if total > 0 else 0.0
    return DSCoverageScore(
        total_mappable_elements=total,
        ds_mapped_elements=ds_count,
        coverage_pct=pct,
        uncovered_selectors=list(set(uncovered)),
    )


# ============================================================================
# INTENT CLASSIFICATION (Enhancement A)
# ============================================================================

@dataclass
class IntentClassification:
    category: str  # LAYOUT_STRUCTURAL | VISUAL_STYLE | COMPONENT_SWAP | DATA_LOGIC_BEHAVIOR | ACCESSIBILITY_PROPERTY | AMBIGUOUS
    new_components_requested: List[str]
    affected_selectors: List[str]
    requires_catalog_lookup: bool
    requires_doc_research: bool
    confidence: float

    def as_dict(self) -> dict:
        return {
            "category": self.category,
            "new_components_requested": self.new_components_requested,
            "affected_selectors": self.affected_selectors,
            "requires_catalog_lookup": self.requires_catalog_lookup,
            "requires_doc_research": self.requires_doc_research,
            "confidence": self.confidence,
        }


_INTENT_HEURISTICS: List[tuple] = [
    ("DATA_LOGIC_BEHAVIOR", re.compile(
        r"\b(method|function|service|api|logic|state|variable|emit|event|handler|click|subscribe|observable|promise|fetch|http|backend|endpoint)\b",
        re.IGNORECASE,
    )),
    ("ACCESSIBILITY_PROPERTY", re.compile(
        r"\b(aria|a11y|accessible|accessibility|role|alt|tab\s*index|focus|keyboard|screen\s*reader)\b",
        re.IGNORECASE,
    )),
    ("VISUAL_STYLE", re.compile(
        r"\b(color|colour|background|bg|font|text\s*size|bold|italic|shadow|border|radius|opacity|theme|dark|light|palette)\b",
        re.IGNORECASE,
    )),
    ("LAYOUT_STRUCTURAL", re.compile(
        r"\b(layout|align|center|right|left|flex|grid|column|row|wrap|gap|padding|margin|position|stack|inline|side)\b",
        re.IGNORECASE,
    )),
    ("COMPONENT_SWAP", re.compile(
        r"\b(add|replace|swap|change|use|insert|put|remove|delete|convert)\b.{0,30}\b"
        r"(button|input|dropdown|select|table|dialog|modal|card|chip|badge|tab|slider|toggle|checkbox|radio|calendar|date picker|autocomplete|tree|menu|panel|accordion)\b",
        re.IGNORECASE,
    )),
]


def _extract_component_terms(prompt: str, catalog: Dict) -> List[str]:
    """Extract DS component-related terms from a prompt."""
    terms = []
    prompt_lower = prompt.lower()
    for entry in catalog.get("components", []):
        for hint in entry.get("figma_hints", [entry.get("name", "")]):
            if hint and hint.lower() in prompt_lower:
                terms.append(hint)
                break
    return terms


def classify_refine_intent(
    prompt: str,
    current_artifact: "GeneratedAngularArtifact",
    catalog: Dict,
) -> IntentClassification:
    """Classify the intent of a refine prompt. Heuristics first, LLM fallback."""
    affected = [
        m.ds_selector for m in current_artifact.ds_components_used
        if m.ds_component != "native"
    ]

    for category, pattern in _INTENT_HEURISTICS:
        if pattern.search(prompt):
            requires_lookup = category not in ("DATA_LOGIC_BEHAVIOR", "ACCESSIBILITY_PROPERTY")
            requires_doc = category in ("COMPONENT_SWAP", "VISUAL_STYLE")
            component_terms = _extract_component_terms(prompt, catalog)
            return IntentClassification(
                category=category,
                new_components_requested=component_terms,
                affected_selectors=affected,
                requires_catalog_lookup=requires_lookup,
                requires_doc_research=requires_doc,
                confidence=0.8,
            )

    # Fallback: cheap LLM call for ambiguous cases
    try:
        llm = ChatOpenAI(model=Config.LLM_MODEL, temperature=0.0, api_key=Config.OPENAI_API_KEY)
        catalog_names = [c["name"] for c in catalog.get("components", [])]
        system = (
            "Classify this Angular refinement request into exactly one category.\n"
            "Categories: LAYOUT_STRUCTURAL, VISUAL_STYLE, COMPONENT_SWAP, "
            "DATA_LOGIC_BEHAVIOR, ACCESSIBILITY_PROPERTY, AMBIGUOUS\n"
            f"Available DS components: {', '.join(catalog_names[:20])}\n"
            'Return JSON: {"category": "...", "new_components_requested": [...], "confidence": 0.0}'
        )
        resp = llm.invoke([SystemMessage(content=system), HumanMessage(content=prompt)])
        parsed = _parse_llm_json_response(resp.content)
        category = parsed.get("category", "AMBIGUOUS")
        new_comps = parsed.get("new_components_requested", [])
        confidence = float(parsed.get("confidence", 0.5))
        requires_lookup = category not in ("DATA_LOGIC_BEHAVIOR", "ACCESSIBILITY_PROPERTY")
        requires_doc = category in ("COMPONENT_SWAP", "VISUAL_STYLE")
        return IntentClassification(
            category=category,
            new_components_requested=new_comps,
            affected_selectors=affected,
            requires_catalog_lookup=requires_lookup,
            requires_doc_research=requires_doc,
            confidence=confidence,
        )
    except Exception as exc:
        print(f"Intent classification LLM failed: {exc}")
        component_terms = _extract_component_terms(prompt, catalog)
        return IntentClassification(
            category="AMBIGUOUS",
            new_components_requested=component_terms,
            affected_selectors=affected,
            requires_catalog_lookup=True,
            requires_doc_research=True,
            confidence=0.3,
        )


# ============================================================================
# CATALOG QUERY FOR INTENT (Enhancement B)
# ============================================================================

def query_catalog_for_intent(
    prompt: str,
    catalog: Dict,
    new_component_terms: List[str],
    doc_research_cache: Optional[Dict[str, str]] = None,
) -> "tuple[List[Dict], Dict[str, str]]":
    """Score catalog entries against requested terms. Fetch+cache API/usage docs.

    Returns (top_matches, updated_doc_cache).
    Each match dict: {selector, name, description, score, doc_text}.
    """
    if doc_research_cache is None:
        doc_research_cache = {}

    prompt_lower = prompt.lower()
    scored: List[tuple] = []

    for entry in catalog.get("components", []):
        score = 0
        hints = entry.get("figma_hints", [entry.get("name", "")])
        for hint in hints:
            hl = hint.lower()
            if re.search(rf"\b{re.escape(hl)}\b", prompt_lower):
                score += 60
                break
            elif hl in prompt_lower:
                score += 40
                break
        for term in new_component_terms:
            tl = term.lower()
            if tl in entry.get("name", "").lower() or any(tl in h.lower() for h in hints):
                score += 30
                break
        if score >= 30:
            scored.append((score, entry))

    scored.sort(key=lambda x: x[0], reverse=True)

    scraper = DocScraper()
    top_matches: List[Dict] = []
    for score, entry in scored[:3]:
        doc_text = ""
        for doc_type in ("api", "usage"):
            url = entry.get("urls", {}).get(doc_type, "")
            if not url:
                continue
            if url in doc_research_cache:
                doc_text += doc_research_cache[url] + "\n"
            else:
                try:
                    fetched = scraper.fetch(url)
                    if fetched:
                        doc_research_cache[url] = fetched
                        doc_text += fetched + "\n"
                except Exception as exc:
                    print(f"  Doc fetch failed for {url}: {exc}")
        top_matches.append({
            "selector": entry["selector"],
            "name": entry["name"],
            "description": entry.get("description", ""),
            "score": score,
            "doc_text": doc_text[:3000],
        })

    return top_matches, doc_research_cache


# ============================================================================
# PROACTIVE COMPONENT SUGGESTIONS (Enhancement C)
# ============================================================================

def build_component_suggestion_response(
    catalog: Dict,
    top_matches: List[Dict],
) -> "tuple[str, str, bool]":
    """Build a suggestion string for the user from matched catalog components.

    Returns (suggestion_text, chosen_selector, awaiting_confirmation).
    awaiting_confirmation=True when confidence is low (multiple close matches, top score < 70).
    """
    if not top_matches:
        return "", "", False

    ds_name = catalog.get("name", "design system")
    lines = [f"I found these {ds_name} components that match your request:"]
    for i, match in enumerate(top_matches, 1):
        lines.append(f"  {i}. <{match['selector']}> — {match['description'][:80]}")

    best = top_matches[0]
    if len(top_matches) == 1 or best["score"] >= 70:
        lines.append(f"\nProceeding with <{best['selector']}> (best match).")
        return "\n".join(lines), best["selector"], False
    else:
        lines.append(
            f"\nProceeding with <{best['selector']}> (highest match). "
            "Reply to choose a different option."
        )
        return "\n".join(lines), best["selector"], True


# ============================================================================
# REFINE WITH PROMPT (DS-aware, Enhancement A/B/C/D)
# ============================================================================

def refine_with_prompt(
    current_artifact: "GeneratedAngularArtifact",
    prompt: str,
    design_system: str,
    component_mappings: Optional[List] = None,
    screenshot_path: Optional[str] = None,
    intent: Optional[IntentClassification] = None,
    doc_research_cache: Optional[Dict[str, str]] = None,
    phase1_research_context: Optional[str] = None,
) -> "tuple[GeneratedAngularArtifact, dict]":
    """Apply a natural-language change to existing generated Angular files.

    DS-aware: classifies intent, queries catalog, fetches docs, enforces guardrails.
    Returns (artifact, updated_meta) where updated_meta contains doc_research_cache,
    intent dict, suggestion_text, and ds_coverage.
    """
    catalog = load_ds_catalog(design_system)
    if not catalog:
        raise ValueError(f"Design system catalog not found: {design_system}")

    if doc_research_cache is None:
        doc_research_cache = {}

    # Classify intent (Enhancement A)
    if intent is None:
        intent = classify_refine_intent(prompt, current_artifact, catalog)

    # Catalog lookup + doc research (Enhancement B)
    doc_section = ""
    suggestion_text = ""
    if intent.requires_catalog_lookup and intent.new_components_requested:
        top_matches, doc_research_cache = query_catalog_for_intent(
            prompt, catalog, intent.new_components_requested, doc_research_cache
        )
        if top_matches:
            suggestion_text, _, _ = build_component_suggestion_response(catalog, top_matches)
            doc_lines = ["\n## RELEVANT COMPONENT DOCUMENTATION"]
            for match in top_matches:
                if match["doc_text"]:
                    doc_lines.append(f"\n### {match['name']} (<{match['selector']}>)")
                    doc_lines.append(match["doc_text"][:2000])
            doc_section = "\n".join(doc_lines)

    # Build shared enforcement prompt (Enhancement G)
    enforcement = build_ds_enforcement_system_prompt(catalog, design_system, intent.category)

    # Reuse Phase 1 research from generation (Enhancement F)
    phase1_section = ""
    if phase1_research_context:
        phase1_section = (
            "\n\n## GENERATION-TIME RESEARCH CONTEXT (reuse these findings)\n"
            + phase1_research_context[:3000]
        )

    current_files = {f.file_type: f.content for f in current_artifact.files}

    screenshot_section = ""
    if screenshot_path and Config.USE_SCREENSHOT_ANALYSIS:
        styling = analyze_screenshot_for_styling(screenshot_path, prompt)
        if styling:
            screenshot_section = f"\n\nVisual reference:\n{json.dumps(styling, indent=2)}"

    mappings_text = "\n".join(
        f"  - {m.ds_selector} ({m.ds_component})"
        for m in (component_mappings or [])
        if m.ds_component != "native"
    ) or "(none)"

    llm = ChatOpenAI(
        model=Config.LLM_MODEL,
        temperature=Config.LLM_TEMPERATURE,
        api_key=Config.OPENAI_API_KEY,
    ).with_structured_output(GeneratedAngularArtifact)

    system = (
        f"You are an expert Angular developer using the {catalog.get('name', design_system)} design system.\n"
        f"Apply the user's requested change to the Angular component. Preserve all unaffected structure.\n"
        f"{enforcement}"
        f"{phase1_section}"
        f"{doc_section}\n\n"
        "Return COMPLETE updated files — not diffs."
    )

    user = f"""Component: {current_artifact.component_name}

HTML:
```html
{current_files.get('html', '')}
```
TypeScript:
```typescript
{current_files.get('typescript', '')}
```
SCSS:
```scss
{current_files.get('scss', '')}
```
DS components in use:
{mappings_text}
{screenshot_section}

Change requested: {prompt}"""

    METRICS.record_llm_call(len(system) + len(user), 0, "refine_prompt")
    result = llm.invoke([SystemMessage(content=system), HumanMessage(content=user)])
    if METRICS.log_trace:
        METRICS.log_trace[-1]["message"] += " / structured output"

    coverage = compute_ds_coverage(result, design_system, catalog)
    updated_meta = {
        "doc_research_cache": doc_research_cache,
        "intent": intent.as_dict(),
        "suggestion_text": suggestion_text,
        "ds_coverage": coverage.as_dict(),
    }
    return result, updated_meta


# ============================================================================
# MAIN ENTRY POINT
# ============================================================================

def run_figma_to_angular(
    figma_json: Dict,
    ds_json: Optional[Dict] = None,        # deprecated, kept for backward compat
    design_tokens: Optional[Dict] = None,
    figma_screenshots: Optional[Dict[str, str]] = None,
    design_system: str = "",
    fast_mode: bool = False,
) -> "tuple[GeneratedAngularArtifact, Dict]":
    """Run the Figma → Angular pipeline.

    Args:
        figma_json: The Figma design tree JSON.
        ds_json: Ignored (deprecated). Previously held compodoc JSON.
        design_tokens: Optional design tokens dict.
        figma_screenshots: Optional dict of screenshot URLs.
        design_system: Name of the design system catalog
                       (e.g., 'primeng' → loads design_systems/primeng_catalog.json).
        fast_mode: If True, skips Tier 2+3 LLM mapping calls (ambiguous and
                   low-confidence nodes fall back to native HTML instead of being
                   LLM-resolved) and doubles the IR chunk size to 100. The Tier 1
                   threshold remains at 70 so only high-confidence DS mappings are
                   kept. Suitable for large designs where speed matters more than
                   maximising DS component coverage.

    Raises:
        ValueError: If design_system is empty or no catalog file is found.
    """
    METRICS.reset()
    Config.FAST_MODE = fast_mode
    if fast_mode:
        print("Fast mode enabled: Tier 2+3 LLM mapping calls skipped, IR chunk size doubled. "
              "Ambiguous/low-confidence nodes will use native HTML.")

    global DESIGN_TOKENS, DS_CATALOG, DOC_KNOWLEDGE, DS_CATALOG_ENTRY_MAP, DS_DOCS_DIR
    DESIGN_TOKENS = design_tokens or {}

    if not design_system:
        raise ValueError("design_system parameter is required (e.g., 'primeng')")

    catalog = load_ds_catalog(design_system)
    if catalog is None:
        raise ValueError(
            f"No catalog found: design_systems/{design_system}_catalog.json\n"
            f"Copy design_systems/template_catalog.json and fill it in."
        )

    DS_CATALOG = _build_ds_catalog_from_catalog(catalog)
    print(f"Loaded catalog: {catalog['name']} ({len(catalog.get('components', []))} components)")

    # Build fast-lookup map: name (lowercase) + selector → catalog entry
    DS_CATALOG_ENTRY_MAP = {}
    for entry in catalog.get("components", []):
        if entry.get("name"):
            DS_CATALOG_ENTRY_MAP[entry["name"].lower()] = entry
        if entry.get("selector"):
            DS_CATALOG_ENTRY_MAP[entry["selector"].lower()] = entry

    knowledge = load_ds_knowledge(design_system)
    DOC_KNOWLEDGE = knowledge or {}

    # Set DS_DOCS_DIR if local scraped docs exist (produced by scrape_ds_docs.py)
    _docs_candidate = os.path.join(Config.DS_MAPPINGS_DIR, f"{design_system}_docs")
    if os.path.isdir(_docs_candidate):
        DS_DOCS_DIR = _docs_candidate
        print(f"Local docs directory found: {DS_DOCS_DIR}")
    else:
        DS_DOCS_DIR = ""

    # Build a ds_config-like dict from catalog metadata for downstream compatibility
    ds_config = {
        "name": catalog.get("name", design_system),
        "framework": catalog.get("framework", "angular"),
        "prefix": catalog.get("prefix", ""),
        "import_path": catalog.get("import_path", ""),
        "component_example": catalog.get("component_example", {}),
        "_catalog": catalog,  # full catalog reference for code-gen prompts
    }

    thumbnail_url = figma_json.get("thumbnailUrl")

    initial_state: AgentState = {
        "figma_json": figma_json,
        "original_figma_json": figma_json,
        "ds_catalog": DS_CATALOG,
        "ds_config": ds_config,
        "ds_catalog_entries": catalog.get("components", []),
        "design_tokens": design_tokens,
        "figma_screenshots": figma_screenshots or ({"main": thumbnail_url} if thumbnail_url else None),
        "ir_tree": None,
        "component_mappings": None,
        "generated": None,
        "validation_errors": [],
        "repair_attempt": 0,
        "messages": [],
        "ds_knowledge": knowledge,
        "phase1_research_context": None,
    }

    print("Invoking workflow...")
    workflow = create_workflow()
    final_state = workflow.invoke(initial_state, config={"recursion_limit": 50})
    print("Workflow complete.")

    print(METRICS.summary())

    if final_state.get("generated"):
        return final_state["generated"], final_state

    artifact = GeneratedAngularArtifact(
        component_name="failed-generation",
        files=[],
        ds_components_used=[],
        imports=[],
        unresolved_nodes=[
            {"error": err.message}
            for err in final_state.get("validation_errors", [])
        ],
    )
    return artifact, final_state


if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        print("Usage: python figma_to_angular_agent.py <design_system>")
        sys.exit(1)

    with open("figma_tree.json") as f:
        figma_data = json.load(f)

    result, _ = run_figma_to_angular(
        figma_json=figma_data,
        design_system=sys.argv[1],
    )

    for file in result.files:
        os.makedirs(os.path.dirname(file.path) if os.path.dirname(file.path) else ".", exist_ok=True)
        with open(file.path, "w") as f:
            f.write(file.content)

    print(f"Generated {len(result.files)} files")
