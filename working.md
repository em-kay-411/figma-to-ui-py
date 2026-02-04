# Figma-to-Angular Code Generation System - Complete Architecture

## High-Level Architecture Diagram

```
┌─────────────────────────────────────────────────────────────────────────────────┐
│                              INPUTS                                              │
├─────────────────┬─────────────────┬─────────────────┬───────────────────────────┤
│   Figma JSON    │  Design System  │  Design Tokens  │    Screenshots (opt)      │
│   (figma_tree)  │   (Compodoc)    │    (optional)   │    (thumbnailUrl)         │
└────────┬────────┴────────┬────────┴────────┬────────┴──────────┬────────────────┘
         │                 │                 │                   │
         └─────────────────┴─────────────────┴───────────────────┘
                                    │
                                    ▼
┌─────────────────────────────────────────────────────────────────────────────────┐
│                         LANGGRAPH WORKFLOW ENGINE                                │
│  ┌─────────────────────────────────────────────────────────────────────────┐   │
│  │                           AgentState                                     │   │
│  │  • figma_json          • ir_tree              • validation_errors       │   │
│  │  • original_figma_json • component_mappings   • repair_attempt          │   │
│  │  • ds_catalog          • generated            • messages                │   │
│  │  • design_tokens       • figma_screenshots                              │   │
│  └─────────────────────────────────────────────────────────────────────────┘   │
│                                    │                                            │
│  ┌─────────────┐    ┌──────────┐   │   ┌───────────┐    ┌──────────────────┐   │
│  │ 1. INGEST   │───▶│ 2. BUILD │───┴──▶│ 3. MAP TO │───▶│ 4. GENERATE CODE │   │
│  │   FIGMA     │    │    IR    │       │    DS     │    │                  │   │
│  └─────────────┘    └──────────┘       └───────────┘    └────────┬─────────┘   │
│                                                                   │             │
│                        ┌──────────────────────────────────────────┘             │
│                        │                                                        │
│                        ▼                                                        │
│               ┌────────────────┐         ┌────────────┐                        │
│               │  5. VALIDATE   │────────▶│ 6. REPAIR  │◀──┐                    │
│               └────────┬───────┘         └─────┬──────┘   │                    │
│                        │                       │          │ (max 2 attempts)   │
│                        │    ┌──────────────────┘          │                    │
│                        ▼    ▼                             │                    │
│               ┌────────────────────┐                      │                    │
│               │  should_repair()?  │──── errors? ────────▶┘                    │
│               └────────┬───────────┘                                           │
│                        │ no errors / max attempts                              │
│                        ▼                                                        │
│                     [END]                                                       │
└────────────────────────┬────────────────────────────────────────────────────────┘
                         │
                         ▼
┌─────────────────────────────────────────────────────────────────────────────────┐
│                              OUTPUTS                                             │
├─────────────────────┬─────────────────────┬─────────────────────────────────────┤
│  component.ts       │  component.html     │  component.scss                     │
│  (TypeScript)       │  (Angular Template) │  (Styles)                           │
└─────────────────────┴─────────────────────┴─────────────────────────────────────┘
```

---

## Detailed Node Processing Pipeline

