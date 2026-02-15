# figma_to_angular_agent.py (PATCHED VERSION)
# Fixed version with better error handling

from typing import TypedDict, List, Dict, Any, Annotated, Optional
from dataclasses import dataclass, field
import json
import os
import base64
import requests
import time
from enum import Enum

from langchain_core.messages import HumanMessage, AIMessage, SystemMessage
from langchain_openai import ChatOpenAI
from langgraph.graph import StateGraph, END
from langgraph.prebuilt.tool_node import ToolNode
from langchain_core.tools import tool
from pydantic import BaseModel, Field
from dotenv import load_dotenv

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
# DESIGN SYSTEM MAPPING REGISTRY
# ============================================================================

def load_ds_config(design_system: str) -> Optional[Dict[str, Any]]:
    """Load a design system mapping config from the design_systems/ directory.

    Args:
        design_system: Name of the design system (e.g., 'primeng', 'clarity').
                       Maps to design_systems/{design_system}.json

    Returns:
        Parsed JSON config dict, or None if not found.
    """
    config_path = os.path.join(Config.DS_MAPPINGS_DIR, f"{design_system}.json")
    if os.path.exists(config_path):
        with open(config_path, "r") as f:
            return json.load(f)
    return None


def build_common_mappings_prompt(ds_config: Dict[str, Any]) -> str:
    """Build the 'Common mappings' section for the map_to_design_system_node prompt."""
    lines = ["Common mappings:"]
    for m in ds_config["common_mappings"]:
        lines.append(f"- {m['semantic']} \u2192 {m['selectors']}")
    return "\n".join(lines)


def build_code_gen_mappings_prompt(ds_config: Dict[str, Any]) -> str:
    """Build the component mapping guide section for the generate_angular_code_node prompt."""
    name = ds_config["name"]
    lines = [f"{name.upper()} COMPONENT MAPPING GUIDE - Use these aggressively:"]
    for m in ds_config["code_gen_mappings"]:
        lines.append(f"- {m['semantic']} \u2192 {m['usage']}")
    return "\n".join(lines)


def build_import_example_prompt(ds_config: Dict[str, Any]) -> str:
    """Build the import example section for the code generation prompt."""
    example = ds_config.get("component_example", {})
    imports_example = example.get("imports_example", "// Import design system modules as needed")
    decorator_imports = example.get("decorator_imports", "CommonModule, ...")
    return f"""```typescript
import {{ Component, ChangeDetectionStrategy }} from '@angular/core';
import {{ CommonModule }} from '@angular/common';
// Import ALL {ds_config['name']} modules that you use in the template
{imports_example}
// ... etc for every {ds_config.get('prefix', 'ds')}-* element used

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


def bootstrap_ds_mappings(
    ds_json: Dict[str, Any],
    ds_name: str,
    save: bool = True
) -> Dict[str, Any]:
    """Auto-generate a design system mapping JSON from its documentation using LLM.

    Use this when you don't have a pre-built mapping file for a design system.
    It reads the component documentation and generates the mapping config.

    Args:
        ds_json: The Compodoc / documentation JSON for the design system.
        ds_name: Human-readable name (e.g., 'PrimeNG', 'Clarity').
        save: If True, saves the generated config to design_systems/ directory.

    Returns:
        The generated design system config dict.
    """
    # Extract component summaries from the documentation
    catalog = _parse_compodoc_json(ds_json)
    components_summary = []
    for selector, info in list(catalog.get("components", {}).items())[:80]:
        components_summary.append({
            "selector": selector,
            "name": info.get("name", ""),
            "description": info.get("description", "")[:150],
            "inputs": list(info.get("inputs", {}).keys())[:10]
        })
    for selector, info in list(catalog.get("directives", {}).items())[:40]:
        components_summary.append({
            "selector": selector,
            "name": info.get("name", ""),
            "description": info.get("description", "")[:150],
            "inputs": list(info.get("inputs", {}).keys())[:10],
            "type": "directive"
        })

    prompt = f"""You are given the component catalog for the "{ds_name}" UI library.
Here are the available components and directives:

{json.dumps(components_summary, indent=2)}

Generate a JSON design system mapping config with EXACTLY this structure:
{{
  "name": "{ds_name}",
  "framework": "angular",
  "prefix": "<the component selector prefix used by this library, inferred from the selectors above>",
  "common_mappings": [
    {{ "semantic": "<semantic UI concept like button, card, input, etc.>", "selectors": "<actual selectors/usage from this library>" }}
  ],
  "code_gen_mappings": [
    {{ "semantic": "<UI concept description like 'Buttons', 'Data tables'>", "usage": "<recommended usage pattern with proper elements and attributes>" }}
  ],
  "import_pattern": "<import statement pattern for this library>",
  "component_example": {{
    "imports_example": "<2-3 example import lines for this library>",
    "decorator_imports": "<example imports array content for @Component decorator>"
  }}
}}

RULES:
- Only include mappings for components that ACTUALLY EXIST in the provided catalog
- Cover all semantic UI concepts: text, button, input, card, container, list, icon, image, toolbar,
  divider, chip, tab, menu, form-field, select, checkbox, radio, toggle, slider, progress,
  stepper, table, expansion, dialog, tooltip, paginator, sidenav, datepicker, autocomplete, nav
