# Figma → Angular Pipeline — Technical Demo Script

---

## SECTION 0 — Opening

> **[Start with the problem statement]**

"Design-to-code is one of the most repetitive and error-prone handoff processes in frontend engineering.
A designer finishes a screen in Figma. A developer has to manually recreate every layout container,
map every visual element to the correct component from the design system — the right selector, the right
CSS classes, the right Angular imports — and then maintain that code as the design evolves.

This pipeline automates the entire process. You hand it a Figma export JSON, a design system catalog,
and optionally a screenshot or a text prompt. It gives you back a production-ready Angular standalone
component — `.ts`, `.html`, `.scss` — with the correct design system selectors, correct variant classes,
no hallucinated tags, and no inline styles.

Let me walk through how every piece works."

---

## SECTION 1 — Repository Layout

> **[Open the repository root]**

```
langgraph-implementation/
│
├── figma_to_angular_agent.py    ← main pipeline (~3800 lines)
├── run_agent.py                 ← CLI runner
├── api.py                       ← FastAPI REST server
├── session_store.py             ← in-memory session store
├── doc_scraper.py               ← HTML scraper + disk cache
│
├── scrape_ds_docs.py            ← one-time: Selenium doc scraper
├── doc_knowledge_builder.py     ← one-time: utility class KB + catalog enrichment
├── generate_figma_hints.py      ← one-time: LLM-generated Figma layer hints
│
├── design_systems/
│   ├── primeng_catalog.json     ← pre-built PrimeNG example
│   ├── template_catalog.json    ← copy-and-fill template
│   ├── primeng_docs/            ← locally scraped Markdown docs
│   └── cache/                   ← runtime doc cache (md5-keyed .txt files)
│
├── figma_tree.json              ← your Figma design export goes here
└── output/generated/            ← pipeline writes .ts/.html/.scss here
```

"There are three categories of files here.

The **core pipeline** lives in `figma_to_angular_agent.py`. Everything — the LangGraph state machine,
all pipeline stages, the scoring logic, the variant resolution, the codegen prompts — is in that one file.

The **one-time setup scripts** are run once per design system to build a knowledge base.
They are not needed on every run.

The **design system catalog** in `design_systems/` is what makes this system generic.
It contains one JSON file per design system. You swap the catalog and the pipeline works
for a completely different design system — PrimeNG, Angular Material, Luminus, anything."

---

## SECTION 2 — The Catalog Format

> **[Open `design_systems/primeng_catalog.json`]**

"Everything the pipeline knows about the design system comes from this file. There is no
hardcoded knowledge about PrimeNG — or any other library — anywhere in the Python code.

The catalog has two parts: metadata and components.

**Metadata:**
```json
{
  "name":        "PrimeNG",
  "framework":   "angular",
  "prefix":      "p",
  "import_path": "primeng",
  "base_url":    "https://primeng.org"
}
```
`prefix` is used in the tag whitelist — the pipeline uses it to distinguish DS custom elements
like `<p-button>` from native HTML elements like `<button>`.

**A component entry looks like this:**
```json
{
  "name":        "dropdown",
  "selector":    "p-dropdown",
  "description": "Single-value select with search and filtering",
  "figma_hints": ["dropdown", "select", "picker", "combobox"],
  "figma_node_types": ["INSTANCE", "COMPONENT"],
  "urls": {
    "api":   "https://primeng.org/dropdown#api",
    "usage": "https://primeng.org/dropdown"
  },
  "base_classes":     [],
  "variant_class_map": {},
  "directives":        [],
  "inner_html_note":   "Pass options via [options] input; no inner option tags.",
  "import_statement":  "import { DropdownModule } from 'primeng/dropdown';",
  "component_classes": ["DropdownModule"]
}
```

The critical fields are:
- `figma_hints` — substrings the pipeline looks for in Figma layer names to score matches
- `selector` — the HTML tag that ends up in the generated template
- `base_classes` + `variant_class_map` — CSS classes applied based on Figma variant data
- `inner_html_note` — one-sentence rule telling the LLM what is valid inner content
- `import_statement` + `component_classes` — copied directly into the `.ts` file

Three fields are auto-populated by the one-time setup scripts. Let me show those quickly."

---

## SECTION 3 — One-Time Setup Scripts

> **[Narrate the setup chain — no live execution needed]**

"There are four one-time scripts. You run them once per design system. After that, every pipeline
run reads from their cached outputs.

### Step 1 — Generate `figma_hints` automatically

```bash
python generate_figma_hints.py primeng
```

This calls the LLM once per catalog component. For each entry it sends:
name, selector, and description — and asks for 5–10 lowercase substrings that a designer
would put in a Figma layer name when using this component.

The result for `dropdown` might be: `['dropdown', 'select', 'picker', 'filter', 'combobox', 'list']`

These hints are written back into the catalog in-place. Without them the scoring system
would have no signal to fire on.

---

### Step 2 — Scrape the documentation

```bash
python scrape_ds_docs.py primeng
```

This uses Selenium + headless Chrome to render every documentation page and save it as
a local Markdown file under `design_systems/primeng_docs/`.