```
┌────────────────────────────────────────────────────────────────────────────────┐
│ NODE 1: INGEST_FIGMA (Lines 378-560)                                           │
├────────────────────────────────────────────────────────────────────────────────┤
│ INPUT: Raw Figma JSON (full export)                                            │
│                                                                                │
│ PROCESSING:                                                                    │
│ ┌─────────────────────────────────────────────────────────────────────────┐   │
│ │  clean_node() - Recursive cleaning                                       │   │
│ │  ├── Skip invisible nodes (visible == false)                            │   │
│ │  ├── Extract layout: layoutMode, alignItems, padding, itemSpacing       │   │
│ │  ├── Extract position: absoluteBoundingBox, constraints                 │   │
│ │  ├── Extract styling: fills, strokes, cornerRadius, effects, opacity    │   │
│ │  ├── Extract text: characters, style, fontFamily, fontSize              │   │
│ │  └── Extract interactions: interactions, boundVariables                 │   │
│ └─────────────────────────────────────────────────────────────────────────┘   │
│                                                                                │
│ OUTPUT: Cleaned Figma JSON + Metadata (components, styles, componentSets)     │
└────────────────────────────────────────────────────────────────────────────────┘
                                        │
                                        ▼
┌────────────────────────────────────────────────────────────────────────────────┐
│ NODE 2: BUILD_IR (Lines 656-687)                                               │
├────────────────────────────────────────────────────────────────────────────────┤
│ INPUT: Cleaned Figma JSON                                                      │
│                                                                                │
│ PROCESSING (Size-based routing):                                              │
│ ┌─────────────────────────────────────────────────────────────────────────┐   │
│ │  if JSON size ≤ 100KB:                                                   │   │
│ │      └── _process_single_tree() - One LLM call                          │   │
│ │  else:                                                                   │   │
│ │      ├── _flatten_figma_tree() - Flatten hierarchy                      │   │
│ │      ├── Split into chunks (50 nodes per chunk)                         │   │
│ │      ├── Process chunks in parallel with LLM                            │   │
│ │      └── _reconstruct_ir_hierarchy() - Rebuild tree                     │   │
│ └─────────────────────────────────────────────────────────────────────────┘   │
│                                                                                │
│ LLM TASK: Identify semantic type for each node                                │
│ ┌─────────────────────────────────────────────────────────────────────────┐   │
│ │  SEMANTIC TYPES (IRNodeType Enum):                                       │   │
│ │  button, text, input, card, icon, image, container, list,               │   │
│ │  header, footer, nav, form, divider, avatar, badge, chip,               │   │
│ │  dialog, menu, tab, table, link, unknown                                │   │
│ └─────────────────────────────────────────────────────────────────────────┘   │
│                                                                                │
│ OUTPUT: List[IRNode] - Semantic intermediate representation tree              │
└────────────────────────────────────────────────────────────────────────────────┘
                                        │
                                        ▼
┌────────────────────────────────────────────────────────────────────────────────┐
│ NODE 3: MAP_TO_DESIGN_SYSTEM (Lines 922-1069)                                  │
├────────────────────────────────────────────────────────────────────────────────┤
│ INPUT: IR Tree + Design System Catalog                                         │
│                                                                                │
│ TOOLS AVAILABLE TO LLM:                                                        │
│ ┌─────────────────────────────────────────────────────────────────────────┐   │
│ │  @tool get_component_by_intent(intent: str)                             │   │
│ │       └── Find DS components by UI intent (button, card, input, etc.)   │   │
│ │                                                                          │   │
│ │  @tool get_component_api(selector: str)                                 │   │
│ │       └── Get detailed inputs/outputs for specific component            │   │
│ │                                                                          │   │
│ │  @tool get_design_token(token_path: str)                                │   │
│ │       └── Retrieve token values (color.primary.500 → "#3f51b5")         │   │
│ │                                                                          │   │
│ │  @tool search_components(keyword: str)                                  │   │
│ │       └── Full-text search in design system                             │   │
│ └─────────────────────────────────────────────────────────────────────────┘   │
│                                                                                │
│ PROCESSING:                                                                    │
│ ├── Flatten IR tree for batch processing                                      │
│ ├── Chunk into 50 nodes per batch                                             │
│ ├── LLM tool-calling loop (up to 10 iterations per chunk)                     │
│ └── Parse JSON mappings from responses                                        │
│                                                                                │
│ OUTPUT: List[DSComponentMapping]                                               │
│ ┌─────────────────────────────────────────────────────────────────────────┐   │
│ │  DSComponentMapping:                                                     │   │
│ │  {                                                                       │   │
│ │    figma_node_id: "1:2412",                                             │   │
│ │    ds_component: "MatButton",                                           │   │
│ │    ds_selector: "button[mat-raised-button]",                            │   │
│ │    inputs: {"color": "primary", "disabled": "false"},                   │   │
│ │    outputs: {"click": "onClick()"},                                     │   │
│ │    children_slot: null                                                  │   │
│ │  }                                                                       │   │
│ └─────────────────────────────────────────────────────────────────────────┘   │
└────────────────────────────────────────────────────────────────────────────────┘
                                        │
                                        ▼
┌────────────────────────────────────────────────────────────────────────────────┐
│ NODE 4: GENERATE_ANGULAR_CODE (Lines 1292-1476)                                │
├────────────────────────────────────────────────────────────────────────────────┤
│ INPUT: IR Tree + Mappings + Design Structure                                   │
│                                                                                │
│ PROCESSING STEPS:                                                              │
│ ┌─────────────────────────────────────────────────────────────────────────┐   │
│ │  1. COMPONENT NAME GENERATION (Lines 1318-1329)                         │   │
│ │     └── Figma node name → PascalCase + "Component"                      │   │
│ │         Example: "welcome-screen" → "WelcomeScreenComponent"            │   │
│ │                                                                          │   │
│ │  2. DESIGN STRUCTURE EXTRACTION (Lines 1071-1245)                       │   │
│ │     └── _build_design_structure_for_codegen()                           │   │
│ │         ├── Depth limit: 8 levels max                                   │   │
│ │         ├── Extract: name, type, layout, text, dimensions               │   │
│ │         ├── Convert RGB → CSS colors                                    │   │
│ │         └── Extract effects (shadows, borders, transforms)              │   │
│ │                                                                          │   │
│ │  3. OPTIONAL SCREENSHOT ANALYSIS (Lines 90-176)                         │   │
│ │     └── analyze_screenshot_for_styling() via GPT-4 Vision               │   │
│ │         ├── Extract exact hex colors                                    │   │
│ │         ├── Estimate padding/margins/gaps (px)                          │   │
│ │         ├── Identify typography properties                              │   │
│ │         └── Detect visual effects                                       │   │
│ │                                                                          │   │
│ │  4. LLM CODE GENERATION (Lines 1351-1461)                               │   │
│ │     └── Structured output with GeneratedAngularArtifact schema          │   │
│ └─────────────────────────────────────────────────────────────────────────┘   │
│                                                                                │
│ OUTPUT: GeneratedAngularArtifact                                               │
│ ┌─────────────────────────────────────────────────────────────────────────┐   │
│ │  Files Generated:                                                        │   │
│ │  ├── {name}.component.ts    (TypeScript class, imports, decorators)     │   │
│ │  ├── {name}.component.html  (Angular template with Material components) │   │
│ │  └── {name}.component.scss  (Flexbox utils, colors, typography, effects)│   │
│ └─────────────────────────────────────────────────────────────────────────┘   │
└────────────────────────────────────────────────────────────────────────────────┘
                                        │
                                        ▼
┌────────────────────────────────────────────────────────────────────────────────┐
│ NODE 5: VALIDATE (Lines 1478-1527)                                             │
├────────────────────────────────────────────────────────────────────────────────┤
│ INPUT: Generated Artifact                                                      │
│                                                                                │
│ CHECKS PERFORMED:                                                              │
│ ├── Generated code exists (non-blocking error if missing)                     │
│ ├── Files list not empty (warning if empty)                                   │
│ └── Selector validation (valid Angular Material or HTML)                      │
│                                                                                │
│ OUTPUT: List[ValidationError]                                                  │
└────────────────────────────────────────────────────────────────────────────────┘
                                        │
                                        ▼
┌────────────────────────────────────────────────────────────────────────────────┐
│ NODE 6: REPAIR (Lines 1529-1580) - Conditional, max 2 attempts                 │
├────────────────────────────────────────────────────────────────────────────────┤
│ INPUT: Validation Errors + Component Mappings + IR Tree                        │
│                                                                                │
│ PROCESSING:                                                                    │
│ ├── Increment repair_attempt counter                                          │
│ ├── Summarize errors for LLM context                                          │
│ ├── Call LLM with structured output schema                                    │
│ └── Return repaired artifact for re-validation                                │
│                                                                                │
│ EXIT CONDITION: repair_attempt >= 2 OR no errors                              │
└────────────────────────────────────────────────────────────────────────────────┘
```