- Skip any semantic concept that has no matching component in this library
- Use the EXACT selectors from the catalog
- Output ONLY valid JSON, no markdown fences or explanation"""

    llm = ChatOpenAI(
        model=Config.LLM_MODEL,
        temperature=0.1,
        api_key=Config.OPENAI_API_KEY
    )

    response = llm.invoke([
        SystemMessage(content="You generate structured JSON configs. Output ONLY valid JSON."),
        HumanMessage(content=prompt)
    ])

    # Parse the response
    content = response.content.strip()
    # Strip markdown fences if present
    if content.startswith("```"):
        content = content.split("\n", 1)[1]
        content = content.rsplit("```", 1)[0]

    ds_config = json.loads(content)

    # Save to file if requested
    if save:
        os.makedirs(Config.DS_MAPPINGS_DIR, exist_ok=True)
        filename = ds_name.lower().replace(" ", "_").replace("-", "_")
        config_path = os.path.join(Config.DS_MAPPINGS_DIR, f"{filename}.json")
        with open(config_path, "w") as f:
            json.dump(ds_config, f, indent=2)
        print(f"Design system mapping saved to: {config_path}")

    return ds_config


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
        self._log(step_name, f"Step started")

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

        total_time = 0.0
        for t in self.step_timings.values():
            if t["end"]:
                total_time += t["end"] - t["start"]
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
        # Check if it's a local file path
        if os.path.isfile(source):
            print(f"Loading image from local file: {source}")
            with open(source, 'rb') as f:
                image_data = base64.b64encode(f.read()).decode('utf-8')

            # Determine content type from file extension
            ext = os.path.splitext(source)[1].lower()
            if ext in ['.jpg', '.jpeg']:
                return f"data:image/jpeg;base64,{image_data}"
            elif ext == '.gif':
                return f"data:image/gif;base64,{image_data}"
            elif ext == '.webp':
                return f"data:image/webp;base64,{image_data}"
            else:  # Default to PNG
                return f"data:image/png;base64,{image_data}"

        # Otherwise treat as URL
        elif source.startswith('http://') or source.startswith('https://'):
            print(f"Fetching image from URL: {source[:50]}...")
            response = requests.get(source, timeout=30)
            response.raise_for_status()
            image_data = base64.b64encode(response.content).decode('utf-8')

            # Determine content type from response
            content_type = response.headers.get('content-type', 'image/png')
            if 'jpeg' in content_type or 'jpg' in content_type:
                return f"data:image/jpeg;base64,{image_data}"
            elif 'gif' in content_type:
                return f"data:image/gif;base64,{image_data}"
            elif 'webp' in content_type:
                return f"data:image/webp;base64,{image_data}"
            else:
                return f"data:image/png;base64,{image_data}"
        else:
            print(f"Warning: Invalid image source (not a file or URL): {source}")
            return None

    except Exception as e:
        print(f"Warning: Failed to load image from {source}: {e}")
        return None


def analyze_screenshot_for_styling(image_url: str, design_context: str) -> Optional[Dict]:
    """Use GPT-4 Vision to analyze a screenshot and extract styling details."""
    if not Config.USE_SCREENSHOT_ANALYSIS:
        return None

    # Fetch and encode the image
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
                    {"type": "image_url", "image_url": {"url": image_data, "detail": "high"}}
                ]
            }
        ]

        response = llm.invoke(messages)
        METRICS.record_llm_call(len(design_context[:2000]), len(response.content), "screenshot_analysis")
        content = response.content.strip()

        # Parse JSON response
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
    children: List['IRNode']
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
    original_figma_json: Optional[Dict[str, Any]]  # Original with thumbnailUrl
    ds_catalog: Dict[str, Any]
    ds_config: Optional[Dict[str, Any]]  # Design system mapping config (from design_systems/*.json)
    design_tokens: Optional[Dict[str, Any]]
    figma_screenshots: Optional[Dict[str, str]]
    ir_tree: Optional[List[IRNode]]
    component_mappings: Optional[List[DSComponentMapping]]
    generated: Optional[GeneratedAngularArtifact]
    validation_errors: List[ValidationError]
    repair_attempt: int
    messages: List[Any]

# ============================================================================
# DESIGN SYSTEM CATALOG TOOLS
# ============================================================================

DS_CATALOG = {}
DESIGN_TOKENS = {}

def initialize_catalog(ds_json: Dict, tokens_json: Optional[Dict] = None):
    global DS_CATALOG, DESIGN_TOKENS
    DS_CATALOG = _parse_compodoc_json(ds_json)
    DESIGN_TOKENS = tokens_json or {}

def _parse_compodoc_json(compodoc_data: Dict) -> Dict:
    print('parsing compodoc data...')
    catalog = {
        "components": {},
        "directives": {},
        "modules": {}
    }

    for component in compodoc_data.get("components", []):
        selector = component.get("selector", "")
        catalog["components"][selector] = {
            "name": component.get("name"),
            "selector": selector,
            "inputs": {inp["name"]: inp for inp in component.get("inputsClass", [])},
            "outputs": {out["name"]: out for out in component.get("outputsClass", [])},
            "description": component.get("description", ""),
            "template_url": component.get("templateUrl"),
            "style_urls": component.get("styleUrls", []),
            "encapsulation": component.get("encapsulation"),
            "module": component.get("module", "")
        }

    for module in compodoc_data.get("modules", []):
        catalog["modules"][module.get("name")] = module
    
    print('done parsing compodoc data.')

    return catalog

@tool
def get_component_by_intent(intent: str) -> str:
    """Find design system component by UI intent (button, text, input, etc.)"""
    intent_mapping = {
        "button": ["button", "btn"],
        "text": ["text", "typography", "paragraph", "heading"],
        "input": ["input", "text-field", "form-control"],
        "card": ["card"],
        "icon": ["icon"],
        "image": ["image", "img"],
        "container": ["container", "box", "layout"],
        "list": ["list", "item"],
        "toolbar": ["toolbar"],
        "divider": ["divider"],
        "chip": ["chip"],
        "tab": ["tab"],
        "menu": ["menu"],
        "form-field": ["form-field", "form"],
        "select": ["select"],
        "checkbox": ["checkbox"],
        "radio": ["radio"],
        "toggle": ["toggle", "slide-toggle"],
        "slider": ["slider"],
        "progress": ["progress"],
        "stepper": ["stepper", "step"],
        "table": ["table"],
        "expansion": ["expansion", "accordion"],
        "dialog": ["dialog"],
        "badge": ["badge"],
        "snackbar": ["snack-bar"],
        "tooltip": ["tooltip"],
        "paginator": ["paginator"],
        "sidenav": ["sidenav", "drawer"],
        "datepicker": ["datepicker"],
        "autocomplete": ["autocomplete"],
        "grid": ["grid"],
        "nav": ["nav"],
        "avatar": ["avatar"],
    }

    keywords = intent_mapping.get(intent.lower(), [intent.lower()])
    matches = []

    for selector, component_info in DS_CATALOG.get("components", {}).items():
        if any(kw in selector.lower() or kw in component_info["name"].lower() 
               for kw in keywords):
            matches.append({
                "selector": selector,
                "name": component_info["name"],
                "inputs": list(component_info["inputs"].keys()),
                "outputs": list(component_info["outputs"].keys()),
                "description": component_info["description"]
            })

    return json.dumps(matches, indent=2)

@tool
def get_component_api(selector: str) -> str:
    """Get detailed API (inputs/outputs) for a specific component"""
    component = DS_CATALOG.get("components", {}).get(selector)
    if not component:
        return json.dumps({"error": f"Component {selector} not found"})

    return json.dumps({
        "selector": selector,
        "name": component["name"],
        "inputs": component["inputs"],
        "outputs": component["outputs"],
        "description": component["description"],
        "module": component["module"]
    }, indent=2)

@tool
def get_design_token(token_path: str) -> str:
    """Retrieve design token value (color.primary.500, spacing.md, etc.)"""
    parts = token_path.split('.')
    value = DESIGN_TOKENS

    for part in parts:
        if isinstance(value, dict):
            value = value.get(part)
        else:
            return json.dumps({"error": f"Invalid token path: {token_path}"})

    return json.dumps({"token": token_path, "value": value})

@tool
def search_components(keyword: str) -> str:
    """Search for components by keyword in name or description"""
    matches = []
    keyword_lower = keyword.lower()

    for selector, comp in DS_CATALOG.get("components", {}).items():
        if (keyword_lower in selector.lower() or 
            keyword_lower in comp["name"].lower() or 
            keyword_lower in comp["description"].lower()):
            matches.append({
                "selector": selector,
                "name": comp["name"],
                "description": comp["description"][:100]
            })

    return json.dumps(matches, indent=2)

# ============================================================================
# WORKFLOW NODES (PATCHED)
# ============================================================================

def ingest_figma_node(state: AgentState) -> AgentState:
    """Step 1: Clean and normalize Figma JSON"""
    METRICS.start_step("ingest_figma")
    figma = state["figma_json"]
    
    def clean_node(node: Dict) -> Optional[Dict]:
        # Skip invisible nodes
        if node.get("visible") == False:
            return None
        
        cleaned = {
            "id": node.get("id"),
            "name": node.get("name"),
            "type": node.get("type"),
            "children": [],
            "properties": {},
            "constraints": {},
            "styling": {},
            "layout": {}
        }
        
        # Layout properties
        if "layoutMode" in node:
            cleaned["layout"]["layoutMode"] = node["layoutMode"]
        if "primaryAxisSizingMode" in node:
            cleaned["layout"]["primaryAxisSizingMode"] = node["primaryAxisSizingMode"]
        if "primaryAxisAlignItems" in node:
            cleaned["layout"]["primaryAxisAlignItems"] = node["primaryAxisAlignItems"]
        if "counterAxisAlignItems" in node:
            cleaned["layout"]["counterAxisAlignItems"] = node["counterAxisAlignItems"]
        if "layoutWrap" in node:
            cleaned["layout"]["layoutWrap"] = node["layoutWrap"]
        if "layoutAlign" in node:
            cleaned["layout"]["layoutAlign"] = node["layoutAlign"]
        if "layoutGrow" in node:
            cleaned["layout"]["layoutGrow"] = node["layoutGrow"]
        if "layoutSizingHorizontal" in node:
            cleaned["layout"]["layoutSizingHorizontal"] = node["layoutSizingHorizontal"]
        if "layoutSizingVertical" in node:
            cleaned["layout"]["layoutSizingVertical"] = node["layoutSizingVertical"]

        # Padding properties
        if "paddingTop" in node:
            cleaned["layout"]["paddingTop"] = node["paddingTop"]
        if "paddingBottom" in node:
            cleaned["layout"]["paddingBottom"] = node["paddingBottom"]
        if "paddingLeft" in node:
            cleaned["layout"]["paddingLeft"] = node["paddingLeft"]
        if "paddingRight" in node:
            cleaned["layout"]["paddingRight"] = node["paddingRight"]

        # Item spacing (gap)
        if "itemSpacing" in node:
            cleaned["layout"]["itemSpacing"] = node["itemSpacing"]

        # Counter axis spacing
        if "counterAxisSpacing" in node:
            cleaned["layout"]["counterAxisSpacing"] = node["counterAxisSpacing"]
        
        # Positioning and sizing
        if "absoluteBoundingBox" in node:
            cleaned["properties"]["absoluteBoundingBox"] = node["absoluteBoundingBox"]
        if "absoluteRenderBounds" in node:
            cleaned["properties"]["absoluteRenderBounds"] = node["absoluteRenderBounds"]
        
        # Constraints
        if "constraints" in node:
            cleaned["constraints"] = node["constraints"]
        
        # Styling - fills
        if "fills" in node:
            cleaned["styling"]["fills"] = node["fills"]
        if "background" in node:
            cleaned["styling"]["background"] = node["background"]
        if "backgroundColor" in node:
            cleaned["styling"]["backgroundColor"] = node["backgroundColor"]
        
        # Styling - strokes
        if "strokes" in node:
            cleaned["styling"]["strokes"] = node["strokes"]
        if "strokeWeight" in node:
            cleaned["styling"]["strokeWeight"] = node["strokeWeight"]
        if "strokeAlign" in node:
            cleaned["styling"]["strokeAlign"] = node["strokeAlign"]
        
        # Styling - effects
        if "effects" in node:
            cleaned["styling"]["effects"] = node["effects"]
        if "blendMode" in node:
            cleaned["styling"]["blendMode"] = node["blendMode"]

        # Corner radius
        if "cornerRadius" in node:
            cleaned["styling"]["cornerRadius"] = node["cornerRadius"]
        if "rectangleCornerRadii" in node:
            cleaned["styling"]["rectangleCornerRadii"] = node["rectangleCornerRadii"]

        # Opacity
        if "opacity" in node:
            cleaned["styling"]["opacity"] = node["opacity"]

        # Text properties
        if "characters" in node:
            cleaned["properties"]["text"] = node["characters"]
        if "style" in node:
            cleaned["styling"]["textStyle"] = node["style"]
        if "characterStyleOverrides" in node:
            cleaned["properties"]["characterStyleOverrides"] = node["characterStyleOverrides"]
        if "lineTypes" in node:
            cleaned["properties"]["lineTypes"] = node["lineTypes"]
        
        # Interactions
        if "interactions" in node:
            cleaned["properties"]["interactions"] = node["interactions"]
        
        # Bound variables (design tokens)
        if "boundVariables" in node:
            cleaned["properties"]["boundVariables"] = node["boundVariables"]
        
        # Component properties
        if "componentPropertyReferences" in node:
            cleaned["properties"]["componentPropertyReferences"] = node["componentPropertyReferences"]
        
        # Clipping
        if "clipsContent" in node:
            cleaned["properties"]["clipsContent"] = node["clipsContent"]
        
        # Scroll behavior
        if "scrollBehavior" in node:
            cleaned["properties"]["scrollBehavior"] = node["scrollBehavior"]
        
        # Recursively clean children
        for child in node.get("children", []):
            cleaned_child = clean_node(child)
            if cleaned_child:
                cleaned["children"].append(cleaned_child)
        
        # Remove empty dictionaries to keep the output clean
        cleaned = {k: v for k, v in cleaned.items() if v or k in ["id", "name", "type"]}
        
        return cleaned
    
    # Handle the full Figma file structure
    if "document" in figma:
        # Extract the document tree
        cleaned_figma = clean_node(figma["document"])
        
        # Store metadata separately if needed
        state["figma_metadata"] = {
            "components": figma.get("components", {}),
            "componentSets": figma.get("componentSets", {}),
            "styles": figma.get("styles", {}),
            "name": figma.get("name"),
            "version": figma.get("version"),
            "lastModified": figma.get("lastModified")
        }
    else:
        # If the JSON is just a node without the wrapper
        cleaned_figma = clean_node(figma)
        state["figma_metadata"] = {}
    
    # Count nodes in cleaned tree
    def count_nodes(n):
        count = 1
        for child in n.get("children", []):
            count += count_nodes(child)
        return count

    node_count = count_nodes(cleaned_figma) if cleaned_figma else 0
    original_size = len(json.dumps(figma))
    cleaned_size = len(json.dumps(cleaned_figma))

    print(f"Figma tree cleaned:")
    print(f"  - Root: {cleaned_figma.get('name', 'Unknown')} (Type: {cleaned_figma.get('type', 'Unknown')})")
    print(f"  - Total nodes: {node_count}")
    print(f"  - Original size: {original_size:,} chars")
    print(f"  - Cleaned size: {cleaned_size:,} chars ({100*cleaned_size//original_size}%)")

    state["figma_json"] = cleaned_figma
    state["messages"].append(
        SystemMessage(content=f"Figma tree cleaned. Root: {cleaned_figma.get('name', 'Unknown')} (Type: {cleaned_figma.get('type', 'Unknown')}), {node_count} nodes")
    )

    METRICS.end_step("ingest_figma")
    return state

def _flatten_figma_tree(node: Dict, parent_id: Optional[str] = None, depth: int = 0) -> List[Dict]:
    """Flatten Figma tree into a list of nodes with parent references."""
    nodes = []

    # Create a flattened node representation
    flat_node = {
        "id": node.get("id"),
        "name": node.get("name"),
        "type": node.get("type"),
        "parent_id": parent_id,
        "depth": depth,
        "has_children": bool(node.get("children")),
        "child_count": len(node.get("children", [])),
    }

    # Copy relevant properties
    for key in ["properties", "constraints", "styling", "layout"]:
        if key in node and node[key]:
            flat_node[key] = node[key]

    nodes.append(flat_node)

    # Recursively flatten children
    for child in node.get("children", []):
        nodes.extend(_flatten_figma_tree(child, node.get("id"), depth + 1))

    return nodes


def _create_compact_tree_representation(node: Dict, max_depth: int = 10) -> Dict:
    """Create a more compact tree representation preserving structure."""
    if max_depth <= 0:
        return {"id": node.get("id"), "name": node.get("name"), "type": node.get("type"), "truncated": True}

    compact = {
        "id": node.get("id"),
        "name": node.get("name"),
        "type": node.get("type"),
    }

    # Include layout info if present
    if node.get("layout"):
        compact["layout"] = node["layout"]

    # Include text content if present
    if node.get("properties", {}).get("text"):
        compact["text"] = node["properties"]["text"]

    # Include key styling info (simplified)
    if node.get("styling"):
        styling = node["styling"]
        compact_styling = {}
        if styling.get("fills"):
            compact_styling["fills"] = styling["fills"][:1]  # First fill only
        if styling.get("textStyle"):
            text_style = styling["textStyle"]
            compact_styling["textStyle"] = {
                k: text_style[k] for k in ["fontSize", "fontWeight", "fontFamily", "textAlignHorizontal"]
                if k in text_style
            }
        if compact_styling:
            compact["styling"] = compact_styling

    # Recursively process children
    if node.get("children"):
        compact["children"] = [
            _create_compact_tree_representation(child, max_depth - 1)
            for child in node["children"]
        ]

    return compact


def _chunk_nodes(nodes: List[Dict], chunk_size: int) -> List[List[Dict]]:
    """Split nodes into chunks for batch processing."""
    return [nodes[i:i + chunk_size] for i in range(0, len(nodes), chunk_size)]


def _parse_llm_json_response(content: str) -> Any:
    """Parse JSON from LLM response, handling markdown formatting."""
    content = content.strip()

    # Remove markdown code blocks if present
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

    # Create compact representation of the tree
    compact_tree = _create_compact_tree_representation(figma_tree)
    compact_json = json.dumps(compact_tree, indent=2)

    print(f"Original Figma tree size: {len(json.dumps(figma_tree))} chars")
    print(f"Compact tree size: {len(compact_json)} chars")

    # Check if tree fits in single request
    if len(compact_json) <= Config.MAX_JSON_SIZE:
        # Process entire tree at once
        ir_data = _process_single_tree(llm, compact_tree, state)
    else:
        # Flatten and chunk for batch processing
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
        HumanMessage(content=f"Convert this Figma tree to IR. Process ALL nodes:\n\n{json.dumps(compact_tree, indent=2)}")
    ]

    try:
        print('Invoking LLM for full tree IR generation...')
        input_chars = len(json.dumps(compact_tree))
        print(f'  Sending {input_chars} chars to LLM')
        response = llm.invoke(messages)
        METRICS.record_llm_call(input_chars, len(response.content), "build_ir")
        print('LLM response received.')
        print(f'  Response length: {len(response.content)} chars')
        print(f'  Response preview: {response.content[:500]}...')

        ir_data = _parse_llm_json_response(response.content)

        # Ensure it's a list
        if isinstance(ir_data, dict):
            ir_data = [ir_data]

        # Count total nodes including nested
        total_nodes = _count_ir_nodes(ir_data)
        print(f"Generated IR with {total_nodes} total nodes")

        # Debug: print first few IR nodes
        if ir_data:
            print(f"  First IR node: {json.dumps(ir_data[0], indent=2)[:500]}")

        return ir_data

    except json.JSONDecodeError as e:
        state["validation_errors"].append(
            ValidationError(
                file_path="ir_tree",
                error_type="parse_error",
                message=f"Failed to parse IR JSON: {str(e)}"
            )
        )
        return []
    except Exception as e:
        state["validation_errors"].append(
            ValidationError(
                file_path="ir_tree",
                error_type="generation_error",
                message=f"IR generation failed: {str(e)}"
            )
        )
        return []


def _count_ir_nodes(ir_nodes: List[Dict]) -> int:
    """Count total nodes in IR tree including nested children."""
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

    all_ir_nodes = {}  # id -> ir_node

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

Output as JSON array with ONE entry per input node. Example:
[
  {
    "id": "1:2",
    "type": "button",
    "name": "SubmitButton",
    "layout": "flex-row",
    "properties": {"text": "Submit"},
    "parent_id": "1:1",
    "constraints": {},
    "styling": {}
  }
]

IMPORTANT: Output ONLY valid JSON, no markdown. Include ALL input nodes in output."""

    for i, chunk in enumerate(chunks):
        print(f"Processing chunk {i+1}/{len(chunks)} ({len(chunk)} nodes)...")

        messages = [
            SystemMessage(content=system_prompt),
            HumanMessage(content=f"Convert these {len(chunk)} Figma nodes to IR:\n\n{json.dumps(chunk, indent=2)}")
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
                ValidationError(
                    file_path="ir_tree",
                    error_type="chunk_error",
                    message=f"Chunk {i+1} processing failed: {str(e)}"
                )
            )

    # Reconstruct tree hierarchy
    print(f"Reconstructing hierarchy from {len(all_ir_nodes)} IR nodes...")
    reconstructed = _reconstruct_ir_hierarchy(all_ir_nodes, original_tree)

    return reconstructed