Why Selenium? Because most modern design system documentation sites are JavaScript-rendered
— a plain `requests.get()` returns a nearly empty page. The scraper waits for the JS to
finish, then strips all navigation chrome, sidebars, breadcrumbs, and script tags, and
converts the visible content to clean Markdown.

These local Markdown files are then read by the pipeline at runtime — zero network requests
for docs during actual code generation.

---

### Step 3 — Build the utility class knowledge base

```bash
python doc_knowledge_builder.py primeng
```

This reads all the layout/content/utilities doc pages — either local Markdown files or live URLs
— and calls the LLM to extract structured utility class definitions:

```json
{
  "sections": {
    "layout": {
      "flex": {
        "classes": {
          "flex":        "Sets display: flex",
          "flex-column": "Sets flex-direction: column",
          "gap-3":       "Sets gap: 0.75rem",
          "p-4":         "Sets padding: 1rem"
        }
      }
    }
  }
}
```

This file — `primeng_knowledge.json` — is loaded at pipeline startup and injected into the
Phase 1 research context so the code generation LLM knows exactly which utility classes exist.
Without it, the LLM would write everything in SCSS. With it, it writes `class='flex gap-3 p-4'`
and the SCSS file shrinks to only truly bespoke styles.

---

### Step 4 — Enrich the catalog with component metadata

```bash
python doc_knowledge_builder.py primeng --enrich-catalog
```

For each component in the catalog, this reads its local docs file and calls the LLM to
extract six fields and write them back to the catalog:

- `import_statement` — the exact TypeScript import line
- `component_classes` — what goes into the `@Component imports` array
- `directives` — Angular attribute directive selectors
- `base_classes` — CSS classes always applied regardless of variant
- `variant_class_map` — maps variant property values to CSS classes
- `inner_html_note` — one-sentence inner content rule

These are all pre-computed once and stored in the catalog, so no LLM is needed during
code generation to figure out the correct Angular import for `p-dropdown`.
The pipeline reads it directly."

---

## SECTION 4 — The LangGraph Pipeline

> **[Open `figma_to_angular_agent.py`, scroll to `create_workflow()`]**

"The pipeline is a **LangGraph `StateGraph`**. LangGraph is a library for building stateful
multi-step LLM workflows as directed graphs. Each node in the graph is a Python function.
All nodes share a single `AgentState` TypedDict — they read from it, mutate it, and return it.

The workflow has **nine nodes**:

```
ingest_figma
    ↓
prune_figma_tree
    ↓
resolve_prompt_instructions   ← NEW: tool-calling research for user prompt
    ↓
build_ir
    ↓
map_to_ds
    ↓
generate_code
    ↓
refine_typography
    ↓
fix_imports
    ↓
validate ─── errors? ──▶ repair ──┐
    │                              │
    └── no errors / max attempts ◀─┘
    ↓
   END
```

Let me walk through each stage."

---

## SECTION 5 — Stage 1: Ingest Figma (`ingest_figma_node`)

> **[Reference `clean_node()` — no LLM]**

"The raw Figma export JSON from the REST API or a plugin is enormous. A 10-screen design might
be 8 MB of JSON. Most of it is redundant.

The ingest node runs a recursive `clean_node()` function over the tree. It does several things:

1. **Skips invisible nodes** — if `visible == false`, the subtree is dropped entirely.
2. **Extracts and normalises layout**: `layoutMode` (VERTICAL/HORIZONTAL), axis alignment,
   `itemSpacing`, individual paddings, `layoutWrap`.
3. **Extracts styling**: fills, strokes, corner radius, effects (shadows), blend mode, opacity.
4. **Extracts text**: `characters` becomes `properties.text`. The full text style
   (fontSize, fontWeight, fontFamily, textAlignHorizontal, lineHeightPx) is preserved.
5. **Captures `componentProperties`**: this is key. Figma INSTANCE nodes carry a
   `componentProperties` object that tells you which variant is applied — for example,
   `{'Color': {'value': 'Primary', 'type': 'VARIANT'}, 'Size': {'value': 'Large', 'type': 'VARIANT'}}`.
   This flows all the way through to the variant resolution system in Stage 4.

The result is typically 40–60% smaller than the raw export, while retaining every piece
of data the downstream stages need.

The cleaned tree is stored in `state['figma_json']`."

---

## SECTION 6 — Stage 1b: Tree Pruning (`prune_figma_tree_node`)

> **[No LLM — pure Python]**

"Before going to the LLM for IR generation, we run a second pass of structural pruning.
This runs up to 5 iterations because some rules create new candidates for other rules.

Four rules:

1. **Tiny/spacer shapes** — any rectangle or ellipse with both dimensions < 4px is dropped.
   These are visual separators the designer drew, not UI elements.

2. **Spacer frame names** — frames whose name matches `\\b(spacer|padding|gap|separator)\\b`
   are dropped. Designers often add invisible padding frames in Figma.

3. **Single-child wrapper collapse** — a frame with exactly one child, no visual styling
   (no fills, no strokes, no effects, no border-radius), and no layout constraints is
   dissolved. Its child takes its place in the tree.

4. **Repeated sibling deduplication** — when a parent has 4 or more structurally identical
   children (same name, same number of children, same styling), keep one representative and
   add a `_repeat_count` annotation. This prevents the IR LLM from receiving 20 copies of
   the same table row.