---

## Data Structures Reference

```
┌────────────────────────────────────────────────────────────────────────────────┐
│                              DATA MODELS                                        │
├────────────────────────────────────────────────────────────────────────────────┤
│                                                                                │
│  AgentState (TypedDict) - Lines 242-253                                        │
│  ┌──────────────────────────────────────────────────────────────────────────┐ │
│  │  figma_json: Dict[str, Any]              # Cleaned Figma tree            │ │
│  │  original_figma_json: Optional[Dict]      # Original with thumbnailUrl   │ │
│  │  ds_catalog: Dict[str, Any]               # Parsed design system         │ │
│  │  design_tokens: Optional[Dict]            # Token mappings               │ │
│  │  figma_screenshots: Optional[Dict[str,str]] # Screenshot URLs            │ │
│  │  ir_tree: Optional[List[IRNode]]          # Intermediate representation  │ │
│  │  component_mappings: Optional[List[DSComponentMapping]]                  │ │
│  │  generated: Optional[GeneratedAngularArtifact]                           │ │
│  │  validation_errors: List[ValidationError]                                │ │
│  │  repair_attempt: int                      # Counter (0-2)                │ │
│  │  messages: List[Any]                      # Message history              │ │
│  └──────────────────────────────────────────────────────────────────────────┘ │
│                                                                                │
│  IRNode (dataclass) - Lines 200-209                                            │
│  ┌──────────────────────────────────────────────────────────────────────────┐ │
│  │  id: str                    # Figma node ID                              │ │
│  │  type: IRNodeType           # Semantic type (BUTTON, TEXT, CARD, etc.)   │ │
│  │  name: str                  # Display name                               │ │
│  │  layout: LayoutType         # FLEX_ROW, FLEX_COLUMN, GRID, ABSOLUTE      │ │
│  │  properties: Dict[str, Any] # Node-specific data                         │ │
│  │  children: List[IRNode]     # Child nodes (recursive)                    │ │
│  │  constraints: Dict          # Figma constraints                          │ │
│  │  styling: Dict              # CSS properties                             │ │
│  └──────────────────────────────────────────────────────────────────────────┘ │
│                                                                                │
│  IRNodeType (Enum) - Lines 189-198                                             │
│  ┌──────────────────────────────────────────────────────────────────────────┐ │
│  │  CONTAINER, TEXT, BUTTON, INPUT, IMAGE, ICON, CARD, LIST, UNKNOWN        │ │
│  └──────────────────────────────────────────────────────────────────────────┘ │
│                                                                                │
│  LayoutType (Enum) - Lines 182-187                                             │
│  ┌──────────────────────────────────────────────────────────────────────────┐ │
│  │  FLEX_ROW, FLEX_COLUMN, GRID, ABSOLUTE, STACK                            │ │
│  └──────────────────────────────────────────────────────────────────────────┘ │
│                                                                                │
│  DSComponentMapping (Pydantic) - Lines 216-222                                 │
│  ┌──────────────────────────────────────────────────────────────────────────┐ │
│  │  figma_node_id: str         # Which Figma node                           │ │
│  │  ds_component: str          # Component class (MatButton)                │ │
│  │  ds_selector: str           # CSS/Angular selector                       │ │
│  │  inputs: Dict[str, str]     # @Input bindings                            │ │
│  │  outputs: Dict[str, str]    # @Output bindings                           │ │
│  │  children_slot: Optional[str] # Content projection slot                  │ │
│  └──────────────────────────────────────────────────────────────────────────┘ │
│                                                                                │
│  GeneratedAngularArtifact (Pydantic) - Lines 224-229                           │
│  ┌──────────────────────────────────────────────────────────────────────────┐ │
│  │  component_name: str        # e.g., "WelcomeComponent"                   │ │
│  │  files: List[GeneratedFile] # .ts, .html, .scss                          │ │
│  │  ds_components_used: List[DSComponentMapping]                            │ │
│  │  imports: List[str]         # Required Angular imports                   │ │
│  │  unresolved_nodes: List[Dict] # Failed mappings                          │ │
│  └──────────────────────────────────────────────────────────────────────────┘ │
│                                                                                │
│  GeneratedFile (Pydantic) - Lines 211-214                                      │
│  ┌──────────────────────────────────────────────────────────────────────────┐ │
│  │  path: str                  # e.g., "welcome.component.ts"               │ │
│  │  content: str               # File content                               │ │
│  │  file_type: str             # "typescript", "html", "scss"               │ │
│  └──────────────────────────────────────────────────────────────────────────┘ │
│                                                                                │
│  ValidationError (Pydantic) - Lines 231-236                                    │
│  ┌──────────────────────────────────────────────────────────────────────────┐ │
│  │  file_path: str                                                          │ │
│  │  error_type: str            # "parse_error", "generation_error", etc.    │ │
│  │  message: str                                                            │ │
│  │  line: Optional[int]                                                     │ │
│  │  suggestion: Optional[str]                                               │ │
│  └──────────────────────────────────────────────────────────────────────────┘ │
└────────────────────────────────────────────────────────────────────────────────┘
```