def _reconstruct_ir_hierarchy(ir_nodes: Dict[str, Dict], original_tree: Dict) -> List[Dict]:
    """Reconstruct IR tree hierarchy based on original Figma tree structure."""

    def build_node(figma_node: Dict) -> Optional[Dict]:
        node_id = figma_node.get("id")
        ir_node = ir_nodes.get(node_id)

        if not ir_node:
            # Create a basic IR node if LLM didn't generate one
            ir_node = {
                "id": node_id,
                "type": "container",
                "name": figma_node.get("name", "Unknown"),
                "layout": "flex-column",
                "properties": {},
                "constraints": {},
                "styling": {}
            }

        # Remove parent_id as we're building proper hierarchy
        ir_node.pop("parent_id", None)

        # Build children from original tree structure
        children = []
        for child in figma_node.get("children", []):
            child_ir = build_node(child)
            if child_ir:
                children.append(child_ir)

        ir_node["children"] = children
        return ir_node

    root_ir = build_node(original_tree)
    return [root_ir] if root_ir else []

def _flatten_ir_nodes(ir_nodes: List[Dict], parent_id: Optional[str] = None) -> List[Dict]:
    """Flatten IR tree to list of nodes for batch processing."""
    flat = []
    for node in ir_nodes:
        flat_node = {k: v for k, v in node.items() if k != "children"}
        flat_node["parent_id"] = parent_id
        flat_node["has_children"] = bool(node.get("children"))
        flat.append(flat_node)

        if node.get("children"):
            flat.extend(_flatten_ir_nodes(node["children"], node.get("id")))

    return flat