The pruned tree goes into `state['figma_json']` (replacing the ingested tree)."

---

## SECTION 7 — Stage 1c: Resolve Prompt Instructions (`resolve_prompt_instructions_node`)

> **[NEW — tool-calling LLM, runs before IR]**

"This is a brand-new stage that runs whenever the user provides a text prompt alongside
the Figma JSON.

The core problem it solves: the user says 'use a p-calendar for the date picker section'.
Without this stage, that instruction only reaches the code generation LLM. But the IR stage
is what classifies each Figma node into a semantic type like `input` or `select`, and the
DS mapping stage is what decides which catalog component to use. If those stages don't know
about the prompt, the component might still get mapped incorrectly.

This stage gives the LLM three tools:

```python
@lc_tool
def catalog_lookup(component_name: str) -> str:
    # Searches DS_CATALOG_ENTRY_MAP by name or selector
    # Returns: selector, description, figma_hints, base_classes,
    #          variant_class_map, inner_html_note, doc URLs

@lc_tool
def component_docs_lookup(selector: str) -> str:
    # Fetches API + usage docs via DocScraper (hits disk cache first)
    # Returns: up to 3000 chars of scraped documentation

@lc_tool
def utility_class_lookup(concept: str) -> str:
    # Searches DOC_KNOWLEDGE sections for matching class names
    # Pass: 'flex', 'spacing', 'typography', 'color', etc.
    # Returns: .class-name — description [section/subsection]
```

The LLM runs a tool-calling loop — up to 6 rounds. It calls `catalog_lookup('calendar')`
to confirm `p-calendar` exists and get its selector and hints. It calls `component_docs_lookup`
to fetch the API docs. It calls `utility_class_lookup` if the prompt mentions spacing or color.

At the end it writes a structured summary:

```
## PROMPT INSTRUCTION RESOLUTION
### Components to use:
- p-calendar: use for any date/date-range picker node — key inputs: [(ngModel)], [selectionMode]
### Utility classes:
- .flex — display flex
- .gap-3 — gap: 0.75rem
### Node mapping hints:
- Nodes named 'date', 'DatePicker', 'CalendarField' → use p-calendar
```

This text — `prompt_research_context` — is stored in the LangGraph state and injected
into both the IR system prompt and the code generation system prompt.

Separately, the pipeline extracts a list of confirmed selectors from the research output —
`prompt_component_hints`. These are used in Stage 3 to boost the scoring for user-requested
components by +50 points."

---

## SECTION 8 — Stage 2: Build IR (`build_ir_node`)

> **[LLM: gpt-4o, 1–N calls]**

"The IR — Intermediate Representation — is a semantic annotation of the Figma tree.
The Figma tree knows about visual properties: fills, strokes, font sizes. The IR adds
semantic knowledge: this node is a `button`, that one is a `card`, that cluster is a `table`.

The LLM is given a compact representation of the tree — just the fields needed for semantic
classification: `id`, `name`, `type`, `layout`, `text`, and `componentProperties`.

If the compact JSON is under 100 KB, the entire tree is processed in a single LLM call.
If it's larger, the tree is flattened into individual nodes and split into chunks of 50 nodes.
Each chunk is processed in a separate LLM call, and they run **in parallel** using
`ThreadPoolExecutor` with up to 8 workers. After all chunks complete, the hierarchy is
reconstructed from the `parent_id` relationships.

**When a user prompt was provided**, the system prompt for IR generation includes the
`prompt_research_context` from Stage 1c. So the LLM knows: 'nodes named DatePicker or
Calendar should be typed as `select` so they get scored against `p-calendar` in the next stage'.

The 28 semantic IR types include:
```
container, text, button, input, card, list, toolbar, divider,
chip, badge, tab, menu, dialog, form, select, checkbox, radio,
toggle, slider, progress, stepper, table, nav, header, footer,
link, avatar, form-field, expansion-panel, sidenav, unknown
```

Text nodes (`type == 'text'` in Figma) always become `type: 'text'` in the IR. Their
heading level (h1/h2/p) is classified deterministically in Stage 3 based on `fontSize`
and `fontWeight` — no LLM involved."

---

## SECTION 9 — Stage 3: Map to Design System (`map_to_design_system_node`)

> **[LLM: conditional — Tier 2/3 only]**

"This stage maps every IR node to a DS component selector or a native HTML tag.

### Text node classification — deterministic, zero LLM

Every `type: 'text'` node goes through `classify_figma_text_as_html()`:
```
fontSize ≥ 48, or ≥ 36 with fontWeight > 500  →  h1
fontSize ≥ 32, or ≥ 24 with fontWeight > 500  →  h2
else                                           →  p
```

### Non-text nodes — six-signal scoring

Every other IR node is scored against every catalog entry:

```
Signal                                               Points
─────────────────────────────────────────────────────────────
Exact word-boundary hint match in layer name          +70
Substring hint match in layer name                    +45  (only if no exact hit)
IR semantic type maps to catalog entry name           +25
Figma node type in entry's figma_node_types           +15
Node is a Figma INSTANCE node                         + 5
Any child node name contains a catalog hint           +15
User prompt requested this component (NEW)            +50
─────────────────────────────────────────────────────────────
Maximum (capped)                                      100
```