---

## Configuration & Environment

```
┌────────────────────────────────────────────────────────────────────────────────┐
│                         CONFIGURATION (Lines 27-37)                            │
├────────────────────────────────────────────────────────────────────────────────┤
│                                                                                │
│  class Config:                                                                 │
│  ┌──────────────────────────────────────────────────────────────────────────┐ │
│  │  LLM_MODEL = "gpt-4o"              # GPT-4 Omni (text + vision)          │ │
│  │  LLM_TEMPERATURE = 0.1             # Low randomness for consistency      │ │
│  │  MAX_REPAIR_ATTEMPTS = 2           # Max repair loop iterations          │ │
│  │  OPENAI_API_KEY = os.getenv(...)   # From environment                    │ │
│  │  MAX_NODES_PER_CHUNK = 50          # For chunked processing              │ │
│  │  MAX_JSON_SIZE = 100000            # 100KB threshold for chunking        │ │
│  │  USE_SCREENSHOT_ANALYSIS = True    # Enable GPT-4 Vision                 │ │
│  └──────────────────────────────────────────────────────────────────────────┘ │
│                                                                                │
│  ENVIRONMENT VARIABLES:                                                        │
│  ┌──────────────────────────────────────────────────────────────────────────┐ │
│  │  OPENAI_API_KEY=sk_...             # Required for LLM access             │ │
│  └──────────────────────────────────────────────────────────────────────────┘ │
│                                                                                │
│  DEPENDENCIES (requirements.txt):                                              │
│  ┌──────────────────────────────────────────────────────────────────────────┐ │
│  │  langchain                # LLM framework                                │ │
│  │  langchain-openai         # OpenAI integration                           │ │
│  │  langchain-anthropic      # Anthropic integration (optional)             │ │
│  │  langgraph               # Graph-based workflow orchestration            │ │
│  │  pydantic                # Data validation                               │ │
│  │  python-dotenv           # Environment variable loading                  │ │
│  └──────────────────────────────────────────────────────────────────────────┘ │
└────────────────────────────────────────────────────────────────────────────────┘
```