def map_to_design_system_node(state: AgentState) -> AgentState:
    """Step 3: Map IR to DS components with chunked processing for large trees."""
    METRICS.start_step("map_to_ds")

    # Early exit if no IR tree
    if not state.get("ir_tree"):
        state["validation_errors"].append(
            ValidationError(
                file_path="mappings",
                error_type="no_ir",
                message="No IR tree available for mapping"
            )
        )
        state["component_mappings"] = []
        return state

    llm = ChatOpenAI(model=Config.LLM_MODEL, temperature=Config.LLM_TEMPERATURE)
    tools = [get_component_by_intent, get_component_api, search_components, get_design_token]
    llm_with_tools = llm.bind_tools(tools)

    # Flatten IR tree for processing
    flat_ir_nodes = _flatten_ir_nodes(state["ir_tree"])
    print(f"Mapping {len(flat_ir_nodes)} IR nodes to design system...")

    # Get available DS components summary for context
    ds_components_summary = []
    for selector, comp in DS_CATALOG.get("components", {}).items():
        ds_components_summary.append({
            "selector": selector,
            "name": comp["name"],
            "inputs": list(comp["inputs"].keys())[:5],  # First 5 inputs
            "description": comp["description"][:100] if comp["description"] else ""
        })

    # Chunk nodes if too many
    chunks = _chunk_nodes(flat_ir_nodes, Config.MAX_NODES_PER_CHUNK)
    all_mappings = []

    # Build mapping hints from design system config
    ds_config = state.get("ds_config")
    ds_name = ds_config["name"] if ds_config else "the design system"
    common_mappings_section = build_common_mappings_prompt(ds_config) if ds_config else ""
    ds_summary_str = json.dumps(ds_components_summary[:50], indent=2)

    system_prompt = f"""You are mapping UI elements to {ds_name} design system components.

Available Design System Components (partial list):
{ds_summary_str}

For each IR node:
1. Identify the best matching DS component based on the node's semantic type
2. Use the tools to explore available components and their APIs
3. Map the IR node properties to component inputs

IMPORTANT: Always prefer {ds_name} components over raw HTML elements.
Every UI element should be mapped to a {ds_name} component when possible.

{common_mappings_section}

Output JSON array of mappings. Example:
[
  {{
    "figma_node_id": "1:2",
    "ds_component": "<ComponentName>",
    "ds_selector": "<component-selector>",
    "inputs": {{"variant": "primary"}},
    "outputs": {{}},
    "children_slot": null
  }},
  {{
    "figma_node_id": "1:3",
    "ds_component": "<AnotherComponent>",
    "ds_selector": "<another-selector>",
    "inputs": {{}},
    "outputs": {{}},
    "children_slot": "ng-content"
  }}
]

IMPORTANT:
- Map ALL input nodes
- Output ONLY valid JSON array at the end, no markdown or explanation
- Use correct {ds_name} selectors"""

    for i, chunk in enumerate(chunks):
        print(f"Mapping chunk {i+1}/{len(chunks)} ({len(chunk)} nodes)...")

        messages = [
            SystemMessage(content=system_prompt),
            HumanMessage(content=f"Map these {len(chunk)} IR nodes to DS components:\n\n{json.dumps(chunk, indent=2)}")
        ]

        # Tool-calling loop
        max_iterations = 10
        iteration = 0

        while iteration < max_iterations:
            msg_input_chars = sum(len(str(m.content)) for m in messages)
            response = llm_with_tools.invoke(messages)
            METRICS.record_llm_call(msg_input_chars, len(str(response.content)), "map_to_ds")
            messages.append(response)

            if not response.tool_calls:
                break

            tool_node = ToolNode(tools)
            tool_results = tool_node.invoke({"messages": [response]})
            messages.extend(tool_results["messages"])
            iteration += 1

        # Parse chunk mappings
        if len(messages) >= 2:
            final_response = messages[-1]
            try:
                mappings_data = _parse_llm_json_response(final_response.content)

                if isinstance(mappings_data, dict):
                    mappings_data = [mappings_data]

                all_mappings.extend(mappings_data)
                print(f"  Got {len(mappings_data)} mappings from chunk {i+1}")

            except Exception as e:
                print(f"Warning: Chunk {i+1} mapping failed: {str(e)}")
                state["validation_errors"].append(
                    ValidationError(
                        file_path="mappings",
                        error_type="chunk_parse_error",
                        message=f"Chunk {i+1} mapping parse failed: {str(e)}"
                    )
                )

    # Convert to DSComponentMapping objects
    try:
        state["component_mappings"] = [DSComponentMapping(**m) for m in all_mappings]
        print(f"Total mappings: {len(state['component_mappings'])}")
        state["messages"].append(AIMessage(content=f"Mapped {len(state['component_mappings'])} nodes to DS components"))
    except Exception as e:
        state["validation_errors"].append(
            ValidationError(
                file_path="mappings",
                error_type="mapping_error",
                message=f"Failed to create mappings: {str(e)}"
            )
        )
        state["component_mappings"] = []

    METRICS.end_step("map_to_ds")
    return state

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