The +50 prompt boost is new. If the user said 'use p-calendar for the date picker' and the
resolve_prompt_instructions node confirmed p-calendar exists, then every node being scored
against the `p-calendar` catalog entry gets +50 added to its score. That overrides any
competing match.

### Three routing tiers

After scoring every node against every catalog entry:

**Tier 1 (score ≥ 70):** Definite deterministic match. No LLM. The best-scoring entry is
used immediately. The entry's API URL is primed in the doc cache for Phase 1.

**Tier 2 (score 30–69):** Ambiguous. The node plus its top-3 candidates are collected into
a batch. All Tier 2 nodes are resolved in a single LLM call. The LLM sees the full catalog
summary and the node context (name, parent name, children, inner text). If a user prompt was
provided, the LLM sees: `USER PREFERENCE (MANDATORY): prefer p-calendar when it is a reasonable fit`.

**Tier 3 (score 1–29):** Low confidence. Same batch LLM call as Tier 2.

**Score = 0:** Immediate native HTML fallback. No LLM.

In fast mode (`--fast` flag), only Tier 1 runs. Tier 2 and 3 both fall back to native HTML.
This doubles throughput at the cost of DS coverage.

### Output

Each node produces a `DSComponentMapping`:
```python
DSComponentMapping(
    figma_node_id = '1:42',
    ds_component  = 'dropdown',   # catalog entry name, or 'native'
    ds_selector   = 'p-dropdown', # HTML tag to emit
    inputs        = {},
)
```
These mappings are stored in `state['component_mappings']` for Stage 4."

---

## SECTION 10 — Stage 4: Generate Angular Code (`generate_angular_code_node`)

> **[LLM: Phase 1 tool loop + 1 structured call]**

"This is the most complex stage. It has two sub-phases.

### Sub-phase A — Phase 1 Documentation Research

Before generating any code, an LLM tool-calling loop runs to gather the information needed
to write correct, idiomatic code. It only runs when there is actual work to do — either
the knowledge JSON is loaded, or DS components were matched in Stage 3.

Three tools are available:

```
search_doc_knowledge(query, section)
  Searches the pre-built knowledge.json
  → Returns matching utility class groups with names, descriptions, examples

fetch_component_docs(component, doc_type)
  Looks up a component in DS_CATALOG_ENTRY_MAP, fetches its api/usage/overview URL
  via DocScraper (disk cache first, then live)
  → Returns up to 8,000 chars of scraped text

fetch_doc_page(url)
  Fetches any arbitrary URL via DocScraper
  → Returns up to 8,000 chars
```

The loop runs up to 10 iterations. The LLM decides when it has enough context.
A typical run for a form with 3 DS components makes 4–6 tool calls: one to fetch the
utility class knowledge for layout and spacing, then one API doc call per component.

The output is a plain text summary injected into the code generation system prompt:

```
## PrimeNG DOCUMENTATION RESEARCH (Phase 1 findings)

## Utility Classes
  flex, flex-column, flex-row, gap-3, p-4, ...

## Component APIs
  ### p-inputText
  @Input() pInputText — applies PrimeNG input styling to native input
  ...
  ### p-dropdown
  @Input() options: any[]
  @Input() optionLabel: string
  @Input() placeholder: string
  @Output() onChange: EventEmitter
  ...
```

This `phase1_research_context` string is also stored in the session (via `pipeline_meta`)
so that subsequent **refine** turns can reuse it without running Phase 1 again.

### Sub-phase B — Component Hierarchy Context

Before calling the code generation LLM, `_build_component_hierarchy_context()` annotates
every IR node with four pre-computed fields from the catalog:

```json
{
  "id": "1:42",
  "type": "button",
  "ds_component": "button",
  "ds_selector": "p-button",
  "resolved_classes":    ["p-button", "p-button-primary"],
  "resolved_directives": [],
  "inner_html_note": "Use [label] input for button text; no inner tags."
}
```

`resolved_classes` comes from the variant resolution system — let me explain that briefly.

### Variant Resolution

The pipeline extracts variant tokens from three sources and matches them against
the catalog's `variant_class_map`:

**Source 1:** `componentProperties` captured from the Figma INSTANCE node — for example,
`{'Color': 'Primary', 'Size': 'Large'}` → tokens `['primary', 'large']`.

**Source 2:** `componentProperties` from the raw Figma JSON (fallback if the IR LLM drops it).

**Source 3:** The Figma layer name, split by `/`, `-`, `_`, spaces — so
`Button / Primary / Large` → tokens `['button', 'primary', 'large']`.

These tokens are matched against `variant_class_map` keys in three tiers:

1. **Exact match:** token `==` key → use that key's classes.
2. **Word-boundary match:** token appears as a whole word inside the key.
3. **Substring match:** token is contained in key or key in token (length ≥ 4 chars only,
   to guard against false positives).

Result: `resolved_classes = base_classes + matched_variant_classes`.

For `Button / Primary / Large` with `base_classes: ['p-button']` and
`variant_class_map: {'primary': ['p-button-primary'], 'large': ['p-button-lg']}`:
```
resolved_classes = ['p-button', 'p-button-primary', 'p-button-lg']
```