---

## File Structure & Entry Points

```
langgraph-implementation/
├── figma_to_angular_agent.py    # Main implementation (1,685 lines)
│   ├── Lines 27-37:     Config class
│   ├── Lines 90-176:    analyze_screenshot_for_styling()
│   ├── Lines 182-236:   Data models (Enums, dataclasses, Pydantic)
│   ├── Lines 242-253:   AgentState TypedDict
│   ├── Lines 256-294:   initialize_catalog()
│   ├── Lines 296-372:   Tool definitions (@tool decorated)
│   ├── Lines 378-560:   ingest_figma_node()
│   ├── Lines 562-653:   Helper functions (flatten, parse JSON)
│   ├── Lines 656-905:   build_ir_node() + helpers
│   ├── Lines 922-1069:  map_to_design_system_node()
│   ├── Lines 1071-1245: _build_design_structure_for_codegen()
│   ├── Lines 1292-1476: generate_angular_code_node()
│   ├── Lines 1478-1527: validate_node()
│   ├── Lines 1529-1580: repair_node()
│   ├── Lines 1582-1597: should_repair() conditional
│   ├── Lines 1599-1622: create_workflow()
│   └── Lines 1624-1670: run_figma_to_angular() - MAIN ENTRY
│
├── run_agent.py                 # Example runner (102 lines)
│   ├── Load figma_tree.json
│   ├── Load documentation/documentation.json
│   ├── Call run_figma_to_angular()
│   └── Save to output/generated/
│
├── figma_tree.json              # Sample Figma export
├── documentation/
│   └── documentation.json       # Angular Material Compodoc
└── output/generated/            # Generated files output
    ├── {name}.component.ts
    ├── {name}.component.html
    ├── {name}.component.scss
    └── metadata.json
```