def _build_design_structure_for_codegen(figma_json: Dict, ir_tree: List[Dict], mappings: List, max_depth: int = 8) -> Dict:
    """Build a comprehensive design structure for code generation using Figma data."""

    def extract_node_info(node: Dict, depth: int = 0) -> Optional[Dict]:
        if depth > max_depth:
            return {"name": node.get("name"), "truncated": True}

        info = {
            "name": node.get("name"),
            "type": node.get("type"),
        }

        # Extract layout information
        layout = node.get("layout", {})
        if layout:
            if layout.get("layoutMode"):
                info["layoutMode"] = layout["layoutMode"]  # VERTICAL, HORIZONTAL
            if layout.get("primaryAxisAlignItems"):
                info["mainAxisAlign"] = layout["primaryAxisAlignItems"]
            if layout.get("counterAxisAlignItems"):
                info["crossAxisAlign"] = layout["counterAxisAlignItems"]
            if layout.get("layoutWrap"):
                info["wrap"] = layout["layoutWrap"]
            # Sizing modes
            if layout.get("layoutSizingHorizontal"):
                info["sizingH"] = layout["layoutSizingHorizontal"]  # FIXED, HUG, FILL
            if layout.get("layoutSizingVertical"):
                info["sizingV"] = layout["layoutSizingVertical"]

        # Extract text content
        props = node.get("properties", {})
        if props.get("text"):
            info["text"] = props["text"]

        # Extract dimensions from properties
        if props.get("absoluteBoundingBox"):
            box = props["absoluteBoundingBox"]
            info["width"] = box.get("width")
            info["height"] = box.get("height")

        # Extract spacing and padding (these might be at root level or in layout)
        spacing_style = {}

        # Check for padding (might be in layout or root)
        padding_top = layout.get("paddingTop") or node.get("paddingTop")
        padding_bottom = layout.get("paddingBottom") or node.get("paddingBottom")
        padding_left = layout.get("paddingLeft") or node.get("paddingLeft")
        padding_right = layout.get("paddingRight") or node.get("paddingRight")

        if any([padding_top, padding_bottom, padding_left, padding_right]):
            spacing_style["padding"] = f"{int(padding_top or 0)}px {int(padding_right or 0)}px {int(padding_bottom or 0)}px {int(padding_left or 0)}px"

        # Check for item spacing (gap)
        item_spacing = layout.get("itemSpacing") or node.get("itemSpacing")
        if item_spacing:
            spacing_style["gap"] = f"{int(item_spacing)}px"

        # Check for corner radius
        corner_radius = node.get("cornerRadius")
        if corner_radius:
            spacing_style["borderRadius"] = f"{int(corner_radius)}px"

        # Check for individual corner radii
        corner_radii = node.get("rectangleCornerRadii")
        if corner_radii and len(corner_radii) == 4:
            spacing_style["borderRadius"] = f"{int(corner_radii[0])}px {int(corner_radii[1])}px {int(corner_radii[2])}px {int(corner_radii[3])}px"

        # Extract styling
        styling = node.get("styling", {})
        style_info = {}

        if styling:
            # Background color from fills
            if styling.get("fills"):
                fills = styling["fills"]
                if fills and len(fills) > 0:
                    fill = fills[0]
                    if fill.get("type") == "SOLID" and fill.get("color"):
                        color = fill["color"]
                        if isinstance(color, dict):
                            r = int(color.get("r", 0) * 255)
                            g = int(color.get("g", 0) * 255)
                            b = int(color.get("b", 0) * 255)
                            opacity = fill.get("opacity", 1)
                            if opacity < 1:
                                style_info["backgroundColor"] = f"rgba({r},{g},{b},{opacity:.2f})"
                            else:
                                style_info["backgroundColor"] = f"rgb({r},{g},{b})"

            # Text styling
            if styling.get("textStyle"):
                ts = styling["textStyle"]
                if ts.get("fontSize"):
                    style_info["fontSize"] = f"{int(ts['fontSize'])}px"
                if ts.get("fontWeight"):
                    style_info["fontWeight"] = ts["fontWeight"]
                if ts.get("fontFamily"):
                    style_info["fontFamily"] = ts["fontFamily"]
                if ts.get("textAlignHorizontal"):
                    style_info["textAlign"] = ts["textAlignHorizontal"].lower()
                if ts.get("lineHeightPx"):
                    style_info["lineHeight"] = f"{ts['lineHeightPx']}px"
                if ts.get("letterSpacing"):
                    style_info["letterSpacing"] = f"{ts['letterSpacing']}px"

            # Effects (shadows)
            if styling.get("effects"):
                for effect in styling["effects"]:
                    if effect.get("type") == "DROP_SHADOW" and effect.get("visible", True):
                        color = effect.get("color", {})
                        r = int(color.get("r", 0) * 255)
                        g = int(color.get("g", 0) * 255)
                        b = int(color.get("b", 0) * 255)
                        a = color.get("a", 1)
                        offset = effect.get("offset", {})
                        x = offset.get("x", 0)
                        y = offset.get("y", 0)
                        radius = effect.get("radius", 0)
                        style_info["boxShadow"] = f"{int(x)}px {int(y)}px {int(radius)}px rgba({r},{g},{b},{a:.2f})"
                        break

            # Opacity
            if styling.get("opacity") is not None and styling["opacity"] < 1:
                style_info["opacity"] = styling["opacity"]

        # Merge spacing and style info
        style_info.update(spacing_style)

        if style_info:
            info["style"] = style_info

        # Process children
        children = node.get("children", [])
        if children:
            info["children"] = [
                child_info
                for child in children
                if (child_info := extract_node_info(child, depth + 1)) is not None
            ]

        # Collect innerText from descendant TEXT nodes
        # This gives the LLM the actual display text for elements like buttons
        if not info.get("text") and children:
            collected_texts = _collect_descendant_texts(node)
            if collected_texts:
                info["innerText"] = " ".join(collected_texts)

        return info

    # Build from Figma JSON if available and has structure
    if figma_json and figma_json.get("children"):
        return extract_node_info(figma_json)

    # Fall back to IR tree if Figma JSON is empty
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

            styling = ir_node.get("styling", {})
            if styling:
                info["style"] = styling

            children = ir_node.get("children", [])
            if children:
                info["children"] = [ir_to_structure(c, depth + 1) for c in children]

            # Collect innerText from descendant TEXT nodes in IR fallback
            if not info.get("text") and children:
                collected_texts = _collect_descendant_texts(ir_node)
                if collected_texts:
                    info["innerText"] = " ".join(collected_texts)

            return info

        return ir_to_structure(ir_tree[0])

    return {"name": "Unknown", "type": "container", "children": []}