The code generation LLM is told to treat these as **authoritative** — use them exactly,
do not guess or add extra classes.

### Structured Code Generation

The system prompt for the code generation LLM includes, in order:

1. **Expert Angular developer framing**
2. **USER INSTRUCTIONS section** (if a user prompt was provided — highest priority)
3. **DS enforcement rules** — shared builder `build_ds_enforcement_system_prompt()`:
   - Complete whitelist of allowed custom element selectors (built from catalog)
   - FORBIDDEN patterns: `style='...'`, inline typography, non-whitelisted DS tags
   - SCSS rules: layout-level only, typography via utility classes
   - Intent-specific overrides (for refine turns)
4. **Component mapping guide** — built from catalog: which selectors to use and when
5. **Phase 1 research context** — utility classes and component API documentation
6. **Text node rules** — always native h1/h2/p, never DS components
7. **Design structure** — the full IR tree with computed CSS values
8. **Component hierarchy** — every node annotated with `resolved_classes`, `ds_selector`, `inner_html_note`
9. **Screenshot styling** (if screenshot provided) — GPT-4 Vision analysis with exact hex colors,
   pixel spacing, border-radius values

The LLM output is a Pydantic `GeneratedAngularArtifact` using structured output:

```python
class GeneratedAngularArtifact(BaseModel):
    component_name:    str
    files:             List[GeneratedFile]  # .ts, .html, .scss
    ds_components_used: List[DSComponentMapping]
    imports:           List[str]
    unresolved_nodes:  List[dict]
```

Structured output means the LLM response is parsed directly into this Pydantic model —
no regex parsing, no error-prone JSON extraction."

---

## SECTION 11 — Stages 5 & 6: Validate and Repair

> **[Validate: no LLM. Repair: 1 structured-output LLM call. Conditional.]**

"After code generation, `validate_node` runs a set of heuristic checks with no LLM involved:

1. The artifact exists and has at least one file.
2. Every DS component selector in `ds_components_used` is either in the catalog or is a
   standard HTML element.
3. DS usage ratio: if the template has more than 5 mappable elements but fewer than 2
   DS component uses, a `low_ds_usage` error is raised.

If errors are found and `repair_attempt < 2`, the `should_repair()` function routes to
the `repair_node`.

The repair node calls the LLM with:
- A `SystemMessage` containing the full DS enforcement prompt (same shared builder used
  in codegen — this is Enhancement G from the DS-aware plan)
- A `HumanMessage` with the error list, all 20 component mappings, and all generated files

The LLM produces a corrected `GeneratedAngularArtifact` that replaces the current one,
and the workflow loops back to `validate_node`.

Maximum 2 repair attempts. After that the artifact is accepted even with validation errors,
and `unresolved_nodes` carries forward the issues for the user to see."

---

## SECTION 12 — DS-Aware Enhancements (REST API Layer)

> **[Open `api.py`]**

"The REST API adds a layer of intelligence on top of the pipeline for multi-turn conversations.
Let me walk through the key enhancements.

### Enhancement I — Unified Entry Point

`generate_angular_component()` is the single function all generate paths go through.
It accepts any combination of `figma_json`, `screenshot_path`, and `prompt`:

- **figma_json only:** runs the pipeline directly on that tree
- **prompt only:** calls `generate_html_from_input()` to synthesise HTML from the prompt,
  converts it to a Figma-like tree with `html_to_figma_tree()`, then runs the pipeline
- **screenshot only:** GPT-4 Vision analyses the screenshot → HTML → Figma tree → pipeline
- **figma_json + prompt:** runs pipeline on figma_json, but prompt is now threaded through
  the entire pipeline as user instructions — this is the behaviour we just added

The prompt always flows into `run_figma_to_angular()` as `user_prompt`, populates
`initial_state['user_prompt']`, and triggers the `resolve_prompt_instructions` node before IR.

### Enhancement F — Session State

The `Session` object in `session_store.py` carries the full context across turns:

```python
@dataclass
class Session:
    session_id:             str
    design_system:          str
    created_at:             datetime
    last_active:            datetime
    current_artifact:       Optional[GeneratedAngularArtifact]
    chat_history:           List[dict]
    component_mappings:     List[dict]
    # New fields:
    doc_research_cache:     Dict[str, str]    # url → text; avoids re-fetching docs
    ds_coverage_history:    List[dict]        # one coverage score per turn
    change_log:             List[dict]        # what changed each turn
    pending_suggestion:     Optional[dict]    # awaiting component confirmation
    pending_unresolved:     List[dict]        # unresolved nodes awaiting clarification
    phase1_research_context: Optional[str]   # reused in every subsequent refine turn
    last_intent:            Optional[dict]
```

`phase1_research_context` is the critical one. The generation-time Phase 1 research
is stored in the session, and every refine turn injects it back in as
`## GENERATION-TIME RESEARCH CONTEXT` — so the refine LLM knows what utility classes
and component APIs were found during the original generation, without re-running Phase 1.

### Enhancement H — Conversation Router

Before any refine prompt reaches the LLM, `route_chat_message()` inspects it:

```python
@dataclass
class ChatRouteDecision:
    action: str  # APPLY_REFINE | CLARIFY | RESOLVE_UNRESOLVED | OUT_OF_SCOPE

def route_chat_message(prompt, session, catalog) -> ChatRouteDecision:
    # 1. Unresolved nodes pending? → RESOLVE_UNRESOLVED
    # 2. Out-of-scope regex match?  → OUT_OF_SCOPE
    #    Pattern: NgModule, routing, HttpClient, backend service,
    #             database, authentication, REST endpoint
    # 3. Intent classified as AMBIGUOUS with confidence < 0.5? → CLARIFY
    # 4. Default → APPLY_REFINE
```

`OUT_OF_SCOPE` returns immediately without calling the LLM — no tokens consumed.
`CLARIFY` returns a clarification question without calling the LLM.

This is important for cost control as well as UX. You don't want to burn an LLM call on
'add routing logic to my component' and get back Angular Router code.

### Enhancement A — Intent Classification

For every refine prompt that reaches `APPLY_REFINE`, `classify_refine_intent()` is called:

```python
@dataclass
class IntentClassification:
    category: str          # one of 6 categories
    new_components_requested: List[str]
    affected_selectors:    List[str]
    requires_catalog_lookup: bool
    requires_doc_research:   bool
    confidence:            float
```

Six categories:
- `DATA_LOGIC_BEHAVIOR` — method, event, output, emit, state → skip catalog lookup; touch TS only
- `ACCESSIBILITY_PROPERTY` — aria, a11y, role, tabindex → partial catalog + a11y docs
- `VISUAL_STYLE` — color, background, shadow, padding → fetch utility class docs
- `LAYOUT_STRUCTURAL` — align, flex, grid, margin → layout utility classes
- `COMPONENT_SWAP` — add/replace + component term → full catalog query + doc research
- `AMBIGUOUS` — LLM fallback classification

Heuristic regex patterns are checked first (5 compiled patterns, confidence = 0.8).
If nothing matches, a cheap LLM call with temperature=0 classifies it.

The intent category controls what context gets fetched for the refine LLM call.

### Enhancement B — Catalog Lookup and Doc Research in Refine

When `intent.requires_catalog_lookup == True`, `query_catalog_for_intent()` runs:

1. Extracts component terms from the prompt
2. Scores them against the catalog using the same `_score_catalog_match` logic
3. Returns the top-3 matches with score > 30
4. For each match, checks `session.doc_research_cache`. If not cached, fetches API + usage
   docs via `DocScraper` and caches them in the session.

The fetched docs are injected into the refine system prompt under
`## RELEVANT COMPONENT DOCUMENTATION`.

### Enhancement C — Proactive Component Suggestions

When `query_catalog_for_intent()` returns multiple close matches, `build_component_suggestion_response()`
creates a text block like:

```
I found these PrimeNG components that match your request:
1. p-dropdown — Single-value select with search, filtering, and templating
2. p-listbox  — Scrollable list with single/multi selection
3. p-select   — Simple select component

Proceeding with p-dropdown (best match, score 85).
```

High confidence (score > 70, unique top match): proceed immediately, prepend suggestion
as informational message in chat history.

Low confidence (score 30–70, multiple close matches): return suggestion and mark
`pending_suggestion` — the next user message selects which component to use.

### Enhancement D — Unresolved Node Follow-up