---

## LLM Interaction Summary

| Stage | Model | Purpose | Structured Output |
|-------|-------|---------|-------------------|
| IR Generation | gpt-4o | Semantic type classification | JSON array of IR nodes |
| DS Mapping | gpt-4o | Component matching (with tools) | JSON array of mappings |
| Code Generation | gpt-4o | Generate TS/HTML/SCSS | `GeneratedAngularArtifact` |
| Code Repair | gpt-4o | Fix validation errors | `GeneratedAngularArtifact` |
| Screenshot Analysis | gpt-4o (vision) | Extract visual styling | JSON styling object |

---

## Key Function Reference

| Function | Line | Purpose |
|----------|------|---------|
| `run_figma_to_angular()` | 1624 | **Main entry point** |
| `create_workflow()` | 1599 | Build LangGraph workflow |
| `ingest_figma_node()` | 378 | Clean/normalize Figma JSON |
| `build_ir_node()` | 656 | Convert to IR representation |
| `map_to_design_system_node()` | 922 | Map IR to Angular Material |
| `generate_angular_code_node()` | 1292 | Generate component files |
| `validate_node()` | 1478 | Check generated code |
| `repair_node()` | 1529 | Fix validation errors |
| `should_repair()` | 1582 | Conditional routing logic |
| `analyze_screenshot_for_styling()` | 90 | GPT-4 Vision analysis |
| `_build_design_structure_for_codegen()` | 1071 | Extract design context |
| `initialize_catalog()` | 256 | Parse design system JSON |

---

## Technology Stack

| Layer | Technology | Purpose |
|-------|-----------|---------|
| Workflow Engine | LangGraph | Graph-based workflow orchestration |
| LLM Framework | LangChain | LLM abstractions, tools, structured output |
| LLM Provider | OpenAI (gpt-4o) | Text and vision understanding |
| Vision Model | gpt-4o (vision) | Screenshot analysis for styling |
| Data Validation | Pydantic | Type-safe models with validation |
| Configuration | python-dotenv | Environment variable management |
| Input | JSON | Figma export format |
| Output | TypeScript/HTML/SCSS | Angular component files |

---

## Key Architectural Patterns

### 1. Hierarchical Processing
The system preserves Figma's tree structure throughout:
- **Figma JSON** → **IR Tree** → **Mappings** → **HTML Structure**
- Each stage respects parent-child relationships

### 2. Chunking & Batch Processing
For large designs (>100KB):
- Flattens tree temporarily
- Processes in 50-node chunks
- Reconstructs original hierarchy
- Enables handling designs with 1000+ nodes

### 3. Tool-Augmented LLM
In design system mapping stage:
- LLM can call tools (get_component_by_intent, etc.)
- Up to 10 tool-calling iterations per chunk
- More accurate mappings than single-shot

### 4. Structured Output
Code generation uses Pydantic models:
- LLM outputs constrained to schema
- Ensures consistent file structure
- Enables post-processing without re-parsing

### 5. Graceful Degradation
If IR generation fails:
- Falls back to Figma JSON directly
- System doesn't crash, continues with available data
- Errors logged but don't block workflow