def _build_component_hierarchy_context(ir_tree: List[Dict], mappings: List, max_depth: int = 5) -> Dict:
    """Build a context object representing the component hierarchy for code generation."""

    # Create mapping lookup by figma_node_id
    mapping_by_id = {}
    for m in mappings:
        if hasattr(m, 'dict'):
            m_dict = m.dict()
        elif hasattr(m, 'model_dump'):
            m_dict = m.model_dump()
        else:
            m_dict = m
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


def generate_angular_code_node(state: AgentState) -> AgentState:
    """Step 4: Generate Angular code with full tree context."""
    METRICS.start_step("generate_code")

    # Get IR tree - if empty, use cleaned Figma JSON directly
    ir_tree = state.get("ir_tree", [])
    figma_json = state.get("figma_json", {})
    mappings = state.get("component_mappings") or []

    # If IR tree is empty but we have Figma JSON, use it directly
    if not ir_tree and figma_json:
        print("Warning: IR tree empty, using Figma JSON directly for code generation")
        ir_tree = [figma_json]  # Wrap in list for consistency

    if not ir_tree:
        state["validation_errors"].append(
            ValidationError(
                file_path="generation",
                error_type="no_data",
                message="No IR tree or Figma data available for code generation"
            )
        )
        return state

    llm = ChatOpenAI(model=Config.LLM_MODEL, temperature=Config.LLM_TEMPERATURE)
    structured_llm = llm.with_structured_output(GeneratedAngularArtifact)

    # Get root component name from Figma data
    root_name = "GeneratedComponent"
    figma_root_name = figma_json.get("name") if figma_json else None
    ir_root_name = ir_tree[0].get("name") if ir_tree else None

    raw_name = figma_root_name or ir_root_name
    if raw_name:
        # Convert Figma name to valid Angular component name
        clean_name = "".join(word.capitalize() for word in raw_name.replace("-", " ").replace("_", " ").replace("*", " ").replace("/", " ").split())
        clean_name = "".join(c for c in clean_name if c.isalnum())
        if clean_name and clean_name != "Document":
            root_name = clean_name + "Component"

    # Build the design structure for code generation
    # Use both IR tree and original Figma JSON to ensure we have all the data
    design_structure = _build_design_structure_for_codegen(figma_json, ir_tree, mappings)

    # Screenshot analysis for better styling
    screenshot_styling = None
    screenshot_url = state.get("figma_screenshots", {}).get("main") if state.get("figma_screenshots") else None

    # Also check for thumbnail URL in original Figma data
    if not screenshot_url:
        original_figma = state.get("original_figma_json", {})
        screenshot_url = original_figma.get("thumbnailUrl")

    if screenshot_url and Config.USE_SCREENSHOT_ANALYSIS:
        print(f"Found screenshot URL, analyzing for styling...")
        design_context = json.dumps(design_structure, indent=2)[:3000]
        screenshot_styling = analyze_screenshot_for_styling(screenshot_url, design_context)
        if screenshot_styling:
            print(f"Screenshot styling analysis: {json.dumps(screenshot_styling, indent=2)[:500]}...")

    # Build dynamic prompt sections from design system config
    ds_config = state.get("ds_config")
    ds_name = ds_config["name"] if ds_config else "the design system"
    ds_prefix = ds_config.get("prefix", "ds") if ds_config else "ds"
    code_gen_mappings_section = build_code_gen_mappings_prompt(ds_config) if ds_config else ""
    import_example_section = build_import_example_prompt(ds_config) if ds_config else ""

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
- innerText: Aggregated text from descendant TEXT nodes — use this as the element's display text (e.g., button label, link text)
- styling: Colors, fonts, effects

CRITICAL TEXT RULES:
- When a node has an "innerText" field, use that as the element's text content (e.g., button label)
- When a node has a "text" field, use that as the element's text content
- NEVER use the node "name" as display text — names like "Button", "Base", "Frame 1037" are Figma layer names, NOT user-visible text
- Actual display text comes ONLY from "text" or "innerText" fields

{code_gen_mappings_section}

GENERATE:

1. TypeScript Component (standalone):
{import_example_section}

2. HTML Template - MUST include ALL text and structure from the design:
- Use {ds_name} components for every UI element where a matching component exists
- Include ALL text content from the design
- Use flexbox containers with classes like "flex-row", "flex-column" only when no {ds_name} layout component fits