After every generate or refine call, if the artifact has `unresolved_nodes` (nodes that
couldn't be confidently mapped), the API appends a structured notice to `chat_history`:

```json
{
  "role": "assistant",
  "content": "Generation complete. However, 2 element(s) could not be confidently mapped to a PrimeNG component:\n- DateRangePicker\n- CustomSlider\n\nDescribe what each should be and I will re-map them.",
  "metadata": {
    "type": "unresolved_notice",
    "unresolved_nodes": [...]
  }
}
```

This sets `session.pending_unresolved`. The next user message is automatically routed to
`RESOLVE_UNRESOLVED` by the conversation router.

### Enhancement E — DS Coverage Scoring

After every generate and refine call, `compute_ds_coverage()` parses the generated HTML
using Python's stdlib `html.parser` and counts:

- **Total mappable elements:** interactive/semantic tags (`button`, `select`, `input`,
  `textarea`, `table`, `a`) + any catalog selector found in the HTML
- **DS-mapped elements:** tags that match a catalog selector
- **Coverage percentage:** `ds_mapped / total * 100`
- **Uncovered selectors:** native tags used where a DS equivalent exists

```json
{
  "total_mappable_elements": 8,
  "ds_mapped_elements": 7,
  "coverage_pct": 87.5,
  "uncovered_selectors": ["button"]
}
```

This is included in every API response and accumulated in `session.ds_coverage_history`.
The frontend can show a coverage meter that updates after each refine turn."

---

## SECTION 13 — Running the CLI

> **[Live demo or narrate with terminal output]**

```bash
# Verify catalog exists
ls design_systems/primeng_catalog.json

# Basic run — Figma JSON only
python run_agent.py primeng

# With a screenshot for visual styling
python run_agent.py primeng my_design.png

# Fast mode — skip Tier 2/3 LLM mapping, 2x IR chunk size
python run_agent.py primeng --fast
```

"After the run completes, `output/generated/` contains:

```
output/generated/
├── WelcomePageComponent.component.ts    ← Angular standalone class + imports
├── WelcomePageComponent.component.html  ← template with p-* selectors + utility classes
├── WelcomePageComponent.component.scss  ← only truly bespoke styles
├── metadata.json                        ← component map + DS component list
└── pipeline_log.txt                     ← step timings + LLM call counts + full trace
```

A typical run for a 37-node design with PrimeNG:
- Stage 1 (ingest + prune): instant, pure Python
- Stage 1c (resolve prompt): 1 LLM tool-calling loop, ~3–6 seconds (if prompt provided)
- Stage 2 (build IR): 1 LLM call, ~8–12 seconds
- Stage 3 (map to DS): 0 LLM calls if all Tier 1, up to 2 batch calls if not
- Stage 4 (generate code): Phase 1 tool loop ~15 seconds + 1 structured call ~10 seconds
- Total: approximately 35–50 seconds end to end

The `pipeline_log.txt` shows exact timings per step and LLM call counts."

---

## SECTION 14 — REST API Demo

> **[Terminal with server running or walk through the curl sequence]**

```bash
# Start the server
uvicorn api:app --reload --port 8000
```

"The server starts a background cleanup thread that runs every 5 minutes and purges sessions
inactive for more than 60 minutes. Sessions are stored in a thread-safe in-memory dict.

Let me walk through the full API lifecycle."

### Step 1 — Discover design systems

```bash
curl http://localhost:8000/design-systems
# → {"design_systems": ["primeng", "myds"]}
```

"This scans `design_systems/` for `*_catalog.json` files. No state created."

### Step 2 — Create a session

```bash
SESSION_ID=$(curl -s -X POST http://localhost:8000/sessions \
  -H "Content-Type: application/json" \
  -d '{"design_system": "primeng"}' \
  | python3 -c "import sys,json; print(json.load(sys.stdin)['session_id'])")
echo "Session: $SESSION_ID"
```

"The session validates that `primeng` matches a catalog file. If you pass an unknown
design system name, you get a 400 immediately.
The session is initialised with 13 fields all set to their zero values."

### Step 3 — Generate from Figma JSON

```bash
curl -X POST http://localhost:8000/sessions/$SESSION_ID/generate \
  -F "figma_json=@figma_tree.json"
```

"The file is read server-side, parsed as JSON, and passed to `generate_angular_component()`.
The full 9-stage pipeline runs. The response includes the generated files, DS coverage score,
unresolved nodes, and chat history."

### Step 4 — Generate with prompt instructions

```bash
curl -X POST http://localhost:8000/sessions/$SESSION_ID/generate \
  -F "figma_json=@figma_tree.json" \
  -F "prompt=Use p-calendar for the date section and p-inputNumber for any numeric fields"
```

"Now the prompt flows into the pipeline. Before IR generation, the resolve_prompt_instructions
node calls `catalog_lookup('p-calendar')` and `catalog_lookup('p-inputNumber')`, confirms both
exist, fetches their API docs, and writes a structured resolution summary.

In Stage 3, when scoring every IR node against every catalog entry, nodes scored against
`p-calendar` get +50. So 'DateRangePicker' which might have scored 45 against p-calendar
(substring match on 'calendar') now scores 95 — confidently Tier 1.

In Stage 4, the code gen system prompt includes `## USER INSTRUCTIONS` at the top with the
full prompt text, plus the `## PROMPT INSTRUCTION RESOLUTION` section from the research node."

### Step 5 — Refine

```bash
# Component swap — requires catalog lookup
curl -X POST http://localhost:8000/sessions/$SESSION_ID/refine \
  -H "Content-Type: application/json" \
  -d '{"prompt": "Replace the plain select with a searchable p-dropdown"}'
```

"The router classifies this as `COMPONENT_SWAP`. `query_catalog_for_intent()` scores
'dropdown' against the catalog, returns `p-dropdown` as top match. API docs are fetched
and cached. The refine LLM gets a system prompt with:
- DS enforcement block (selector whitelist, forbidden patterns)
- Generation-time Phase 1 context (reused from session)
- Relevant component documentation for p-dropdown"

```bash
# Logic change — skips catalog lookup, touches TypeScript only
curl -X POST http://localhost:8000/sessions/$SESSION_ID/refine \
  -H "Content-Type: application/json" \
  -d '{"prompt": "Emit a formSubmit output event when the submit button is clicked"}'
```

"Intent: `DATA_LOGIC_BEHAVIOR`. No catalog lookup. The enforcement prompt includes:
`DATA_LOGIC_BEHAVIOR: only modify TypeScript; do not touch HTML or SCSS.`"

```bash
# Out-of-scope — returns immediately, no LLM call
curl -X POST http://localhost:8000/sessions/$SESSION_ID/refine \
  -H "Content-Type: application/json" \
  -d '{"prompt": "Connect the form to a backend API using HttpClient"}'
```

"The out-of-scope regex fires on `HttpClient`. The router returns immediately with:
`This tool generates Angular component UI only. Backend services, routing, NgModule
configuration, and API integration are out of scope.` Zero LLM tokens consumed."

### Step 6 — Inspect session state

```bash
curl http://localhost:8000/sessions/$SESSION_ID
```

"The response shows:
- `chat_history` — every user message and assistant response, including component
  suggestion notices (`metadata.type = 'component_suggestion'`) and unresolved node
  notices (`metadata.type = 'unresolved_notice'`)
- `ds_coverage_history` — one coverage object per generate/refine turn, showing
  how DS coverage evolved over the conversation
- `current_files` — the latest generated .ts, .html, .scss content"

### Step 7 — Delete the session

```bash
curl -X DELETE http://localhost:8000/sessions/$SESSION_ID
# → 204 No Content
```

---

## SECTION 15 — Architecture Summary

> **[Close with the data flow diagram]**

"Let me summarise the complete data flow.

```
User provides:  figma_tree.json  +  primeng_catalog.json  +  optional prompt

                         ↓
generate_angular_component()         ← unified entry point
  │
  │  if no figma_json:
  │    generate_html_from_input()    ← GPT-4 Vision or text → HTML
  │    html_to_figma_tree()          ← synthetic Figma tree
  │
  └──▶ run_figma_to_angular(figma_json, user_prompt=prompt)
         │
         │  Loads catalog → populates module globals
         │  DS_CATALOG_ENTRY_MAP  (name + selector → entry)
         │  DOC_KNOWLEDGE         (utility classes from knowledge.json)
         │  DS_CATALOG_ENTRY_MAP  (for tool-based lookup)
         │
         └──▶ LangGraph workflow:
               1. ingest_figma                  [no LLM]
               2. prune_figma_tree              [no LLM]
               3. resolve_prompt_instructions   [tool LLM — if prompt]
               4. build_ir                      [LLM: 1–N calls, parallel chunks]
               5. map_to_ds                     [LLM: conditional Tier 2/3 batch]
               6. generate_code                 [LLM: Phase1 tool loop + structured]
               7. refine_typography             [LLM: inline style → utility class]
               8. fix_imports                   [LLM: missing import correction]
               9. validate                      [no LLM]
              10. repair (conditional, ≤2x)     [LLM: structured output]

Returns: (GeneratedAngularArtifact, final_state)

generate_angular_component() wraps this and returns:
  artifact         → .ts / .html / .scss files + metadata
  pipeline_meta    → phase1_research_context + ds_coverage
```

Key properties of this design:

1. **Fully generic** — no PrimeNG-specific code anywhere in Python.
   Swap the catalog file, get a different design system.

2. **Deterministic where possible** — text node classification, tree pruning, Tier 1 mapping,
   variant resolution, component hierarchy context — all pure Python, zero LLM.

3. **LLM only for semantic judgement** — IR classification, ambiguous node resolution,
   documentation research, code generation. Everything else is deterministic.

4. **Prompt propagates through the full pipeline** — the user instruction reaches the
   pre-IR research node (tool calling), the IR system prompt, the DS mapping score (Tier 1
   boost + Tier 2/3 preference injection), and the code gen system prompt (highest priority block).

5. **Shared enforcement** — `build_ds_enforcement_system_prompt()` produces the same selector
   whitelist and SCSS rules for initial generation, refine, and repair. No path can bypass it.

6. **Session continuity** — Phase 1 research context, doc research cache, and DS coverage
   history persist across multi-turn conversations so later refine calls are fast and coherent."

---

## SECTION 16 — Q&A Prompts

> **[Anticipate common questions]**

**Q: How do you add a new design system?**

"Copy `template_catalog.json`, fill in name/prefix/import_path and your component list,
run `generate_figma_hints.py`, `scrape_ds_docs.py` (optional but recommended), and
`doc_knowledge_builder.py`. That's it. The pipeline is immediately usable."

**Q: What if the LLM hallucinates a non-existent tag?**

"The system prompt contains a hard whitelist: `The ONLY custom element tags you may use are:
<p-button> <p-dropdown> <p-calendar> ...`. Any tag not in that list is forbidden.
The same whitelist is in the shared enforcement prompt used during repair. If the initial
generation hallucinates a tag, the validate step catches it and triggers repair with the
whitelist prominently in the system prompt."

**Q: What happens when a Figma node can't be mapped to any DS component?**

"It goes to `unresolved_nodes` in the artifact, and the API surfaces it in the chat as
an `unresolved_notice` message. The user can reply with clarification, which routes to
`RESOLVE_UNRESOLVED` and triggers a targeted refine that focuses only on those nodes."

**Q: Can it handle very large Figma designs?**

"Yes. The tree pruning stage removes structural noise. If the compact tree exceeds 100 KB,
the IR generation uses parallel chunking — 50 nodes per chunk, up to 8 concurrent workers.
Fast mode doubles the chunk size to 100 and skips Tier 2/3 LLM mapping, which roughly
halves total runtime for large designs."

**Q: How accurate is the DS coverage score?**

"It's a heuristic, not ground truth. It counts DS-selector tags vs mappable native tags
in the generated HTML using stdlib `html.parser`. 'Mappable' means tags that typically
have a DS equivalent: button, select, input, textarea, table, a, plus any catalog selector.
Structural divs and spans are not counted. It's useful for tracking regression across
refine turns, not as a hard quality gate."

---

*End of demo script.*
