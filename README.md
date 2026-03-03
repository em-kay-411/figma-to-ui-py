# Figma → Angular Code Generation Pipeline

An LLM-powered pipeline that converts a Figma design export into production-ready Angular component files (`.ts`, `.html`, `.scss`) using a user-configured design system catalog. Supports any Angular design system (PrimeNG, Angular Material, custom DS, etc.) through a single JSON catalog file — no compodoc or DS-specific tooling required.

---

## Table of Contents

1. [Overview](#overview)
2. [High-Level Architecture](#high-level-architecture)
3. [Repository Structure](#repository-structure)
4. [Setup & Configuration](#setup--configuration)
5. [Complete Workflow — Step by Step](#complete-workflow--step-by-step)
6. [Pipeline Deep Dive](#pipeline-deep-dive)
   - [Stage 1 — Ingest Figma](#stage-1--ingest-figma)
   - [Stage 2 — Build IR](#stage-2--build-ir)
   - [Stage 3 — Map to Design System](#stage-3--map-to-design-system)
   - [Stage 4 — Generate Angular Code](#stage-4--generate-angular-code)
   - [Stage 5 — Validate](#stage-5--validate)
   - [Stage 6 — Repair](#stage-6--repair)
7. [Component Hierarchy Context](#component-hierarchy-context)
8. [Variant Resolution System](#variant-resolution-system)
9. [Tag Safety: Whitelist & No-Hallucination Rules](#tag-safety-whitelist--no-hallucination-rules)
10. [Phase 1: Documentation Research](#phase-1-documentation-research)
11. [Design System Catalog Format](#design-system-catalog-format)
12. [Script Reference](#script-reference)
    - [run_agent.py](#run_agentpy--main-cli-runner)
    - [scrape_ds_docs.py](#scrape_ds_docspy--javascript-aware-doc-scraper)
    - [doc_knowledge_builder.py](#doc_knowledge_builderpy--knowledge-base-builder)
    - [generate_figma_hints.py](#generate_figma_hintspy--hint-generator)
13. [REST API Server](#rest-api-server)
14. [Data Models](#data-models)
15. [Global State](#global-state)
16. [Caching System](#caching-system)
17. [LLM Calls Summary](#llm-calls-summary)
18. [Key Design Decisions](#key-design-decisions)
19. [Adding a New Design System](#adding-a-new-design-system)
20. [Output Files](#output-files)

---

## Overview

The pipeline takes a Figma JSON export and a design system catalog and produces a complete Angular standalone component. Every decision is driven by the catalog — the pipeline is fully generic and works with any Angular DS.

| Input | File | Required |
|---|---|---|
| Figma design export | `figma_tree.json` | Yes |
| Design system catalog | `design_systems/{name}_catalog.json` | Yes |
| Design screenshot | CLI arg or `figma_screenshots.json` | No |
| Design tokens | `design_tokens.json` | No |

The catalog JSON defines every DS component: its selector, Figma layer name hints, documentation URLs, HTML classes, Angular directives, and inner content strategy. A set of one-time scripts populate the catalog automatically from live documentation.

---

## High-Level Architecture

```
┌──────────────────────────────────────────────────────────────────────┐
│                              INPUTS                                   │
│                                                                       │
│   figma_tree.json     {name}_catalog.json     screenshot (optional)  │
│      (required)           (required)          design_tokens (optional)│
└────────────┬─────────────────┬───────────────────────────────────────┘
             │                 │
             ▼                 ▼
┌──────────────────────────────────────────────────────────────────────┐
│                      run_figma_to_angular()                           │
│                    figma_to_angular_agent.py                          │
│                                                                       │
│  Loads catalog → populates module globals (DS_CATALOG,               │
│  DS_CATALOG_ENTRY_MAP, DOC_KNOWLEDGE) → builds LangGraph workflow    │
│  → invokes it with initial state                                     │
└────────────────────────────┬─────────────────────────────────────────┘
                             │
                             ▼
┌──────────────────────────────────────────────────────────────────────┐
│                        LANGGRAPH WORKFLOW                             │
│                                                                       │
│  ┌──────────────┐  ┌──────────┐  ┌────────────┐  ┌───────────────┐  │
│  │  1. INGEST   │─▶│ 2. BUILD │─▶│  3. MAP TO │─▶│  4. GENERATE  │  │
│  │     FIGMA    │  │    IR    │  │     DS     │  │     CODE      │  │
│  └──────────────┘  └──────────┘  └────────────┘  └──────┬────────┘  │
│                                                          │            │
│                                                          ▼            │
│                                                  ┌────────────┐      │
│                                                  │ 5. VALIDATE│      │
│                                                  └──────┬─────┘      │
│                                                         │             │
│                                         ┌───────────────┴──────┐     │
│                                         │    errors?            │     │
│                                         ▼ yes (≤2 attempts)    │     │
│                                  ┌────────────┐                │     │
│                                  │  6. REPAIR │────────────────┘     │
│                                  └────────────┘  no errors / max     │
│                                                         │             │
│                                                         ▼             │
│                                                       [END]           │
└──────────────────────────────────────────────────────────────────────┘
                             │
                             ▼
┌──────────────────────────────────────────────────────────────────────┐
│                             OUTPUTS                                   │
│                                                                       │
│   output/generated/                                                   │
│   ├── {name}.component.ts      Angular standalone component class    │
│   ├── {name}.component.html    Template with DS selectors + classes  │
│   ├── {name}.component.scss    Bespoke styles only (utility-first)   │
│   ├── metadata.json            Component map & DS components used    │
│   └── pipeline_log.txt         Step timings & LLM call counts        │
└──────────────────────────────────────────────────────────────────────┘
```

---

## Repository Structure

```
langgraph-implementation/
│
│  ── Core pipeline ──────────────────────────────────────────────────
│
├── figma_to_angular_agent.py     Main pipeline (~3000 lines)
│                                  LangGraph workflow, all stages,
│                                  variant resolution, tag whitelist,
│                                  code-gen prompts, repair logic
├── run_agent.py                  CLI runner — loads files, runs pipeline,
│                                  writes output/generated/
├── api.py                        FastAPI REST server — sessions, generate,
│                                  refine endpoints
├── session_store.py              In-memory session store (TTL: 60 min)
├── doc_scraper.py                HTML scraper with md5-keyed disk cache
│
│  ── One-time / setup scripts ──────────────────────────────────────
│
├── scrape_ds_docs.py             Selenium-based JS-aware doc scraper
│                                  → writes design_systems/{ds}_docs/
├── doc_knowledge_builder.py      Utility class knowledge builder +
│                                  catalog enrichment (--enrich-catalog)
│                                  → writes {ds}_knowledge.json +
│                                    updates catalog with import/directive/
│                                    class/variant/inner_html metadata
├── generate_figma_hints.py       LLM-generates figma_hints for catalog
│                                  components → writes back to catalog
│
│  ── Inputs ─────────────────────────────────────────────────────────
│
├── figma_tree.json               Figma design export (place yours here)
├── design_tokens.json            (optional) design token map
├── figma_screenshots.json        (optional) {key: image_url/path} map
│
│  ── Design system configs ──────────────────────────────────────────
│
├── design_systems/
│   ├── template_catalog.json     Full template with all fields & comments
│   ├── primeng_catalog.json      Pre-configured PrimeNG example
│   ├── {name}_docs/              Local Markdown docs (from scrape_ds_docs)
│   │   ├── layout/{slug}.md
│   │   ├── content/{slug}.md
│   │   ├── utilities/{slug}.md
│   │   └── components/{name}.md
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

Core dependencies: `langchain`, `langchain-openai`, `langgraph`, `pydantic`, `python-dotenv`, `requests`, `fastapi`, `uvicorn`

Optional (only for `scrape_ds_docs.py`): `selenium`, `html2text`, `beautifulsoup4`

### 2. Set environment variable

```bash
# .env
OPENAI_API_KEY=sk-...
```

### 3. Place your Figma export

Export your Figma design as JSON and save it as `figma_tree.json`.
The file can be a raw Figma REST API response (`GET /v1/files/:key`) or a Figma plugin export.

### 4. Configure your design system

```bash
cp design_systems/template_catalog.json design_systems/myds_catalog.json
# Fill in: name, prefix, import_path, base_url, component_example,
#          component entries (name, selector, description, urls)
```

PrimeNG users: `design_systems/primeng_catalog.json` is already provided.

### 5. (One-time) Generate figma_hints

```bash
python generate_figma_hints.py myds
```

### 6. (One-time) Scrape documentation

For JavaScript-rendered documentation sites:
```bash
pip install selenium html2text beautifulsoup4
python scrape_ds_docs.py myds
```

For simpler sites, skip this and let the runtime agent fetch on demand.

### 7. (One-time) Build utility class knowledge base

```bash
python doc_knowledge_builder.py myds
```

### 8. (One-time) Enrich catalog with import/class/directive metadata

```bash
python doc_knowledge_builder.py myds --enrich-catalog
```

This reads component documentation and adds `import_statement`, `component_classes`, `directives`, `base_classes`, `variant_class_map`, and `inner_html_note` to each catalog entry.

### 9. Run the pipeline

```bash
python run_agent.py myds
python run_agent.py myds screenshot.png    # with a Figma screenshot
```

---

## Complete Workflow — Step by Step

```
1. scrape_ds_docs.py myds              (once — Selenium, JS-rendered docs)
        │
        └──▶  design_systems/myds_docs/
                ├── layout/*.md
                ├── utilities/*.md
                └── components/*.md

2. generate_figma_hints.py myds        (once — fills figma_hints[])
        │
        └──▶  design_systems/myds_catalog.json  (updated in place)

3. doc_knowledge_builder.py myds       (once — reads local .md or fetches URLs)
        │
        └──▶  design_systems/myds_knowledge.json
              (utility classes: flex, gap, shadow, color, etc.)

4. doc_knowledge_builder.py myds --enrich-catalog   (once per catalog update)
        │
        └──▶  design_systems/myds_catalog.json  (adds per-component metadata:
              import_statement, component_classes, directives,
              base_classes, variant_class_map, inner_html_note)

5. run_agent.py myds [screenshot.png]  (every design)
        │
        └──▶  output/generated/
              ├── {name}.component.ts
              ├── {name}.component.html
              ├── {name}.component.scss
              ├── metadata.json
              └── pipeline_log.txt
```

Steps 1–4 only need to be re-run when the design system changes or new components are added.

---

## Pipeline Deep Dive

The pipeline is a **LangGraph `StateGraph`** with six nodes sharing a single `AgentState` TypedDict. Each node receives the full state, mutates it in place, and returns it.

```
AgentState fields
────────────────────────────────────────────────────────────────
figma_json              dict       Cleaned Figma tree
original_figma_json     dict       Raw Figma JSON
ds_catalog              dict       Internal catalog for validation
ds_config               dict       Catalog metadata (name, prefix, …)
ds_catalog_entries      list       Raw component list from catalog
ds_knowledge            dict|None  Pre-built utility class knowledge
design_tokens           dict|None  Optional token map
figma_screenshots       dict|None  {key: image_url_or_path}
ir_tree                 list|None  Semantic IR nodes (Stage 2)
component_mappings      list|None  DS mappings per node (Stage 3)
generated               artifact   Generated files (Stage 4)
validation_errors       list       Errors from Stage 5
repair_attempt          int        Repair counter (max 2)
messages                list       LLM message history
```

---

### Stage 1 — Ingest Figma

**Node:** `ingest_figma_node` | **LLM:** No

Cleans and normalises the raw Figma JSON into a compact, structured tree using a recursive `clean_node()` function.

```
Raw Figma JSON (full export)
         │
         ▼
    clean_node() — recursive
    ├── Skip nodes where visible == false
    ├── Extract layout:    layoutMode, primaryAxisAlignItems,
    │                      counterAxisAlignItems, itemSpacing,
    │                      paddingTop/Bottom/Left/Right, layoutWrap
    ├── Extract position:  absoluteBoundingBox, absoluteRenderBounds
    ├── Extract styling:   fills, strokes, cornerRadius, effects,
    │                      blendMode, opacity
    ├── Extract text:      characters → properties.text
    │                      style     → styling.textStyle
    │                      (fontSize, fontWeight, fontFamily,
    │                       textAlignHorizontal, lineHeightPx)
    ├── Extract misc:      interactions, boundVariables,
    │                      componentPropertyReferences,
    │                      componentProperties  ← Figma variant data
    │                      clipsContent, scrollBehavior
    └── Recurse into children
         │
         ▼
Cleaned Figma tree  +  figma_metadata
  (components, componentSets, styles, name, version)
```

**`componentProperties` capture:** INSTANCE nodes in Figma carry a `componentProperties` object that encodes which variant is currently applied (e.g. `{"Color": {"value": "Primary", "type": "VARIANT"}, "Size": {"value": "Large", "type": "VARIANT"}}`). This is captured in `clean_node` and flows into the variant resolution system in Stage 4.

**Result:** typically 40–60% smaller than the raw export.

---

### Stage 2 — Build IR

**Node:** `build_ir_node` | **LLM:** Yes (gpt-4o, 1–N calls)

Converts the cleaned Figma tree into a semantic *Intermediate Representation* (IR), annotating each node with a semantic type and layout classification.

**Compact tree representation:**

Before sending to the LLM, the cleaned tree is further compressed via `_create_compact_tree_representation()`, which keeps only: `id`, `name`, `type`, `layout`, `text` (from properties), `componentProperties` (variant data), and a trimmed `styling` snapshot. This ensures variant data survives into the IR nodes.

**Size-based routing:**

```
compact tree size ≤ 100 KB?
         │
    YES  ▼  NO ──▶ _flatten_figma_tree()
         │              └── split into 50-node chunks
  _process_single_tree()      └── LLM call per chunk
  (1 LLM call, full tree)         └── _reconstruct_ir_hierarchy()
```

**IR semantic types (28 values):**

```
container   text       button    input      image     icon      card
list        toolbar    divider   chip       badge     tab       menu
dialog      form       select    checkbox   radio     toggle    slider
progress    stepper    table     nav        header    footer    link
avatar      form-field expansion-panel  sidenav   unknown
```

**IR node shape:**
```json
{
  "id":         "1:42",
  "type":       "button",
  "name":       "Button / Primary / Large",
  "layout":     "flex-row",
  "properties": {
    "text": "Get Started",
    "componentProperties": {
      "Color": {"value": "Primary", "type": "VARIANT"},
      "Size":  {"value": "Large",   "type": "VARIANT"}
    }
  },
  "styling":    {"fills": [...], "effects": [...]},
  "children":   []
}
```

---

### Stage 3 — Map to Design System

**Node:** `map_to_design_system_node` | **LLM:** Conditional (0–N calls)

Maps every IR node to a DS component selector or native HTML tag using a multi-tier scoring system.

**Six-signal scoring (`_score_catalog_match`):**

```
Signal                                          Points
──────────────────────────────────────────────────────
Exact word-boundary hint match in layer name    +70
Substring hint match in layer name              +45  (only if no exact hit)
IR semantic type matches catalog entry name     +25
Figma node type in entry's figma_node_types     +15
Node is a Figma INSTANCE node                    +5
Any child's name contains a catalog hint        +15
──────────────────────────────────────────────────────
Maximum score (capped)                          100
```

**Text node classification (deterministic, no LLM):**

```
Figma node type == "text"
         │
         ▼
classify_figma_text_as_html(figma_node)
  reads textStyle.fontSize + fontWeight
  fontSize ≥ 48  (or ≥36 bold) → h1
  fontSize ≥ 32  (or ≥24 bold) → h2
  else                         → p
```

**Three routing tiers:**

```
Score ≥ 70  →  Tier 1: definite match (deterministic)
               Best entry used immediately; API URL primed in cache

30 ≤ score < 70  →  Tier 2: ambiguous
                    Collected for a single batch LLM call
                    (_resolve_ambiguous_with_llm)

1 ≤ score < 30  →  Tier 3: low confidence
                   Per-node LLM call with full subtree context
                   (_resolve_low_confidence_nodes)

score == 0  →  native HTML fallback (div, section, etc.)
```

**Output — `DSComponentMapping` per node:**
```python
DSComponentMapping(
    figma_node_id = "1:42",
    ds_component  = "button",     # catalog entry name, or "native"
    ds_selector   = "mt-button",  # HTML tag to use
    inputs        = {},
)
```

---

### Stage 4 — Generate Angular Code

**Node:** `generate_angular_code_node` | **LLM:** Yes (Phase 1 tool loop + 1 structured call)

Two sub-phases: a documentation research loop, then structured code generation.

#### Sub-phase A — Documentation Research (Phase 1)

An LLM tool-calling loop (up to 10 iterations) gathers all context needed to write correct, idiomatic code.

**Three tools available:**

```
search_doc_knowledge(query, section)
    Searches the pre-built utility class knowledge base
    ({name}_knowledge.json built by doc_knowledge_builder.py)
    section: "layout" | "content" | "utilities" | "all"
    Returns: JSON of matching class groups with descriptions + examples

fetch_doc_page(url)
    Fetches any URL via DocScraper (cached under design_systems/cache/)
    Max 8,000 chars returned

fetch_component_docs(component, doc_type)
    Looks up a component by name OR selector in DS_CATALOG_ENTRY_MAP
    doc_type: "api" | "usage" | "overview"
    Returns: scraped page text (cached, 8,000 chars) with header
    Falls back through URL types if one is missing
```

**Phase 1 runs when:**
- `DOC_KNOWLEDGE` is loaded (`{name}_knowledge.json` exists), OR
- Any DS components were mapped (to fetch their API docs)

**Output:** A plain-text summary injected into the code-gen system prompt:
```
## Utility Classes
  flex, flex-column, gap-3, p-4, shadow-sm, text-primary, …

## Component APIs
  ### button
  @Input() label: string
  @Input() severity: "primary" | "secondary" | "danger"
  @Output() onClick: EventEmitter<MouseEvent>
  …
```

#### Sub-phase B — Structured Code Generation

**Context object assembled for the LLM:**

```
{
  component_name:                    "WelcomePageComponent"
  design_structure:                  full tree with CSS-ready values
  component_hierarchy_with_ds_mappings:
    IR tree annotated per node with:
      ds_component, ds_selector
      resolved_classes    ← CSS classes from catalog (base + variant)
      resolved_directives ← Angular attribute directives from catalog
      inner_html_note     ← how to fill this component's inner content
  utility_classes_context:           Phase 1 output
  visual_styling_from_screenshot:    (if screenshot provided)
}
```

**Screenshot analysis (when screenshot provided):**

The screenshot is sent to GPT-4 Vision alongside the design JSON. It extracts exact hex colors, pixel spacing, font sizes, border-radius, and shadow values to use as authoritative overrides in the SCSS.

**Structured output:** `GeneratedAngularArtifact` (Pydantic model) containing three files + metadata.

---

### Stage 5 — Validate

**Node:** `validate_node` | **LLM:** No

Heuristic checks on the generated code:

- Generated artifact exists and has files
- Each DS component selector in `ds_components_used` is either a known DS selector or a standard HTML tag
- **DS usage ratio:** if fewer than 2 DS component references are found in a template with more than 5 elements → `low_ds_usage` error → triggers repair

---

### Stage 6 — Repair

**Node:** `repair_node` | **LLM:** Yes (1 structured-output call) | **Conditional**

```
should_repair() routing
├── validation_errors == []   →  "complete"  →  END
├── repair_attempt >= 2       →  "complete"  →  END  (accept with errors)
└── otherwise                 →  "repair"
        │
        ▼
LLM receives:
  - error list
  - first 20 component mappings
  - all current generated files
  - DS component hints from catalog

LLM outputs:
  - new GeneratedAngularArtifact (replaces current)
        │
        ▼
back to validate_node
```

---

## Component Hierarchy Context

`_build_component_hierarchy_context()` annotates every IR node with four fields resolved from the catalog before the code-gen LLM is called:

| Field | Type | Source | Description |
|---|---|---|---|
| `resolved_classes` | `List[str]` | Catalog `base_classes` + variant matching | CSS classes to apply unconditionally + variant classes |
| `resolved_directives` | `List[str]` | Catalog `directives[].selector` | Angular attribute directives to emit as bare attributes |
| `inner_html_note` | `str` (optional) | Catalog `inner_html_note` | One-sentence guide on valid inner content for this element |
| `ds_component` / `ds_selector` | `str` | Stage 3 mapping | Component name and HTML tag |

**Example context node for a primary button:**

```json
{
  "id": "1:42",
  "name": "Button / Primary / Large",
  "type": "button",
  "ds_component": "button",
  "ds_selector": "button",
  "resolved_classes": ["mt-btn", "mt-btn-primary", "mt-btn-lg"],
  "resolved_directives": ["mtButton"],
  "inner_html_note": "Place text label as direct child; no inner DS tags.",
  "inputs": {}
}
```

The code-gen LLM is instructed to treat `resolved_classes` and `resolved_directives` as **authoritative** — use them exactly, do not guess or add extra classes.

**Directive-based components:** When `ds_selector` is a native HTML element (`button`, `input`, etc.) and `resolved_directives` is non-empty, the LLM generates the native tag with directives as bare attributes:

```html
<!-- resolved_classes: ["mt-btn", "mt-btn-primary"], resolved_directives: ["mtButton"] -->
<button mtButton class="mt-btn mt-btn-primary">Get Started</button>
```

---

## Variant Resolution System

The pipeline extracts variant information from three sources and matches it against the catalog's `variant_class_map` using a three-tier algorithm.

### Token Extraction (`_extract_variant_tokens`)

**Source 1 — `componentProperties` from the IR node** (preserved from Figma via `_create_compact_tree_representation`):
```json
{"Color": {"value": "Primary", "type": "VARIANT"}, "Size": {"value": "Large", "type": "VARIANT"}}
→ tokens: ["primary", "large"]
```

**Source 2 — `componentProperties` from the raw Figma node** (via `figma_lookup`, reliable fallback if the IR LLM drops it):
```
Same structure, same extraction
```

**Source 3 — The Figma layer name** (most universally present source), split by `_VARIANT_TOKEN_RE = r'[/\-_\s,|]+'`:
```
"Button / Primary / Large"  → ["button", "primary", "large"]
"btn-secondary-outlined"    → ["btn", "secondary", "outlined"]
"mt-button_primary_lg"      → ["mt", "button", "primary", "lg"]
```

All tokens are deduplicated, lowercased, and filtered to ≥2 characters.

### Class Matching (`_match_variant_classes`)

Matches the token list against `variant_class_map` keys in three priority tiers. Each catalog key is used **at most once** (first-match wins per key):

```
Tier 1 — Exact match
  token == catalog_key (lowercase)
  "primary" == "primary"  → ["mt-btn-primary"] ✓

Tier 2 — Word-boundary match
  token appears as a whole word inside catalog_key
  (key split by _VARIANT_TOKEN_RE)
  token="large"  key="large-outlined" words={"large","outlined"}  → match ✓

Tier 3 — Substring match  (tokens/keys ≥ 4 chars only)
  token is contained in catalog_key, or catalog_key is contained in token
  token="prim"  key="primary"  → "prim" in "primary"  → match ✓
  Guards against short-token false positives
```

**Result:** `resolved_classes = base_classes + variant_classes`

**Example catalog `variant_class_map`:**
```json
{
  "variant_class_map": {
    "primary":   ["mt-btn-primary"],
    "secondary": ["mt-btn-secondary"],
    "large":     ["mt-btn-lg"],
    "small":     ["mt-btn-sm"],
    "outlined":  ["mt-btn-outlined"]
  }
}
```

For a node named `"Button / Primary / Large"` with `base_classes: ["mt-btn"]`:
```
resolved_classes = ["mt-btn", "mt-btn-primary", "mt-btn-lg"]
```

---

## Tag Safety: Whitelist & No-Hallucination Rules

The code-gen system prompt enforces two mechanisms to prevent the LLM from inventing DS component tags that don't exist.

### Whitelist

`build_catalog_code_gen_prompt()` appends to the mapping guide:

```
ALLOWED CUSTOM ELEMENT TAGS — COMPLETE WHITELIST:
  <mt-button>  <mt-card>  <mt-input>  <mt-select>  <mt-segmented-control>  …

DO NOT use any other custom element tag. NEVER invent child tags like
<mt-option>, <mt-segment>, <p-item>, <ds-child>, or any unlisted variant.
```

The whitelist is built from every catalog component whose `selector` is **not** a native HTML element (detected via `_NATIVE_HTML_TAGS` frozenset).

### Directive-based component section

Components whose `selector` is a native HTML element are listed separately:

```
DIRECTIVE-BASED COMPONENTS (use native HTML tag + directives):
  - button → <button> (native HTML + [directives: mtButton] + resolved_classes)
```

### Inner content rule

The system prompt also states:

> For components that manage their own inner content (selects, dropdowns, segmented controls, autocompletes, tab groups): pass items/options as `@Input()` arrays or objects — do NOT add inner component tags. Close the tag with no children, or use only native HTML inside if `inner_html_note` explicitly says to.

### `inner_html_note` per node

When a catalog entry has `inner_html_note`, it is included in the component hierarchy context for that node and the LLM is instructed to follow it exactly:

```json
{
  "ds_component": "select",
  "inner_html_note": "Pass options via [options] input (array); no inner option tags."
}
```

---

## Phase 1: Documentation Research

Phase 1 runs before code generation to gather DS-specific class names and component APIs. It only runs when there is work to do:

```
has_phase1_work = bool(DOC_KNOWLEDGE) OR bool(matched_ds_components AND DS_CATALOG_ENTRY_MAP)
```

If neither condition is true, Phase 1 is skipped entirely.

**Two caches:**

| Cache directory | Populated by | Content | Max chars/file |
|---|---|---|---|
| `design_systems/builder_cache/` | `doc_knowledge_builder.py` | Full utility class doc pages | 50,000 |
| `design_systems/cache/` | Agent at runtime via `fetch_component_docs` | Component API/usage pages | 8,000 |

**Local doc file priority:** When `design_systems/{ds}_docs/{section}/{slug}.md` exists (from `scrape_ds_docs.py`), it is read directly — no network request is made. This solves the common problem of JavaScript-rendered documentation sites that `requests.get()` cannot read.

---

## Design System Catalog Format

The catalog is the **single configuration file** for a design system.
File: `design_systems/{name}_catalog.json`

```jsonc
{
  // ── Metadata ──────────────────────────────────────────────────────
  "name":          "MyDesignSystem",
  "framework":     "angular",
  "prefix":        "mt",
  "base_url":      "https://docs.myds.com",
  "import_path":   "@company/myds",
  "component_example": {
    "imports_example":   "import { MtButtonModule } from '@company/myds/button';",
    "decorator_imports": "MtButtonModule, CommonModule"
  },

  // ── Utility class doc pages (scraped by doc_knowledge_builder.py) ─
  "layout":    [{"title": "Flexbox",    "url": "https://..."}],
  "content":   [{"title": "Typography", "url": "https://..."}],
  "utilities": [{"title": "Colors",     "url": "https://..."}],

  // ── Component definitions ─────────────────────────────────────────
  "components": [
    {
      // Core identification
      "name":        "button",
      "selector":    "mt-button",          // HTML tag or native element
      "description": "Interactive button",

      // Figma matching (auto-generated by generate_figma_hints.py)
      "figma_hints":      ["button", "btn", "cta", "action", "submit"],
      "figma_node_types": ["INSTANCE", "COMPONENT", "FRAME"],

      // Documentation URLs (for Phase 1 scraping)
      "urls": {
        "overview": "https://docs.myds.com/components/button",
        "api":      "https://docs.myds.com/components/button#api",
        "usage":    "https://docs.myds.com/components/button/usage"
      },

      // ── Fields auto-populated by --enrich-catalog ─────────────────

      // Angular import (added to the .ts component file)
      "import_statement":  "import { MtButtonModule } from '@company/myds/button';",
      "component_classes": ["MtButtonModule"],

      // Attribute directives emitted as bare HTML attributes
      "directives": [
        {"selector": "mtButton", "description": "Activates DS button styling on native element"}
      ],

      // CSS classes always applied to the element (regardless of variant)
      "base_classes": ["mt-btn"],

      // CSS classes to add based on Figma variant property values
      // Keys are lowercase and matched against componentProperties values
      // and Figma layer name tokens using 3-tier logic
      "variant_class_map": {
        "primary":   ["mt-btn-primary"],
        "secondary": ["mt-btn-secondary"],
        "large":     ["mt-btn-lg"],
        "small":     ["mt-btn-sm"],
        "outlined":  ["mt-btn-outlined"]
      },

      // One-sentence description of valid inner HTML content
      // The LLM follows this exactly — critical for preventing hallucinated child tags
      "inner_html_note": "Place text label or icon as direct children; no inner DS tags needed."
    },

    // Example: component that uses @Input() instead of CSS classes for variants
    {
      "name":        "alert",
      "selector":    "mt-alert",
      "description": "Status alert banner",
      "base_classes": [],
      "variant_class_map": {},   // variants passed via [severity]="primary" input
      "inner_html_note": "Place message text as direct child; severity via @Input().",
      "figma_hints": ["alert", "banner", "notification", "toast"],
      "figma_node_types": ["INSTANCE", "COMPONENT", "FRAME"],
      "import_statement": "import { MtAlertModule } from '@company/myds/alert';",
      "component_classes": ["MtAlertModule"],
      "directives": [],
      "urls": { "overview": "...", "api": "...", "usage": "..." }
    },

    // Example: component where selector IS a native HTML element (directive pattern)
    {
      "name":        "input",
      "selector":    "input",              // native <input> element
      "description": "Text input with DS styling",
      "base_classes": ["mt-input"],
      "variant_class_map": {},
      "directives": [
        {"selector": "mtInput", "description": "Applies DS input styling to native input"}
      ],
      "inner_html_note": "Self-contained void element — no inner tags.",
      "figma_hints": ["input", "text-field", "search", "email"],
      "figma_node_types": ["INSTANCE", "COMPONENT", "FRAME"],
      "import_statement": "import { MtInputModule } from '@company/myds/input';",
      "component_classes": ["MtInputModule"],
      "urls": { "overview": "...", "api": "...", "usage": "..." }
    }
  ]
}
```

### Selector types

| Selector | Type | Generated HTML | Example |
|---|---|---|---|
| `mt-button` | Custom element | `<mt-button>` | `<mt-button [label]="'Save'">` |
| `button` | Native + directive | `<button mtButton class="...">` | `<button mtButton class="mt-btn mt-btn-primary">Save</button>` |
| `input` | Native + directive | `<input mtInput class="...">` | `<input mtInput class="mt-input" />` |

---

## Script Reference

### `run_agent.py` — Main CLI runner

```
Usage:
  python run_agent.py <design_system>
  python run_agent.py <design_system> <screenshot.png|.jpg>

Examples:
  python run_agent.py primeng
  python run_agent.py primeng figma_export.png
  python run_agent.py myds /path/to/design.jpg
```

**Screenshot priority:**
1. CLI argument (highest)
2. `figma_screenshots.json` if it exists
3. No screenshot (Phase 1 runs without visual context)

**Outputs written to `output/generated/`:**
- `{name}.component.ts` — standalone Angular component
- `{name}.component.html` — template
- `{name}.component.scss` — bespoke styles
- `metadata.json` — component map and DS component list
- `pipeline_log.txt` — step timings, LLM call counts, full log trace

---

### `scrape_ds_docs.py` — JavaScript-aware doc scraper

Renders documentation pages in a real headless Chrome browser and saves them as local Markdown files. These files are then read by `doc_knowledge_builder.py` and the runtime agent — enabling offline operation and correct handling of JS-rendered doc sites.

```
Usage:
  python scrape_ds_docs.py <design_system>
  python scrape_ds_docs.py <design_system> --delay 3
  python scrape_ds_docs.py <design_system> --overwrite
  python scrape_ds_docs.py <design_system> --components-only
  python scrape_ds_docs.py <design_system> --pages-only

Options:
  --delay N           JS render wait in seconds (default: 4)
  --overwrite         Re-scrape pages that already have local files
  --components-only   Skip layout/content/utilities sections
  --pages-only        Skip components

Requirements: pip install selenium html2text beautifulsoup4
              Chrome or Chromium must be installed
              (chromedriver auto-downloaded by Selenium Manager)

Reads:  design_systems/{name}_catalog.json
Writes: design_systems/{name}_docs/
        ├── layout/{slug}.md
        ├── content/{slug}.md
        ├── utilities/{slug}.md
        └── components/{name}.md   (overview + api + usage combined)
```

**What it does per component:** Fetches overview, API, and usage URLs for each catalog component and combines them into a single `.md` file. This multi-section format gives `--enrich-catalog` all the context it needs in one read.

**Boilerplate removal:** Strips `<script>`, `<style>`, `<noscript>`, `<svg>`, `<nav>`, `<header>`, `<footer>`, `<aside>`, cookie banners, sidebars, pagination, and breadcrumbs before converting to Markdown.

**Rate limiting:** 1.5 seconds between page fetches by default.

---

### `doc_knowledge_builder.py` — Knowledge base builder

Two modes: default (utility class knowledge) and `--enrich-catalog` (component metadata).

```
Usage:
  python doc_knowledge_builder.py <design_system>
  python doc_knowledge_builder.py <design_system> --enrich-catalog
  python doc_knowledge_builder.py <design_system> --all
  python doc_knowledge_builder.py <design_system> --overwrite

  --enrich-catalog   Populate import/directive/class metadata in catalog
  --all              Run both modes sequentially
  --overwrite        Re-extract even for already-enriched entries
```

**Default mode — utility class knowledge:**

```
Reads:  design_systems/{name}_catalog.json  (layout/content/utilities sections)
        OR design_systems/{name}_docs/{section}/{slug}.md  (preferred — local files)
Writes: design_systems/{name}_knowledge.json

For each layout/content/utilities page:
  1. Read local .md if it exists (from scrape_ds_docs.py)
  2. Fall back to URL fetch via DocScraper
  3. LLM call → extract {className: description} pairs
  4. Write to knowledge.json

Output shape:
{
  "sections": {
    "layout": {
      "flex": {
        "classes": {"flex": "display flex", "flex-column": "column direction", ...},
        "examples": ["<div class=\"flex gap-3\">...</div>"]
      }
    }
  }
}
```

**`--enrich-catalog` mode — component metadata:**

For each catalog component, reads its local `.md` file (or fetches the API URL) and calls the LLM to extract six fields written back to the catalog:

| Field | Type | Description |
|---|---|---|
| `import_statement` | `str` | Exact TypeScript import line |
| `component_classes` | `List[str]` | Angular module/class names for `@Component imports` |
| `directives` | `List[{selector, description}]` | Attribute directive selectors |
| `base_classes` | `List[str]` | CSS classes always applied to the element |
| `variant_class_map` | `Dict[str, List[str]]` | Variant name → CSS class list mapping |
| `inner_html_note` | `str` | One-sentence inner content guide |

**Skip condition:** A component is skipped if it already has all three of `import_statement`, `base_classes` in catalog, and `inner_html_note`. Use `--overwrite` to re-extract.

**Rate limiting:** 0.5 seconds between LLM calls.

---

### `generate_figma_hints.py` — Hint generator

```
Usage:
  python generate_figma_hints.py <design_system>
  python generate_figma_hints.py <design_system> --overwrite

Reads:  design_systems/{name}_catalog.json
Writes: design_systems/{name}_catalog.json  (in-place update)
```

For each component entry, calls the LLM with the component's name, selector, and description to generate 4–10 short lowercase substrings that designers typically use in Figma layer names.

**Example:** `"button"` → `["button", "btn", "cta", "action", "submit", "primary", "icon-btn"]`

These hints bridge the gap between freeform Figma layer names like `"HeroActionButton"` and the catalog component `mt-button`. The scoring system awards +70 for an exact word-boundary hit, +45 for a substring hit.

**Rate limiting:** 0.3 seconds between LLM calls.

---

## REST API Server

The FastAPI server exposes the pipeline as a stateful multi-session HTTP API.

```bash
uvicorn api:app --reload --port 8000
```

**Session TTL:** 60 minutes of inactivity.

### Endpoints

#### `GET /design-systems`
Returns all available design system names (any `{name}_catalog.json` found in `design_systems/`).

```json
{"design_systems": ["primeng", "myds"]}
```

#### `POST /sessions`
Creates a new session for the given design system.

```json
// Request
{"design_system": "primeng"}

// Response 201
{"session_id": "abc-123", "design_system": "primeng", "created_at": "2026-03-02T10:00:00"}
```

#### `GET /sessions/{session_id}`
Returns session state including chat history and current generated files.

#### `DELETE /sessions/{session_id}`
Deletes a session (204 No Content).

#### `POST /sessions/{session_id}/generate`
Runs the full Figma-to-Angular pipeline. Accepts `multipart/form-data`.

| Field | Type | Required | Description |
|---|---|---|---|
| `figma_json` | file | One of these three | Figma export JSON file |
| `screenshot` | file | is required | Figma screenshot (PNG/JPG) |
| `prompt` | string | | Text description of the desired component |

When `figma_json` is provided, runs the full 6-stage LangGraph pipeline. When only `prompt` is provided (no `figma_json`), calls `generate_from_prompt()` for a prompt-only generation.

**Response:**
```json
{
  "session_id": "abc-123",
  "component_name": "WelcomePageComponent",
  "files": [
    {"path": "welcome-page.component.ts", "content": "...", "file_type": "typescript"},
    {"path": "welcome-page.component.html", "content": "...", "file_type": "html"},
    {"path": "welcome-page.component.scss", "content": "...", "file_type": "scss"}
  ],
  "imports": ["import { ButtonModule } from 'primeng/button';"],
  "ds_components_used": [...],
  "unresolved_nodes": [...],
  "chat_history": [...]
}
```

#### `POST /sessions/{session_id}/refine`
Refines the current generated code using a natural language prompt. Requires a prior `/generate` call.

```json
// Request
{
  "prompt": "Make the button full-width and add a loading state",
  "screenshot_base64": "data:image/png;base64,..."   // optional
}
```

Calls `refine_with_prompt()` which sends the current files, component mappings, and the new prompt to the LLM. Returns the same shape as `/generate`.

---

## Data Models

```python
class GeneratedFile(BaseModel):
    path:      str     # "welcome.component.ts"
    content:   str     # full file content
    file_type: str     # "typescript" | "html" | "scss"

class DSComponentMapping(BaseModel):
    figma_node_id: str
    ds_component:  str              # "button" or "native"
    ds_selector:   str              # "mt-button" or "h2"
    inputs:        Dict[str, str]   # @Input() bindings

class GeneratedAngularArtifact(BaseModel):
    component_name:     str
    files:              List[GeneratedFile]
    ds_components_used: List[DSComponentMapping]
    imports:            List[str]               # all import statements
    unresolved_nodes:   List[Dict[str, str]]    # nodes that couldn't be mapped

class ValidationError(BaseModel):
    file_path:  str
    error_type: str       # "parse_error" | "low_ds_usage" | "unknown_selector"
    message:    str
    line:       Optional[int]
    suggestion: Optional[str]
```

---

## Global State

Four module-level globals are populated by `run_figma_to_angular()` before the workflow starts:

```python
DS_CATALOG: Dict = {}
# Internal catalog structure for validation.
# Shape: {"components": {selector: {name, selector, description}}}

DESIGN_TOKENS: Dict = {}
# Optional token map from design_tokens.json.

DOC_KNOWLEDGE: Dict = {}
# Pre-built utility class knowledge from {name}_knowledge.json.
# Shape: {"sections": {"layout": {"flex": {"classes": {...}}}}}
# Empty dict if no knowledge.json → Phase 1 utility research is skipped.

DS_CATALOG_ENTRY_MAP: Dict = {}
# Fast-lookup map built from catalog components.
# Keys: component name (lowercase) AND selector (lowercase) → full catalog entry dict.
# Example: {"button": {...}, "mt-button": {...}, "card": {...}, "mt-card": {...}}
# Used by fetch_component_docs() and _build_component_hierarchy_context().
```

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
      │         YES → return file content (no network, no LLM)
      │
      NO → fetch URL → strip HTML → write file → return text

Two separate cache directories:
┌────────────────────────────────┬──────────────────────────────────────┐
│ design_systems/builder_cache/  │ Populated by doc_knowledge_builder   │
│                                │ Full utility class pages (≤50 KB)    │
│                                │ One-time, not runtime                │
├────────────────────────────────┼──────────────────────────────────────┤
│ design_systems/cache/          │ Populated at runtime by Phase 1      │
│                                │ Component API/usage pages (≤8 KB)    │
│                                │ Grows with each new component seen   │
└────────────────────────────────┴──────────────────────────────────────┘

Local .md files (from scrape_ds_docs.py):
  design_systems/{ds}_docs/{section}/{slug}.md
  design_systems/{ds}_docs/components/{name}.md
  Checked first by both doc_knowledge_builder.py and the runtime agent.
  Takes priority over URL fetching — enables fully offline operation.
```

Cache entries are permanent until manually deleted. Delete `*.txt` files in `cache/` or `builder_cache/` to force re-fetching.

---

## LLM Calls Summary

| Step | Model | Structured? | When |
|---|---|---|---|
| IR generation | gpt-4o | No (JSON parsed manually) | Always |
| Ambiguous node resolution (batch) | gpt-4o | No | If ambiguous nodes exist (30 ≤ score < 70) |
| Low-confidence node resolution (per-node) | gpt-4o | No | If score 1–29 nodes exist |
| Phase 1 — tool-calling loop | gpt-4o | Via tools | If DOC_KNOWLEDGE or DS components mapped |
| Screenshot analysis | gpt-4o vision | No | If screenshot provided |
| Code generation | gpt-4o | Yes (Pydantic) | Always |
| Repair | gpt-4o | Yes (Pydantic) | If validation fails, max 2× |
| `generate_figma_hints.py` | gpt-4o | No | One-time setup |
| `doc_knowledge_builder.py` — class extraction | gpt-4o | No | One-time setup |
| `doc_knowledge_builder.py` — `--enrich-catalog` | gpt-4o | No | One-time per catalog update |

**Typical cost per run (no repair, warm cache):** 2 LLM calls (IR + code generation) + Phase 1 tool iterations (fast after first run — cache hits).

---

## Key Design Decisions

### 1. Catalog-based, not compodoc-based
The design system is described by a hand-curated (or script-assisted) JSON catalog rather than auto-generated compodoc output. This works for any Angular DS regardless of whether it has compodoc, and keeps the developer in control of the source of truth.

### 2. Deterministic first, LLM second
TEXT nodes are classified into heading levels with zero LLM calls — purely by font size and weight. High-confidence component matches (score ≥ 70) are determined by string matching against `figma_hints`. Only genuinely ambiguous nodes use LLM tokens. This minimises latency and cost.

### 3. Multi-source variant resolution
Variant data is extracted from three sources (IR `componentProperties`, raw Figma `componentProperties`, and layer name tokens) and matched with a 3-tier algorithm (exact → word-boundary → substring). This makes variant resolution robust even when the IR LLM drops structured data.

### 4. Authoritative class/directive injection
`resolved_classes` and `resolved_directives` are computed deterministically from the catalog before the code-gen LLM is called. The LLM is instructed to use them verbatim — removing the need for the LLM to guess DS class names from raw scraped documentation.

### 5. Tag whitelist to prevent hallucinated child elements
The complete list of allowed custom element selectors is injected into the system prompt. Components like `mt-select` and `mt-segmented-control` also carry an `inner_html_note` telling the LLM exactly what (if anything) goes inside them — eliminating hallucinated `<mt-option>`, `<mt-segment>`, etc. tags.

### 6. Two-phase code generation
Phase 1 (tool-calling loop) gathers documentation context — utility classes, component APIs. Phase 2 (structured output) generates code using that context. Separating these lets the code-gen LLM focus entirely on writing correct code.

### 7. Utility-class-first SCSS
The Phase 1 output instructs the code-gen LLM to apply DS utility classes (e.g. `flex`, `gap-3`, `shadow-sm`) as `class=` attributes rather than writing equivalent custom SCSS. Custom SCSS is only written for values with no utility class equivalent.

### 8. Local docs for offline and JS-rendered sites
`scrape_ds_docs.py` uses Selenium to render JavaScript-heavy documentation sites and saves the content as local Markdown files. Both the builder and the runtime agent check for local files before making any network request — enabling fully offline operation after the initial scrape.

### 9. Graceful degradation everywhere
Every optional feature (screenshot, knowledge base, component docs, design tokens, local doc files) is non-fatal. If a URL fails to scrape, a file is missing, or Phase 1 throws an exception, the pipeline logs a warning and continues. The output may be less accurate, but the pipeline never crashes on external failures.

### 10. Chunked IR generation for large designs
Figma exports from complex screens can exceed 100 KB. The pipeline detects this, flattens the tree, processes 50-node chunks in parallel LLM calls, then reconstructs the hierarchy — enabling designs with hundreds of nodes to be processed without hitting context limits.

---

## Adding a New Design System

**Step 1 — Create the catalog**
```bash
cp design_systems/template_catalog.json design_systems/myds_catalog.json
```
Fill in: `name`, `prefix`, `import_path`, `base_url`, `component_example`.
Add one entry per component with `name`, `selector`, `description`, empty `figma_hints: []`, and all three `urls`.

**Step 2 — Generate figma_hints**
```bash
python generate_figma_hints.py myds
```
Review and manually adjust any hints that seem off.

**Step 3 — Scrape documentation** (recommended for JS-rendered docs)
```bash
pip install selenium html2text beautifulsoup4
python scrape_ds_docs.py myds
```
Skip this if your DS has a simple, server-rendered documentation site.

**Step 4 — Build utility class knowledge base**
```bash
# Fill in layout/content/utilities URLs in the catalog first
python doc_knowledge_builder.py myds
```

**Step 5 — Enrich catalog with import and class metadata**
```bash
python doc_knowledge_builder.py myds --enrich-catalog
```
Review the populated `import_statement`, `base_classes`, `variant_class_map`, and `inner_html_note` for each component. Manually correct anything the LLM got wrong — especially `variant_class_map` keys (they must match what Figma's `componentProperties` values look like in your team's designs).

**Step 6 — Run**
```bash
python run_agent.py myds
python run_agent.py myds design.png   # with screenshot
```

**Step 7 — Iterate**
After a few runs, manually tune:
- `figma_hints` for components that aren't being matched correctly
- `variant_class_map` keys for components whose CSS classes aren't resolving
- `inner_html_note` for any component that keeps getting hallucinated child tags

---

## Output Files

```
output/generated/
│
├── {name}.component.ts
│     Angular standalone component.
│     Imports every DS module used in the template.
│     Uses ChangeDetectionStrategy.OnPush.
│
├── {name}.component.html
│     Angular template.
│     DS component selectors (e.g. <mt-button>) for matched nodes.
│     Native HTML (h1, p, div) for text and layout containers.
│     Directive-based elements: native tag + bare directive attrs.
│       e.g. <button mtButton class="mt-btn mt-btn-primary">
│     Utility classes from Phase 1 research in class= attributes.
│     No inline style= attributes — all styling via classes or SCSS.
│
├── {name}.component.scss
│     Bespoke styles only.
│     Exact pixel values that have no utility class equivalent.
│     Component-level overrides, hover states, animations.
│     No SCSS that duplicates a utility class.
│
├── metadata.json
│     {
│       "component_name": "WelcomePageComponent",
│       "ds_components_used": [{figma_node_id, ds_selector, inputs}],
│       "imports": ["import { ButtonModule } from '...';"],
│       "unresolved_nodes": [...]
│     }
│
└── pipeline_log.txt
      PipelineMetrics summary:
      - Step timings (ingest, build_ir, map_ds, generate_code, validate)
      - Total LLM calls, input chars, output chars
      - Full chronological log trace
```

### After generation

1. Copy the three component files to your Angular project: `src/app/{name}/`
2. Install the DS package if not already present (e.g. `npm install @company/myds`)
3. Check `metadata.json → imports` for any modules to add to your `AppModule` or standalone imports
4. Run `ng serve` and review the rendered component
5. Manually adjust pixel values in SCSS to align with your DS spacing scale