3. SCSS Styles:
- .flex-row {{ display: flex; flex-direction: row; }}
- .flex-column {{ display: flex; flex-direction: column; }}
- Apply colors, gaps, padding from the design
- Use proper spacing based on layout info

4. ds_components_used: Populate this array with EVERY {ds_name} component used in the template.
   For each component, include: figma_node_id, ds_component name, ds_selector, inputs, outputs.

IMPORTANT:
- DO NOT generate placeholder text like "Sample Component" or "This is a sample"
- USE the ACTUAL text content from the design structure
- PRESERVE the visual hierarchy exactly as shown in the design
- Apply the EXACT styling from the style properties (padding, gap, colors, fonts)
- MAXIMIZE {ds_name} component usage - every UI element should use a {ds_name} component if one exists for that purpose
- The TypeScript file MUST import the module for every {ds_prefix}-* element used in the HTML"""

    # Add screenshot styling instructions if available
    if screenshot_styling:
        system_prompt += """

VISUAL STYLING FROM SCREENSHOT ANALYSIS:
You have been provided with a detailed styling analysis from the actual Figma screenshot.
Use these EXACT values in your SCSS:

Colors: Use the exact hex codes provided
Spacing: Use the exact padding and gap values
Typography: Match the font sizes, weights, and line heights
Effects: Apply shadows and border-radius as specified

