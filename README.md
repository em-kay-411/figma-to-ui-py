# Figma → Angular Code Generation Pipeline

An LLM-powered pipeline that converts a Figma design export into production-ready Angular component files (`.ts`, `.html`, `.scss`) using a user-configured design system catalog. Supports any Angular design system (PrimeNG, Angular Material, etc.) through a single JSON configuration file.

---

## Table of Contents

1. [Overview](#overview)
2. [High-Level Architecture](#high-level-architecture)
3. [Repository Structure](#repository-structure)
4. [Setup & Configuration](#setup--configuration)
5. [Pipeline Deep Dive](#pipeline-deep-dive)
   - [Stage 1 — Ingest Figma](#stage-1--ingest-figma)
   - [Stage 2 — Build IR](#stage-2--build-ir)
   - [Stage 3 — Map to Design System](#stage-3--map-to-design-system)
   - [Stage 4 — Generate Angular Code](#stage-4--generate-angular-code)
   - [Stage 5 — Validate](#stage-5--validate)
   - [Stage 6 — Repair](#stage-6--repair)
6. [Phase 1: Documentation Research](#phase-1-documentation-research)
7. [Data Models](#data-models)
8. [Global State](#global-state)
9. [Design System Catalog Format](#design-system-catalog-format)
10. [Script Reference](#script-reference)
11. [Caching System](#caching-system)
12. [LLM Calls Summary](#llm-calls-summary)
13. [Key Design Decisions](#key-design-decisions)
14. [Adding a New Design System](#adding-a-new-design-system)
15. [Output Files](#output-files)

---

## Overview

The pipeline takes three inputs and produces a complete Angular standalone component:

| Input | File | Required |
|---|---|---|
| Figma design export | `figma_tree.json` | Yes |
| Design system catalog | `design_systems/{name}_catalog.json` | Yes |
| Design tokens | `design_tokens.json` | No |
| Figma screenshots | `figma_screenshots.json` | No |

The catalog JSON is the single source of truth for the design system — it lists every component, its selector, Figma layer name hints, figma node types, and three documentation URLs (overview, API, usage).

---

## High-Level Architecture

```
┌────────────────────────────────────────────────────────────────────┐
│                            INPUTS                                   │
│                                                                     │
│  figma_tree.json   {name}_catalog.json   design_tokens.json        │
│       (required)        (required)            (optional)           │
└───────────────┬──────────────┬───────────────────┬─────────────────┘
                │              │                   │
                ▼              ▼                   ▼
┌────────────────────────────────────────────────────────────────────┐
│                   run_figma_to_angular()                            │
│                 figma_to_angular_agent.py                           │
│                                                                     │
│  Loads catalog → populates globals (DS_CATALOG, DS_CATALOG_ENTRY_  │
│  MAP, DOC_KNOWLEDGE) → builds LangGraph workflow → invokes it      │
└────────────────────────────┬───────────────────────────────────────┘
                             │
                             ▼
┌────────────────────────────────────────────────────────────────────┐
│                     LANGGRAPH WORKFLOW                              │
│                                                                     │
│  ┌─────────────┐   ┌──────────┐   ┌────────────┐   ┌───────────┐  │
│  │  1. INGEST  │──▶│ 2. BUILD │──▶│  3. MAP TO │──▶│ 4. GENERATE│ │
│  │    FIGMA    │   │    IR    │   │     DS     │   │    CODE    │ │
│  └─────────────┘   └──────────┘   └────────────┘   └─────┬─────┘  │
│                                                           │         │
│                                                           ▼         │
│                                                   ┌────────────┐   │
│                                                   │ 5. VALIDATE│   │
│                                                   └──────┬─────┘   │
│                                                          │          │
│                                          ┌───────────────┴────┐    │
│                                          │  errors?           │    │
│                                          ▼ yes (≤2 attempts)  │    │
│                                   ┌────────────┐              │    │
│                                   │  6. REPAIR │──────────────┘    │
│                                   └────────────┘  no errors / max  │
│                                                           │         │
│                                                           ▼         │
│                                                         [END]       │
└────────────────────────────────────────────────────────────────────┘
                             │
                             ▼
┌────────────────────────────────────────────────────────────────────┐
│                           OUTPUTS                                   │
│                                                                     │
│  output/generated/                                                  │
│  ├── {name}.component.ts     Angular standalone component class     │
│  ├── {name}.component.html   Template with DS component selectors  │
│  ├── {name}.component.scss   Bespoke styles only (utility-first)   │
│  └── metadata.json           Component map & DS components used    │
└────────────────────────────────────────────────────────────────────┘
```

---

## Repository Structure

```
langgraph-implementation/
│
│  ── Core pipeline ──────────────────────────────────────────────────
│
├── figma_to_angular_agent.py     Main pipeline (LangGraph, ~2250 lines)
├── run_agent.py                  CLI runner — loads files, calls pipeline
├── doc_scraper.py                HTML scraper with disk-cache (utility)
│
│  ── One-time / setup scripts ──────────────────────────────────────
│
├── doc_knowledge_builder.py      Scrapes layout/content/utilities doc pages
│                                 → builds {name}_knowledge.json
├── generate_figma_hints.py       LLM-generates figma_hints for catalog
│                                 components → writes back to catalog JSON
│
│  ── Inputs ─────────────────────────────────────────────────────────
│
├── figma_tree.json               Figma design export (place yours here)
├── design_tokens.json            (optional) design token map
├── figma_screenshots.json        (optional) {node_id: image_url} map
│
│  ── Design System configs ──────────────────────────────────────────
│
├── design_systems/
│   ├── template_catalog.json     Blank template — copy & fill for any DS
│   ├── primeng_catalog.json      Pre-configured PrimeNG (25 components)
│   ├── cache/                    Runtime doc cache (component API pages)
│   └── builder_cache/            Build-time doc cache (utility class pages)
│
│  ── Generated output ───────────────────────────────────────────────
│
└── output/generated/
    ├── {name}.component.ts
    ├── {name}.component.html
    ├── {name}.component.scss
    ├── metadata.json
    └── pipeline_log.txt
```

---

## Setup & Configuration

### 1. Install dependencies

```bash
pip install -r requirements.txt
```

`requirements.txt`:
```
langchain
langchain-openai
langchain-anthropic
langgraph
pydantic
python-dotenv
```

### 2. Set environment variable

Create a `.env` file in the project root:

```
OPENAI_API_KEY=sk-...
```

### 3. Place your Figma export

Export your Figma design as JSON and save it as `figma_tree.json` in the project root.
The file can be a raw Figma REST API response (`GET /v1/files/:key`) or a plugin export.

### 4. Configure your design system

If using PrimeNG, `design_systems/primeng_catalog.json` is already provided.
For any other DS, copy the template:

```bash
cp design_systems/template_catalog.json design_systems/myds_catalog.json
# then fill in components, URLs, prefix, etc.
```

### 5. (One-time) Generate figma_hints

```bash
python generate_figma_hints.py myds
```

### 6. (Optional) Build utility class knowledge base

```bash
python doc_knowledge_builder.py myds
```

### 7. Run the pipeline

```bash
python run_agent.py myds
```

---

## Pipeline Deep Dive

The pipeline is a **LangGraph `StateGraph`** with six nodes sharing a single `AgentState` TypedDict. The workflow is compiled once via `create_workflow()` and then invoked with the initial state. Each node receives the full state, mutates it, and returns it.

```
AgentState (TypedDict) — flows through every node
─────────────────────────────────────────────────────
figma_json              dict       Cleaned Figma tree (mutated in Stage 1)
original_figma_json     dict       Raw Figma JSON (for thumbnail URL)
ds_catalog              dict       Internal DS catalog for validation
ds_config               dict       Catalog metadata (name, prefix, etc.)
ds_catalog_entries      list       Raw component list from catalog
ds_knowledge            dict|None  Pre-built utility class knowledge
design_tokens           dict|None  Optional token map
figma_screenshots       dict|None  Optional {key: image_url}
ir_tree                 list|None  IR nodes (set in Stage 2)
component_mappings      list|None  DS mappings (set in Stage 3)
generated               artifact   Generated files (set in Stage 4)
validation_errors       list       Errors from Stage 5
repair_attempt          int        Repair counter (max 2)
messages                list       LLM message history
```

---

### Stage 1 — Ingest Figma

**Node:** `ingest_figma_node`
**LLM:** No
**Purpose:** Clean and normalise the raw Figma JSON into a compact, structured tree.

```
Raw Figma JSON (full export)
         │
         ▼
    clean_node() — recursive
    ├── Skip nodes where visible == false
    ├── Extract layout:   layoutMode, primaryAxisAlignItems,
    │                     counterAxisAlignItems, itemSpacing,
    │                     paddingTop/Bottom/Left/Right, layoutWrap
    ├── Extract position: absoluteBoundingBox, absoluteRenderBounds
    ├── Extract styling:  fills, strokes, cornerRadius, effects,
    │                     blendMode, opacity
    ├── Extract text:     characters → properties.text
    │                     style     → styling.textStyle
    │                     (fontSize, fontWeight, fontFamily,
    │                      textAlignHorizontal, lineHeightPx)
    └── Extract misc:     interactions, boundVariables,
                          componentPropertyReferences
         │
         ▼
Cleaned Figma tree + figma_metadata dict
(components, componentSets, styles, name, version)
```

**What gets removed:** raw SVG paths, hidden layers, all Figma internal IDs that aren't useful, verbose properties that don't affect rendering. The result is typically 40–60% smaller than the raw export.

---

### Stage 2 — Build IR

**Node:** `build_ir_node`
**LLM:** Yes (1–N calls, gpt-4o)
**Purpose:** Convert the cleaned Figma tree into a semantic *Intermediate Representation* (IR) — annotating each node with a semantic type and layout classification.

The LLM is asked to identify the semantic role of every node using these types:

```
IRNodeType (28 values)
───────────────────────────────────────────────────────────────────
container   text     button    input    image    icon     card
list        toolbar  divider   chip     badge    tab      menu
dialog      form     select    checkbox radio    toggle   slider
progress    stepper  table     nav      header   footer   link
avatar      form-field         expansion-panel  sidenav  unknown
```

**Size-based routing:**

```
compact tree size ≤ 100 KB
         │
    YES  │  NO
         │   └── _flatten_figma_tree()
         │            └── split into chunks of 50 nodes
         │                  └── LLM call per chunk
         │                        └── _reconstruct_ir_hierarchy()
         ▼
  _process_single_tree()
      └── one LLM call for entire tree

Result: List[IRNode] — hierarchical, preserving parent-child structure
```

**IR node shape:**
```json
{
  "id":         "1:42",
  "type":       "button",
  "name":       "PrimaryBtn",
  "layout":     "flex-row",
  "properties": { "text": "Get Started" },
  "styling":    { "fills": [...], "effects": [...] },
  "constraints": {},
  "children":   []
}
```

---

### Stage 3 — Map to Design System

**Node:** `map_to_design_system_node`
**LLM:** Conditional (0 or 1 batch call)
**Purpose:** Map every IR node to a DS component selector (or native HTML tag). This is the core of the catalog-based matching system.

```
Flat list of IR nodes
         │
         ├── type == "text"?
         │         │
         │         └── classify_figma_text_as_html(figma_node)
         │               Reads textStyle.fontSize + fontWeight
         │               fontSize ≥ 36  →  h1
         │               fontSize ≥ 28  →  h2
         │               fontSize ≥ 22  →  h3
         │               fontSize ≥ 18  →  h4
         │               fontSize ≥ 16  →  h5
         │               else           →  p
         │               (no LLM, fully deterministic)
         │
         ├── No catalog entries? → native HTML fallback (div, section, etc.)
         │
         └── Score each catalog entry: _score_catalog_match()
               ┌────────────────────────────────────────┐
               │  +60  any figma_hint substring found   │
               │        in Figma layer name             │
               │        (case-insensitive)              │
               │  +30  IR semantic type matches         │
               │        entry name exactly              │
               │  Max score: 100                        │
               └────────────────────────────────────────┘
                     │
                     ├── top score ≥ 60  →  definite match (deterministic)
                     │                      fetch API URL into cache
                     │
                     ├── 0 < score < 60  →  ambiguous node
                     │                       collected for batch LLM call
                     │
                     └── score == 0  →  native HTML fallback

 Batch LLM call (if any ambiguous nodes):
   _resolve_ambiguous_with_llm()
   ─ single call with all ambiguous nodes + catalog summary
   ─ returns selector for each node or "div" as fallback
```

**Output — DSComponentMapping per node:**
```python
DSComponentMapping(
    figma_node_id = "1:42",
    ds_component  = "button",        # or "native"
    ds_selector   = "p-button",      # or "h2", "div", etc.
    inputs        = {},
    outputs       = {},
    children_slot = None,
)
```

---

### Stage 4 — Generate Angular Code

**Node:** `generate_angular_code_node`
**LLM:** Yes (Phase 1 tool-calling loop + 1 structured-output call)
**Purpose:** Generate the actual `.ts`, `.html`, and `.scss` files.

This node has two sub-phases:

#### Sub-phase A — Documentation Research (Phase 1)

Before generating code, an LLM tool-calling loop gathers the documentation context needed to write correct, idiomatic code. It has access to three tools:

```
Tools available in Phase 1
──────────────────────────────────────────────────────────────────────────
search_doc_knowledge(query, section)
    Searches the pre-built utility class knowledge base
    (from {name}_knowledge.json built by doc_knowledge_builder.py)
    section: "layout" | "content" | "utilities" | "all"
    Returns: JSON of matching class groups with descriptions + examples

fetch_doc_page(url)
    Fetches any URL via DocScraper (cached)
    Max 8000 chars returned
    Use when a search result URL needs deeper inspection

fetch_component_docs(component, doc_type)
    Looks up a component by name OR selector in DS_CATALOG_ENTRY_MAP
    doc_type: "api" | "usage" | "overview"
    Falls back gracefully through the other URL types if one is missing
    Returns: scraped page text (cached, 8000 chars max) with header
```

**Phase 1 strategy (up to 10 iterations):**

```
PART A — Utility class research (if knowledge.json exists)
  1. search_doc_knowledge("flex", "layout")
  2. search_doc_knowledge("grid", "layout")
  3. search_doc_knowledge("gap spacing", "layout")
  4. search_doc_knowledge("padding", "utilities")
  5. search_doc_knowledge("shadow", "utilities")
  6. search_doc_knowledge("background surface", "utilities")
  7. search_doc_knowledge("text color", "utilities")

PART B — Component docs research (for each matched DS component)
  For every non-native component in component_mappings:
  1. fetch_component_docs("<name>", "api")      ← inputs, outputs, selector
  2. fetch_component_docs("<name>", "usage")    ← real usage examples
  3. fetch_component_docs("<name>", "overview") ← only if needed

Output: plain-text summary with two sections:
  ## Utility Classes   (layout, spacing, color, shadow, typography classes)
  ## Component APIs    (one subsection per component)
```

The Phase 1 output is injected into the code-gen system prompt so the LLM uses real DS utility classes instead of writing custom SCSS, and uses correct component inputs/outputs.

#### Sub-phase B — Structured Code Generation

```
Build context object:
├── component_name         (derived from Figma root node name, PascalCase)
├── design_structure       (_build_design_structure_for_codegen)
│     Full tree with: name, type, layout direction, padding, gap,
│     text content, background color, font properties, shadows,
│     border-radius, opacity, dimensions — all converted to CSS values
├── component_hierarchy_with_ds_mappings
│     IR tree annotated with ds_component + ds_selector per node
├── utility_classes_context  (Phase 1 output)
└── visual_styling_from_screenshot  (if screenshot available)

LLM call → structured output → GeneratedAngularArtifact
  Files produced:
  ├── {name}.component.ts    (standalone component, correct imports)
  ├── {name}.component.html  (DS selectors + utility classes in class=)
  └── {name}.component.scss  (bespoke styles only — no SCSS for things
                              already covered by utility classes)
```

**Component name derivation:**
```
Figma root node name: "welcome-screen / desktop"
    → strip special chars, split on space/dash/underscore
    → capitalise each word → join
    → append "Component"
Result: "WelcomeScreenDesktopComponent"
```

---

### Stage 5 — Validate

**Node:** `validate_node`
**LLM:** No
**Purpose:** Heuristic checks on generated code to decide whether repair is needed.

Checks performed:
- Generated artifact exists and has files
- Each DS component selector in `ds_components_used` is either a known DS selector (from `DS_CATALOG`) or standard HTML
- **DS usage ratio** in the HTML file: counts occurrences of DS selectors vs total element count. If fewer than 2 DS references found in a template with more than 5 elements → `low_ds_usage` error → triggers repair

---

### Stage 6 — Repair

**Node:** `repair_node`
**LLM:** Yes (1 structured-output call)
**Conditional:** Only runs when validation errors exist and `repair_attempt < 2`

```
should_repair() routing
├── validation_errors == []  →  "complete"  →  END
├── repair_attempt >= 2      →  "complete"  →  END (accept with errors)
└── otherwise                →  "repair"    →  repair_node
                                                    │
                                                    ▼
                                          LLM sees:
                                          - error list
                                          - first 20 component mappings
                                          - all current generated files
                                          - DS component hints from catalog
                                          Outputs: new GeneratedAngularArtifact
                                                    │
                                                    ▼
                                          back to validate_node
```

---

## Phase 1: Documentation Research

Phase 1 is the documentation-aware pre-pass before code generation. It only runs when there is work to do:

```
has_phase1_work = bool(DOC_KNOWLEDGE) OR bool(matched_ds_components AND DS_CATALOG_ENTRY_MAP)
```

If neither condition is true (no `knowledge.json` and no DS components were mapped), Phase 1 is skipped entirely and the pipeline generates code exactly as before — using only the design structure and catalog mapping guide.

**Two caches are involved:**

| Cache directory | Populated by | Content | Max chars/file |
|---|---|---|---|
| `design_systems/builder_cache/` | `doc_knowledge_builder.py` | Full utility class doc pages | 50,000 |
| `design_systems/cache/` | Agent at runtime via `fetch_component_docs` | Component API/usage pages | 8,000 |

Both caches use `md5(url)` as filename, so a URL is only fetched once ever.

---

## Data Models

### Pydantic models (structured outputs and validation)

```python
class GeneratedFile(BaseModel):
    path:      str    # e.g. "welcome.component.ts"
    content:   str    # full file content
    file_type: str    # "typescript" | "html" | "scss"

class DSComponentMapping(BaseModel):
    figma_node_id: str
    ds_component:  str            # e.g. "button" or "native"
    ds_selector:   str            # e.g. "p-button" or "h2"
    inputs:        Dict[str, str] # @Input bindings
    outputs:       Dict[str, str] # @Output bindings
    children_slot: Optional[str]  # content projection slot name

class GeneratedAngularArtifact(BaseModel):
    component_name:     str
    files:              List[GeneratedFile]
    ds_components_used: List[DSComponentMapping]
    imports:            List[str]
    unresolved_nodes:   List[Dict[str, str]]

class ValidationError(BaseModel):
    file_path:  str
    error_type: str            # "parse_error" | "low_ds_usage" | etc.
    message:    str
    line:       Optional[int]
    suggestion: Optional[str]
```

### Enums

```python
class LayoutType(str, Enum):
    FLEX_ROW    = "flex-row"
    FLEX_COLUMN = "flex-column"
    GRID        = "grid"
    ABSOLUTE    = "absolute"
    STACK       = "stack"

class IRNodeType(str, Enum):
    # 28 values:
    CONTAINER, TEXT, BUTTON, INPUT, IMAGE, ICON, CARD, LIST,
    TOOLBAR, DIVIDER, CHIP, BADGE, TAB, MENU, DIALOG, FORM_FIELD,
    SELECT, CHECKBOX, RADIO, TOGGLE, SLIDER, PROGRESS, STEPPER,
    TABLE, EXPANSION_PANEL, SIDENAV, NAV, HEADER, FOOTER, LINK,
    AVATAR, FORM, UNKNOWN
```

---

## Global State

Four module-level globals are populated by `run_figma_to_angular()` before the workflow starts. They are read by tool functions and node functions throughout the pipeline.

```python
DS_CATALOG: Dict = {}
# Internal catalog structure for validation.
# Built from the catalog's components list by _build_ds_catalog_from_catalog().
# Shape: {"components": {selector: {name, selector, description}}, "directives": {}}

DESIGN_TOKENS: Dict = {}
# Optional token map from design_tokens.json.

DOC_KNOWLEDGE: Dict = {}
# Pre-built utility class knowledge from {name}_knowledge.json.
# Shape: {"name": "...", "sections": {"layout": {"flex": {"classes": {...}}}}}
# Empty dict if no knowledge.json exists — Phase 1 utility research is skipped.

DS_CATALOG_ENTRY_MAP: Dict = {}
# Fast-lookup map built from catalog components.
# Keys: component name (lowercase) AND selector (lowercase) → catalog entry dict.
# Example: {"button": {...}, "p-button": {...}, "card": {...}, "p-card": {...}}
# Used by fetch_component_docs() to resolve component name/selector to URLs.
```

---

## Design System Catalog Format

The catalog is the **single configuration file** for a design system.
File path: `design_systems/{name}_catalog.json`

```jsonc
{
  // Metadata
  "name":          "PrimeNG",
  "framework":     "angular",
  "prefix":        "p-",               // selector prefix (e.g. p-button)
  "base_url":      "https://primeng.org",
  "import_path":   "primeng",
  "component_example": {
    "imports_example":   "import { ButtonModule } from 'primeng/button';",
    "decorator_imports": "ButtonModule, CommonModule"
  },

  // Doc pages for CSS utility class knowledge base.
  // Scraped by doc_knowledge_builder.py (one-time).
  // Each entry: { "title": "...", "url": "..." }
  "layout":    [ { "title": "Flexbox", "url": "https://..." }, ... ],
  "content":   [ { "title": "Typography", "url": "..." }, ... ],
  "utilities": [ { "title": "Colors",  "url": "..." }, ... ],

  // Component definitions — one entry per DS component.
  "components": [
    {
      "name":        "button",           // internal name (used in DS_CATALOG_ENTRY_MAP)
      "selector":    "p-button",         // Angular selector used in HTML template
      "description": "Interactive button with severity variants",
      "figma_hints": [                   // substrings matched against Figma layer names
        "button", "btn", "cta", "action", "submit", "primary"
      ],
      "figma_node_types": ["INSTANCE", "COMPONENT", "FRAME"],
      "urls": {
        "overview": "https://primeng.org/button",       // what the component looks like
        "api":      "https://primeng.org/button#api",   // @Input/@Output reference
        "usage":    ""                                   // code usage examples (fill in)
      }
    }
  ]
}
```

### figma_hints matching

`figma_hints` are short lowercase substrings checked against each Figma layer name during Stage 3. Matching is case-insensitive and substring-based:

```
Layer name: "PrimaryButtonLarge"
Hint "button" → found → +60 points → definite match (≥60 threshold)

Layer name: "icon-btn-outline"
Hint "btn" → found → +60 points → definite match
Hint "icon" → also found but scoring stops at first hint match
```

Generate hints automatically for any new catalog with:
```bash
python generate_figma_hints.py myds
python generate_figma_hints.py myds --overwrite   # re-generate existing
```

### URL types and when they are fetched

| URL type | Doc content | Fetched by | When |
|---|---|---|---|
| `overview` | Visual examples, design usage | `fetch_component_docs` | Phase 1, if api+usage don't suffice |
| `api` | `@Input`/`@Output` reference, selector | `fetch_component_docs` | Phase 1, first priority |
| `usage` | Code snippets, common patterns | `fetch_component_docs` | Phase 1, second priority |

`layout`, `content`, `utilities` URLs:

| Section | Doc content | Fetched by | When |
|---|---|---|---|
| `layout` | Flex, grid, display classes | `doc_knowledge_builder.py` | One-time build step |
| `content` | Typography, image classes | `doc_knowledge_builder.py` | One-time build step |
| `utilities` | Color, shadow, spacing classes | `doc_knowledge_builder.py` | One-time build step |

---

## Script Reference

### `run_agent.py` — Main runner

```
Usage:  python run_agent.py <design_system>
Example: python run_agent.py primeng
```

Loads `figma_tree.json` (required), `design_tokens.json` and `figma_screenshots.json` (optional), calls `run_figma_to_angular()`, and writes all output files to `output/generated/`.

Also writes `output/generated/pipeline_log.txt` — step timings and LLM call counts.

---

### `doc_knowledge_builder.py` — One-time utility class knowledge builder

```
Usage:  python doc_knowledge_builder.py <design_system>
Example: python doc_knowledge_builder.py primeng

Reads:  design_systems/{name}_catalog.json   (layout/content/utilities sections)
Writes: design_systems/{name}_knowledge.json
Cache:  design_systems/builder_cache/        (full pages, up to 50 KB each)
```

**What it does:**
1. Reads the `layout`, `content`, and `utilities` sections from the catalog (each entry has a `title` and `url`)
2. Fetches each URL using `DocScraper` (cached in `builder_cache/`)
3. Makes one LLM call per page to extract all CSS utility class names and descriptions
4. Writes the structured result to `{name}_knowledge.json`

The `components` section is intentionally skipped — component pages are fetched at runtime on demand by `fetch_component_docs()`.

**When to re-run:** after adding new layout/utility doc pages to the catalog, or when DS utility class documentation is updated.

**Rate limiting:** 0.5 s sleep between LLM calls to stay within API rate limits.

**Output shape (`{name}_knowledge.json`):**
```json
{
  "name": "PrimeNG",
  "built_from": "primeng_catalog.json",
  "sections": {
    "layout": {
      "flex": {
        "url":   "https://...",
        "title": "Flexbox",
        "classes": {
          "flex":               "Applies display:flex",
          "flex-column":        "Sets flex-direction to column",
          "align-items-center": "Centers flex children on cross axis",
          "gap-3":              "Sets gap between flex children to spacing-3"
        },
        "examples": ["<div class=\"flex gap-3\">...</div>"]
      }
    },
    "utilities": { ... },
    "content":   { ... }
  }
}
```

---

### `generate_figma_hints.py` — Auto-generate figma_hints

```
Usage:
  python generate_figma_hints.py <design_system>
  python generate_figma_hints.py <design_system> --overwrite

Reads:  design_systems/{name}_catalog.json
Writes: design_systems/{name}_catalog.json  (in-place update)
```

**What it does:**
For each component entry in `components[]`:
- Skips if `figma_hints` is non-empty (unless `--overwrite` is passed)
- Calls the LLM with the component's name, selector, and description
- Generates 4–10 short lowercase substrings designers would use in Figma layer names
- Examples: `"button"` → `["button", "btn", "cta", "action", "submit", "primary"]`

**Why it is needed:** Figma layer names are freeform and designer-dependent. The hints bridge the gap between a layer name like `"HeroActionButton"` and the DS component `p-button`. The LLM generates comprehensive sets of synonyms and abbreviations that manual entry would miss.

**What it does NOT generate:** hints for `layout`, `content`, or `utilities` sections — those are documentation page lists, not components, and are never matched against Figma layer names.

**Rate limiting:** 0.3 s sleep between LLM calls.

---

### `doc_scraper.py` — HTML scraper with caching (utility class)

Not invoked directly. Used internally by:
- `doc_knowledge_builder.py` (with `cache_dir="design_systems/builder_cache"`)
- `fetch_doc_page()` tool (with default `cache_dir="design_systems/cache"`)
- `fetch_component_docs()` tool (same default cache)
- `map_to_design_system_node` (primes cache for matched component API URLs)

**`DocScraper.fetch(url, max_chars)`:**
1. Computes `md5(url)` → cache filename
2. Cache hit → reads and returns cached text
3. Cache miss → `requests.get(url)` → strip HTML via `HTMLTextExtractor` → write cache → return text
4. Any failure → returns `""` (non-fatal)

`HTMLTextExtractor` skips `<script>`, `<style>`, `<nav>`, `<header>`, `<footer>`, `<noscript>`, `<svg>` tags and collapses whitespace.

---

## Caching System

```
URL requested
      │
      ▼
md5(url) → filename.txt
      │
      ├── file exists in cache dir?
      │         │
      │         YES → return file content (no network)
      │
      NO → fetch URL → strip HTML → write file → return text

Two separate cache directories:
┌────────────────────────────────┬──────────────────────────────────────┐
│ design_systems/builder_cache/  │ Populated by doc_knowledge_builder   │
│                                │ Full utility class pages (≤50 KB)    │
│                                │ Run once, not runtime                │
├────────────────────────────────┼──────────────────────────────────────┤
│ design_systems/cache/          │ Populated at runtime by Phase 1      │
│                                │ Component API/usage pages (≤8 KB)    │
│                                │ Grows with each new component seen   │
└────────────────────────────────┴──────────────────────────────────────┘
```

Cache entries are permanent until manually deleted. Run `scraper.clear_cache()` or delete the `*.txt` files to force re-fetching.

---

## LLM Calls Summary

| Step | Model | Input | Output | Structured? | When |
|---|---|---|---|---|---|
| IR generation | gpt-4o | Compact Figma tree JSON | IR node list (JSON) | No (parsed manually) | Always |
| Ambiguous node resolution | gpt-4o | IR nodes + catalog summary | Mapping list (JSON) | No | Only if ambiguous nodes exist |
| Phase 1 — utility/component research | gpt-4o | Design summary + tool results | Plain-text context | Via tool-calling | Always if DOC_KNOWLEDGE or DS components matched |
| Code generation | gpt-4o | Full design context + Phase 1 output | GeneratedAngularArtifact | Yes (Pydantic) | Always |
| Screenshot analysis | gpt-4o vision | Base64 image + design context | Styling JSON | No (parsed manually) | Only if screenshot URL available |
| Repair | gpt-4o | Errors + current files | GeneratedAngularArtifact | Yes (Pydantic) | Only if validation fails (max 2×) |
| Figma hint generation | gpt-4o | Component name/selector/desc | JSON array of strings | No | `generate_figma_hints.py` only |
| Knowledge extraction | gpt-4o | Scraped doc page text | CSS class JSON | No | `doc_knowledge_builder.py` only |

**Typical cost per run (no repair):** 2 LLM calls (IR + code generation) + Phase 1 iterations (cached after first run).

---

## Key Design Decisions

### 1. Catalog-based, not compodoc-based
The design system is described by a hand-curated (or script-assisted) JSON catalog rather than auto-generated compodoc output. This works for any Angular DS regardless of whether it has compodoc, and puts control in the developer's hands.

### 2. Deterministic mapping first, LLM second
TEXT nodes are classified into heading levels with zero LLM calls — purely by font size and weight. High-confidence component matches (score ≥ 60) are determined by string matching against `figma_hints`. Only genuinely ambiguous nodes go to the LLM. This minimises latency and cost.

### 3. Two-phase code generation
Phase 1 (tool-calling loop) gathers documentation context. Phase 2 (structured output) generates code with that context in the prompt. Separating these lets the code-gen LLM focus only on writing correct code, not on figuring out what classes or APIs exist.

### 4. Utility-class-first SCSS
The Phase 1 output instructs the code-gen LLM to use DS utility classes (like `flex`, `gap-3`, `shadow-sm`) in HTML `class=` attributes rather than authoring equivalent SCSS. Custom SCSS is only written for pixel values and component-level overrides that have no utility class equivalent.

### 5. Graceful degradation everywhere
Every optional feature (screenshots, knowledge base, component docs, design tokens) is non-fatal. If a URL fails to scrape, if a knowledge file is missing, if Phase 1 throws an exception — the pipeline falls through to the next step with a warning and continues. The output may be less accurate but the pipeline never crashes on external failures.

### 6. Chunked IR generation for large designs
Figma exports from complex screens can exceed 100 KB. The pipeline detects this, flattens the tree, processes it in 50-node chunks, then reconstructs the hierarchy — enabling designs with hundreds of nodes to be processed without hitting LLM context limits.

---

## Adding a New Design System

**Step 1 — Create the catalog**
```bash
cp design_systems/template_catalog.json design_systems/myds_catalog.json
```
Fill in: `name`, `prefix`, `base_url`, `import_path`, `component_example`.
Add one entry per component with `name`, `selector`, `description`, empty `figma_hints: []`, and all three `urls`.

**Step 2 — Generate figma_hints**
```bash
python generate_figma_hints.py myds
```
Inspect the output — you can manually edit any hints that seem off before running the pipeline.

**Step 3 — (Optional) Add utility class doc pages**
Fill the `layout`, `content`, and `utilities` arrays with URLs from your DS's documentation. Then build the knowledge base:
```bash
python doc_knowledge_builder.py myds
```

**Step 4 — Run**
```bash
python run_agent.py myds
```

**Step 5 — Fill in component usage URLs over time**
After your first run, add `urls.usage` and `urls.api` URLs to each component entry. Subsequent runs will automatically scrape and cache them during Phase 1.

---

## Output Files

```
output/generated/
├── {name}.component.ts
│     Angular standalone component. Imports every DS module used in
│     the template. Uses ChangeDetectionStrategy.OnPush.
│
├── {name}.component.html
│     Angular template. Uses DS component selectors (e.g. <p-button>,
│     <p-card>) for matched nodes, native HTML tags (h1, p, div) for
│     text and containers. Utility classes in class= attributes.
│
├── {name}.component.scss
│     Bespoke styles only — exact pixel values, component-level
│     overrides, hover states. No SCSS that duplicates a utility class.
│
├── metadata.json
│     { component_name, ds_components_used[], imports[], unresolved_nodes[] }
│     Machine-readable record of every DS component used and every
│     node that could not be mapped.
│
└── pipeline_log.txt
      PipelineMetrics summary: step timings, total LLM calls,
      total input/output characters, and the full chronological log trace.
```

### Next steps after generation

1. Copy the three component files to your Angular project: `src/app/{name}/`
2. Install the DS package if not already present (`npm install primeng`)
3. Check `metadata.json` → `imports` for any modules not yet in your `AppModule` or standalone imports array
4. Run `ng serve` and review the rendered component
5. Manually adjust any pixel values in the SCSS to match your DS spacing scale
