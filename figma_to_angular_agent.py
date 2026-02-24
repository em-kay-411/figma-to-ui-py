# figma_to_angular_agent.py — Web-Catalog DS Mapper
#
# Changes from previous version:
#   - Replaced compodoc-based flow with user-curated catalog JSON
#   - TEXT nodes always use native HTML (h1–h6, p) — decided deterministically
#   - Component matching uses scored hints from catalog entries
#   - Ambiguous nodes resolved with a single batch LLM call
#   - Only 1–2 LLM calls per run (IR gen + optional repair)

from typing import TypedDict, List, Dict, Any, Optional
from dataclasses import dataclass, field
import json
import os
import base64
import requests
import time
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
    lines = [f"{name.upper()} COMPONENT MAPPING GUIDE - Use these aggressively:"]
    for c in catalog.get("components", []):
        selector = c["selector"]
        desc = c.get("description", "")[:80]
        hints = ", ".join(c.get("figma_hints", [c["name"]]))
        lines.append(f"- {hints} → <{selector}> ({desc})")
    return "\n".join(lines)


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

def _score_catalog_match(ir_node: Dict, figma_node_name: str, entry: Dict) -> int:
    """Return a 0–100 score for how well a catalog entry matches this IR node.

    Scoring:
      +60  any figma_hint substring found in the Figma layer name (case-insensitive)
      +30  IR semantic type matches the entry name exactly
    """
    score = 0
    ir_type = ir_node.get("type", "")
    name_lower = figma_node_name.lower()

    for hint in entry.get("figma_hints", []):
        if hint.lower() in name_lower:
            score += 60
            break

    # Semantic type alignment
    type_to_entry = {
        "button": "button",
        "card": "card",
        "input": "input",
        "checkbox": "checkbox",
        "radio": "radiobutton",
        "toggle": "toggleswitch",
        "tab": "tabs",
        "table": "table",
        "dialog": "dialog",
        "chip": "chip",
        "badge": "badge",
        "icon": "icon",
        "avatar": "avatar",
        "divider": "divider",
        "select": "select",
        "slider": "slider",
        "progress": "progressbar",
    }
    if type_to_entry.get(ir_type) == entry.get("name"):
        score += 30

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
            "description": c.get("description", "")[:100],
            "hints": c.get("figma_hints", []),
        }
        for c in catalog_entries
    ]

    nodes_payload = [
        {
            "id": item["ir_node"].get("id"),
            "type": item["ir_node"].get("type"),
            "name": item["ir_node"].get("name"),
            "candidates": [
                {"score": s, "name": e["name"], "selector": e["selector"]}
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
[{{"id": "<node_id>", "ds_component": "<component_name_or_native>", "ds_selector": "<selector_or_html_tag>"}}]

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


# ============================================================================
# PIPELINE METRICS & LOGGING
# ============================================================================

class PipelineMetrics:
    """Tracks LLM call counts, character usage, step timings, and log trace."""

    def __init__(self):
        self.reset()

    def reset(self):
        self.llm_calls: int = 0
        self.llm_total_input_chars: int = 0
        self.llm_total_output_chars: int = 0
        self.step_timings: Dict[str, Dict[str, float]] = {}
        self.log_trace: List[Dict[str, Any]] = []

    def record_llm_call(self, input_chars: int = 0, output_chars: int = 0, step: str = ""):
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
                    "clipsContent", "scrollBehavior"]:
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
    if node.get("properties", {}).get("text"):
        compact["text"] = node["properties"]["text"]
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


def build_ir_node(state: AgentState) -> AgentState:
    """Step 2: Convert Figma JSON to IR using chunked processing for large trees."""
    METRICS.start_step("build_ir")
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

        ir_data = _parse_llm_json_response(response.content)
        if isinstance(ir_data, dict):
            ir_data = [ir_data]

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


def _process_chunked_tree(llm, flat_nodes: List[Dict], original_tree: Dict, state: AgentState) -> List[Dict]:
    """Process large Figma tree in chunks and reconstruct the hierarchy."""
    chunks = _chunk_nodes(flat_nodes, Config.MAX_NODES_PER_CHUNK)
    print(f"Split into {len(chunks)} chunks")

    all_ir_nodes = {}

    system_prompt = """You are an expert at analyzing Figma design nodes and converting them to semantic UI components.

Convert these Figma nodes to semantic UI primitives (IR - Intermediate Representation).

For EACH node, identify:
1. Semantic type: button, text, input, card, icon, image, container, list, header, footer, nav, form, divider, avatar, badge, chip, dialog, menu, tab, table, link
2. Layout type: flex-row, flex-column, grid, absolute, stack
3. Key properties based on the node data

Note: These are flattened nodes from a larger tree. Each node has:
- id: unique identifier
- parent_id: ID of parent node (null for root)
- depth: nesting level
- has_children: whether it has child nodes

Output as JSON array with ONE entry per input node.
IMPORTANT: Output ONLY valid JSON, no markdown. Include ALL input nodes in output."""

    for i, chunk in enumerate(chunks):
        print(f"Processing chunk {i+1}/{len(chunks)} ({len(chunk)} nodes)...")
        messages = [
            SystemMessage(content=system_prompt),
            HumanMessage(content=f"Convert these {len(chunk)} Figma nodes to IR:\n\n{json.dumps(chunk, indent=2)}"),
        ]
        try:
            chunk_input_chars = len(json.dumps(chunk))
            response = llm.invoke(messages)
            METRICS.record_llm_call(chunk_input_chars, len(response.content), "build_ir")
            chunk_ir = _parse_llm_json_response(response.content)
            if isinstance(chunk_ir, dict):
                chunk_ir = [chunk_ir]
            for ir_node in chunk_ir:
                if ir_node.get("id"):
                    all_ir_nodes[ir_node["id"]] = ir_node
        except Exception as e:
            print(f"Warning: Chunk {i+1} failed: {str(e)}")
            state["validation_errors"].append(
                ValidationError(file_path="ir_tree", error_type="chunk_error",
                                message=f"Chunk {i+1} processing failed: {str(e)}")
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
    """Step 3: Map IR nodes to DS components using catalog scoring + optional batch LLM.

    TEXT nodes are always classified as native HTML (h1–h6 / p) — no LLM involved.
    Non-text nodes with a high-confidence catalog match (score ≥ 60) are mapped
    deterministically. Ambiguous nodes are resolved in a single batch LLM call.
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

    flat_ir_nodes = _flatten_ir_nodes(state["ir_tree"])
    print(f"Mapping {len(flat_ir_nodes)} IR nodes "
          f"(catalog: {len(catalog_entries)} components)...")

    definite_mappings: List[Dict] = []
    ambiguous_nodes: List[Dict] = []

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

        # ── Score catalog entries ──────────────────────────────────────────
        scored = sorted(
            [
                (s, entry)
                for entry in catalog_entries
                if (s := _score_catalog_match(ir_node, node_name, entry)) > 0
            ],
            key=lambda x: x[0],
            reverse=True,
        )

        if scored and scored[0][0] >= 60:
            # High-confidence deterministic match
            best_entry = scored[0][1]
            # Scrape API docs for context (cached, non-fatal)
            api_url = best_entry.get("urls", {}).get("api", "")
            if api_url:
                scraper.fetch(api_url)  # prime cache; context not used directly here
            definite_mappings.append({
                "figma_node_id": node_id,
                "ds_component": best_entry["name"],
                "ds_selector": best_entry["selector"],
                "inputs": {},
            })
        elif scored:
            # Ambiguous — defer to batch LLM
            ambiguous_nodes.append({"ir_node": ir_node, "top3": scored[:3]})
        else:
            # No match — native HTML
            definite_mappings.append({
                "figma_node_id": node_id,
                "ds_component": "native",
                "ds_selector": _infer_native_html_tag(ir_node),
                "inputs": {},
            })

    # ── Batch resolve ambiguous nodes (1 LLM call) ────────────────────────
    if ambiguous_nodes:
        print(f"Resolving {len(ambiguous_nodes)} ambiguous nodes with LLM batch call...")
        ambiguous_resolved = _resolve_ambiguous_with_llm(ambiguous_nodes, catalog_entries)
        definite_mappings.extend(ambiguous_resolved)

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


def _build_component_hierarchy_context(ir_tree: List[Dict], mappings: List,
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
    component_hierarchy = _build_component_hierarchy_context(ir_tree, mappings)

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

    repair_mapping_hints = ""
    if catalog.get("components"):
        repair_mapping_hints = f"\n\nIMPORTANT - MAXIMIZE {ds_name} component usage:\n"
        for c in catalog["components"][:15]:
            repair_mapping_hints += f"- {', '.join(c.get('figma_hints', [c['name']]))} → <{c['selector']}>\n"
        repair_mapping_hints += (
            f"\n## TEXT NODE RULES (ABSOLUTE):\n"
            f"- type='text' nodes use native HTML tags (h1–h6, p) from ds_selector — NEVER a DS component\n"
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
{repair_mapping_hints}"""

    try:
        repair_input_chars = len(repair_prompt)
        repaired = structured_llm.invoke([HumanMessage(content=repair_prompt)])
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
    workflow.add_node("build_ir", build_ir_node)
    workflow.add_node("map_to_ds", map_to_design_system_node)
    workflow.add_node("generate_code", generate_angular_code_node)
    workflow.add_node("refine_typography", refine_typography_node)
    workflow.add_node("validate", validate_node)
    workflow.add_node("repair", repair_node)

    workflow.set_entry_point("ingest_figma")
    workflow.add_edge("ingest_figma", "build_ir")
    workflow.add_edge("build_ir", "map_to_ds")
    workflow.add_edge("map_to_ds", "generate_code")
    workflow.add_edge("generate_code", "refine_typography")
    workflow.add_edge("refine_typography", "validate")
    workflow.add_conditional_edges(
        "validate",
        should_repair,
        {"repair": "repair", "complete": END},
    )
    workflow.add_edge("repair", "refine_typography")

    print("create_workflow: before compile")
    return workflow.compile()


# ============================================================================
# MAIN ENTRY POINT
# ============================================================================

def run_figma_to_angular(
    figma_json: Dict,
    ds_json: Optional[Dict] = None,        # deprecated, kept for backward compat
    design_tokens: Optional[Dict] = None,
    figma_screenshots: Optional[Dict[str, str]] = None,
    design_system: str = "",
) -> GeneratedAngularArtifact:
    """Run the Figma → Angular pipeline.

    Args:
        figma_json: The Figma design tree JSON.
        ds_json: Ignored (deprecated). Previously held compodoc JSON.
        design_tokens: Optional design tokens dict.
        figma_screenshots: Optional dict of screenshot URLs.
        design_system: Name of the design system catalog
                       (e.g., 'primeng' → loads design_systems/primeng_catalog.json).

    Raises:
        ValueError: If design_system is empty or no catalog file is found.
    """
    METRICS.reset()

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
    }

    print("Invoking workflow...")
    workflow = create_workflow()
    final_state = workflow.invoke(initial_state, config={"recursion_limit": 50})
    print("Workflow complete.")

    print(METRICS.summary())

    if final_state.get("generated"):
        return final_state["generated"]

    return GeneratedAngularArtifact(
        component_name="failed-generation",
        files=[],
        ds_components_used=[],
        imports=[],
        unresolved_nodes=[
            {"error": err.message}
            for err in final_state.get("validation_errors", [])
        ],
    )


if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        print("Usage: python figma_to_angular_agent.py <design_system>")
        sys.exit(1)

    with open("figma_tree.json") as f:
        figma_data = json.load(f)

    result = run_figma_to_angular(
        figma_json=figma_data,
        design_system=sys.argv[1],
    )

    for file in result.files:
        os.makedirs(os.path.dirname(file.path) if os.path.dirname(file.path) else ".", exist_ok=True)
        with open(file.path, "w") as f:
            f.write(file.content)

    print(f"Generated {len(result.files)} files")