This visual analysis takes precedence for styling - use it to make the component look EXACTLY like the design."""

    # Create compact design representation
    design_json = json.dumps(design_structure, indent=2)

    # Truncate if too large
    if len(design_json) > Config.MAX_JSON_SIZE:
        design_json = json.dumps(design_structure)[:Config.MAX_JSON_SIZE]

    # Build dynamic list of available DS components from DS_CATALOG
    available_ds_components = list(DS_CATALOG.get("components", {}).keys())
    if not available_ds_components:
        # Fallback: extract selectors from ds_config code_gen_mappings if available
        if ds_config:
            available_ds_components = [m["usage"].split(">")[0].split("<")[-1].split()[0]
                                       for m in ds_config.get("code_gen_mappings", [])
                                       if "<" in m.get("usage", "")]
        else:
            available_ds_components = []

    # Build component hierarchy context from DS mappings (connects mapping step to generation)
    component_hierarchy = _build_component_hierarchy_context(ir_tree, mappings)

    context = {
        "component_name": root_name,
        "design_structure": design_structure,
        "component_hierarchy_with_ds_mappings": component_hierarchy,
        "mappings_count": len(mappings),
        "available_ds_components": available_ds_components
    }

    # Add screenshot styling to context if available
    if screenshot_styling:
        context["visual_styling_from_screenshot"] = screenshot_styling

    # Debug: print context being sent
    context_str = json.dumps(context, indent=2)
    print(f"\n=== CODE GENERATION CONTEXT ===")
    print(f"Component name: {root_name}")
    print(f"Context size: {len(context_str)} chars")
    print(f"Design structure preview:\n{json.dumps(design_structure, indent=2)[:3000]}...")
    print(f"=== END CONTEXT ===\n")

    messages = [
        SystemMessage(content=system_prompt),
        HumanMessage(content=f"Generate Angular component '{root_name}' from this Figma design:\n\n{context_str}")
    ]

    try:
        print(f"Generating Angular component: {root_name}...")
        input_chars = len(context_str)
        generated = structured_llm.invoke(messages)
        METRICS.record_llm_call(input_chars, sum(len(f.content) for f in generated.files), "generate_code")
        state["generated"] = generated
        state["messages"].append(AIMessage(content=f"Generated {len(generated.files)} files for {root_name}"))
        print(f"Generated {len(generated.files)} files")

        # Debug: print generated file names
        for f in generated.files:
            print(f"  - {f.path} ({len(f.content)} chars)")
    except Exception as e:
        state["validation_errors"].append(
            ValidationError(
                file_path="generation",
                error_type="generation_error",
                message=f"Code generation failed: {str(e)}"
            )
        )

    METRICS.end_step("generate_code")
    return state

def validate_node(state: AgentState) -> AgentState:
    """Step 5: Validate generated code"""
    METRICS.start_step("validate")
    errors = []
    generated = state.get("generated")

    print(f"Validating generated code (repair attempt: {state.get('repair_attempt', 0)})...")

    if not generated:
        errors.append(ValidationError(
            file_path="",
            error_type="missing_generation",
            message="No generated code to validate"
        ))
        state["validation_errors"] = errors
        return state

    # Check if we have files generated
    if not generated.files:
        errors.append(ValidationError(
            file_path="",
            error_type="no_files",
            message="No files were generated"
        ))

    # Determine the DS prefix for validation from ds_config
    ds_config = state.get("ds_config")
    ds_prefix = ds_config.get("prefix", "") if ds_config else ""
    ds_name = ds_config["name"] if ds_config else "the design system"

    # Validate basic file structure (non-blocking warnings only)
    warnings = []
    known_selectors = set(DS_CATALOG.get("components", {}).keys())
    known_selectors.update(DS_CATALOG.get("directives", {}).keys())
    standard_html = {"div", "span", "p", "h1", "h2", "h3", "h4", "h5", "h6",
                     "button", "input", "img", "a", "ul", "li", "section",
                     "header", "footer", "nav", "main", "article"}

    for mapping in generated.ds_components_used:
        selector = mapping.ds_selector
        is_known_ds = selector in known_selectors or (ds_prefix and ds_prefix in selector)
        is_standard_html = selector in standard_html

        if not is_known_ds and not is_standard_html:
            warnings.append(f"Unknown selector: {selector}")

    if warnings:
        print(f"  Validation warnings: {warnings[:3]}")

    # Check design system component usage rate in generated HTML
    html_files = [f for f in generated.files if f.file_type in ("html", "template") or f.path.endswith(".html")]
    for html_file in html_files:
        content = html_file.content
        # Count DS component references using the prefix from config
        ds_count = 0
        if ds_prefix:
            ds_count = content.count(f"{ds_prefix}-") + content.count(f"{ds_prefix}[") + content.count(f"[{ds_prefix}")
        # Also count any known selectors from the catalog
        for sel in list(known_selectors)[:50]:
            if sel in content:
                ds_count += content.count(sel)

        tag_count = content.count("<") - content.count("</") - content.count("<!--")
        tag_count = max(tag_count, 1)
        ds_ratio = ds_count / tag_count

        print(f"  DS usage in {html_file.path}: {ds_count} {ds_name} references out of ~{tag_count} elements ({ds_ratio:.0%})")

        if ds_count < 2 and tag_count > 5:
            errors.append(ValidationError(
                file_path=html_file.path,
                error_type="low_ds_usage",
                message=f"Low {ds_name} usage: only {ds_count} component references found in {tag_count} elements. Maximize {ds_name} component usage for all UI elements."
            ))
            print(f"  WARNING: Low {ds_name} usage detected, will trigger repair")

    # Check that ds_components_used is populated
    if not generated.ds_components_used and html_files:
        for html_file in html_files:
            if ds_prefix and ds_prefix in html_file.content:
                print(f"  WARNING: ds_components_used is empty but HTML contains {ds_name} elements")
                break

    # Only add critical errors that would prevent compilation
    state["validation_errors"] = errors

    if errors:
        print(f"  Validation errors: {[e.message for e in errors]}")
    else:
        print("  Validation passed!")

    METRICS.end_step("validate")
    return state

def repair_node(state: AgentState) -> AgentState:
    """Step 6: Repair code based on errors"""
    METRICS.start_step("repair")
    print(f"Attempting repair #{state.get('repair_attempt', 0) + 1}...")

    # Increment repair attempt FIRST to prevent infinite loops
    state["repair_attempt"] = state.get("repair_attempt", 0) + 1

    # If no mappings or IR, we can't repair - just skip
    if not state.get("component_mappings") and not state.get("ir_tree"):
        print("  No mappings or IR tree available, skipping repair")
        return state

    llm = ChatOpenAI(model=Config.LLM_MODEL, temperature=Config.LLM_TEMPERATURE)
    structured_llm = llm.with_structured_output(GeneratedAngularArtifact)

    errors_summary = "\n".join([
        f"- {err.file_path}: {err.message}"
        for err in state.get("validation_errors", [])
    ])

    # Build a more concise context for repair
    mappings = state.get("component_mappings") or []
    mappings_data = []
    for m in mappings[:20]:  # Limit to first 20 mappings
        if hasattr(m, 'dict'):
            mappings_data.append(m.dict())
        elif hasattr(m, 'model_dump'):
            mappings_data.append(m.model_dump())
        else:
            mappings_data.append(m)

    # Include current generated code for context
    current_files = {}
    if state.get("generated") and state["generated"].files:
        for f in state["generated"].files:
            current_files[f.path] = f.content

    # Build repair hints from ds_config
    ds_config = state.get("ds_config")
    ds_name = ds_config["name"] if ds_config else "the design system"
    repair_mapping_hints = ""
    if ds_config:
        repair_mapping_hints = f"\n\nIMPORTANT - MAXIMIZE {ds_name} component usage:\n"
        for m in ds_config.get("code_gen_mappings", []):
            repair_mapping_hints += f"- {m['semantic']} \u2192 {m['usage']}\n"
        repair_mapping_hints += f"- Every {ds_name} module used in the template MUST be imported in the TypeScript file\n"
        repair_mapping_hints += f"- Populate ds_components_used with ALL {ds_name} components used"

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
        METRICS.record_llm_call(repair_input_chars, sum(len(f.content) for f in repaired.files), "repair")
        state["generated"] = repaired
        state["messages"].append(AIMessage(content=f"Repair attempt {state['repair_attempt']} complete"))
        print(f"  Repair generated {len(repaired.files)} files")
    except Exception as e:
        print(f"  Repair failed: {str(e)}")
        # Don't add more errors - just log and continue
        state["messages"].append(AIMessage(content=f"Repair attempt {state['repair_attempt']} failed: {str(e)}"))

    METRICS.end_step("repair")
    return state

def should_repair(state: AgentState) -> str:
    errors = state.get("validation_errors", [])
    repair_attempt = state.get("repair_attempt", 0)

    print(f"Checking if repair needed: {len(errors)} errors, attempt {repair_attempt}/{Config.MAX_REPAIR_ATTEMPTS}")

    if not errors:
        print("  -> No errors, completing workflow")
        return "complete"

    if repair_attempt >= Config.MAX_REPAIR_ATTEMPTS:
        print(f"  -> Max repair attempts reached, completing with {len(errors)} unresolved errors")
        return "complete"

    print(f"  -> Attempting repair #{repair_attempt + 1}")
    return "repair"

def create_workflow() -> StateGraph:
    workflow = StateGraph(AgentState)

    workflow.add_node("ingest_figma", ingest_figma_node)
    workflow.add_node("build_ir", build_ir_node)
    workflow.add_node("map_to_ds", map_to_design_system_node)
    workflow.add_node("generate_code", generate_angular_code_node)
    workflow.add_node("validate", validate_node)
    workflow.add_node("repair", repair_node)

    workflow.set_entry_point("ingest_figma")
    workflow.add_edge("ingest_figma", "build_ir")
    workflow.add_edge("build_ir", "map_to_ds")
    workflow.add_edge("map_to_ds", "generate_code")
    workflow.add_edge("generate_code", "validate")
    workflow.add_conditional_edges(
        "validate",
        should_repair,
        {"repair": "repair", "complete": END}
    )
    workflow.add_edge("repair", "validate")

    print("create_workflow: before compile")
    return workflow.compile()

def run_figma_to_angular(
    figma_json: Dict,
    ds_json: Dict,
    design_tokens: Optional[Dict] = None,
    figma_screenshots: Optional[Dict[str, str]] = None,
    design_system: str = ""
) -> GeneratedAngularArtifact:
    """Main entry point.

    Args:
        figma_json: The Figma design tree JSON.
        ds_json: The Compodoc / documentation JSON for the design system.
        design_tokens: Optional design tokens dict.
        figma_screenshots: Optional dict of screenshot URLs.
        design_system: Name of the design system mapping config file
                       (without .json extension, e.g., 'primeng', 'clarity').
                       If a matching file exists in design_systems/, it will be loaded.
                       Otherwise, a new config will be auto-generated from ds_json using LLM.
    """
    METRICS.reset()
    initialize_catalog(ds_json, design_tokens)

    if not design_system:
        raise ValueError("design_system parameter is required (e.g., 'primeng', 'clarity', 'my_ds')")

    # Load or bootstrap design system mapping config
    ds_config = load_ds_config(design_system)
    if ds_config is None:
        print(f"No mapping config found for '{design_system}', bootstrapping from documentation...")
        ds_config = bootstrap_ds_mappings(ds_json, design_system, save=True)
        print(f"Bootstrap complete. Config saved to design_systems/{design_system}.json")
    else:
        print(f"Loaded design system config: {ds_config['name']}")

    # Extract thumbnail URL from original Figma JSON before processing
    thumbnail_url = figma_json.get("thumbnailUrl")

    initial_state: AgentState = {
        "figma_json": figma_json,
        "original_figma_json": figma_json,  # Keep original for thumbnail URL
        "ds_catalog": DS_CATALOG,
        "ds_config": ds_config,
        "design_tokens": design_tokens,
        "figma_screenshots": figma_screenshots or ({"main": thumbnail_url} if thumbnail_url else None),
        "ir_tree": None,
        "component_mappings": None,
        "generated": None,
        "validation_errors": [],
        "repair_attempt": 0,
        "messages": []
    }

    print('invoking workflow...')
    workflow = create_workflow()
    # Set recursion limit to prevent infinite loops (max 10 iterations through the graph)
    final_state = workflow.invoke(initial_state, config={"recursion_limit": 50})
    print('workflow invoked.')

    # Print metrics summary
    print(METRICS.summary())

    # FIX: Return generated artifact or create empty one
    if final_state.get("generated"):
        return final_state["generated"]
    else:
        # Return minimal artifact with errors
        return GeneratedAngularArtifact(
            component_name="failed-generation",
            files=[],
            ds_components_used=[],
            imports=[],
            unresolved_nodes=[
                {"error": err.message} 
                for err in final_state.get("validation_errors", [])
            ]
        )

if __name__ == "__main__":
    with open("example_figma_tree.json") as f:
        figma_data = json.load(f)
    with open("example_design_system.json") as f:
        ds_data = json.load(f)

    result = run_figma_to_angular(figma_json=figma_data, ds_json=ds_data)

    for file in result.files:
        os.makedirs(os.path.dirname(file.path) if os.path.dirname(file.path) else ".", exist_ok=True)
        with open(file.path, "w") as f:
            f.write(file.content)

    print(f"Generated {len(result.files)} files")