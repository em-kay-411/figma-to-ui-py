# ngForge - Instructions & Prompt Guide

## What is ngForge?

ngForge converts Figma designs, screenshots, text descriptions, or existing code into production-ready Angular components using a design system of your choice. It runs a multi-step AI pipeline that maps your UI to the correct design system components, generates code, validates it, and repairs any issues automatically.

---

## Table of Contents

1. [Getting Started](#getting-started)
2. [Input Types](#input-types)
3. [Generation Scenarios](#generation-scenarios)
4. [Prompt Writing Guide](#prompt-writing-guide)
5. [Working with Existing Code](#working-with-existing-code)
6. [Fast Path vs Full Pipeline](#fast-path-vs-full-pipeline)
7. [Design System Setup](#design-system-setup)
8. [CLI Usage](#cli-usage)
9. [Troubleshooting](#troubleshooting)

---

## Getting Started

### Prerequisites

- Python 3.10+
- An OpenAI API key (set as `OPENAI_API_KEY` environment variable)
- A design system catalog file (see [Design System Setup](#design-system-setup))

### Running the Web UI

```bash
# Start the API server
uvicorn api:app --reload --port 8000

# Open ide.jsx in your browser (via your dev server)
```

### Quick Start

1. Select a design system from the dropdown in the header
2. Provide at least one input: a prompt, screenshot, Figma JSON, or existing code
3. Click **Generate**
4. Review the output in the code editor (HTML, SCSS, TypeScript tabs)
5. Make further changes by updating your prompt and clicking **Regenerate**

---

## Input Types

You can provide any combination of the following. At least one is required.

| Input | How to Provide | Best For |
|-------|---------------|----------|
| **Text prompt** | Type in the prompt textarea | Describing what you want |
| **Screenshot** | Drag & drop or click the screenshot drop zone | Visual reference for styling and layout |
| **Figma JSON** | Drag & drop or click the Figma JSON drop zone | Precise structural conversion from Figma |
| **HTML file** | Click "Import" on the .html tab, or paste directly | Modifying existing components |
| **SCSS file** | Click "Import" on the .scss tab, or paste directly | Modifying existing styles |
| **TypeScript file** | Click "Import" on the .ts tab, or paste directly | Modifying existing logic |

### Combining Inputs

Inputs stack. The more context you give, the better the output.

- **Prompt alone** - Generates a component from your description
- **Screenshot alone** - Generates a component matching the visual design
- **Figma JSON alone** - Most precise structural conversion
- **Figma JSON + Screenshot** - Structure from Figma, styling hints from the screenshot
- **Figma JSON + Prompt** - Structure from Figma, customized by your instructions
- **Existing files + Prompt** - Modifies your code according to your instructions
- **Existing files + Screenshot** - Modifies your code to match the visual design
- **Existing files + Figma JSON + Prompt** - Full context: your code, the design, and what to change

---

## Generation Scenarios

### Scenario 1: Generate from a Text Prompt

Type a description of the component you want.

**Prompt:**
> Create a user profile card with an avatar, name, email, and a "Follow" button

**What happens:** The tool generates HTML from your description, converts it to an internal design tree, maps elements to your design system components, and produces Angular code.

---

### Scenario 2: Generate from a Screenshot

Drop a screenshot of a UI design into the screenshot zone. Optionally add a prompt for extra guidance.

**Prompt (optional):**
> This is a settings page. The toggles should use the design system toggle component.

**What happens:** The tool analyzes the screenshot to extract layout, colors, typography, and structure, then generates Angular code matching the visual design.

---

### Scenario 3: Generate from Figma JSON

Export your Figma design tree as JSON and drop it into the Figma JSON zone.

**Prompt (optional):**
> Focus on the main content area. Ignore the navigation sidebar.

**What happens:** The tool ingests the Figma tree, prunes noise, builds an intermediate representation, maps each element to design system components, and generates Angular code.

---

### Scenario 4: Refine Existing Code with a Simple Change

Import or paste your existing Angular component files into the code editor tabs, then type a prompt.

**Prompt:**
> Change the background color to #f5f5f5 and add 24px padding to the container

**What happens:** The tool detects this is a simple styling change (fast path), applies it via a single LLM call, and returns the modified files. No full pipeline overhead.

---

### Scenario 5: Refine Existing Code with Design System Migration

Import or paste native HTML code and ask the tool to rewrite it using your design system.

**Prompt:**
> Rewrite this component using PrimeNG components. Replace all native buttons with p-button, native inputs with p-inputText, and the select with p-dropdown.

**What happens:** The tool detects that DS component swaps are needed, runs the full pipeline (IR mapping, DS component lookup, code generation with documentation, validation, repair), and returns properly migrated code.

---

### Scenario 6: Refine with a Screenshot Reference

Import your existing files and drop a screenshot showing the desired final state.

**Prompt:**
> Make my component look like this screenshot

**What happens:** The tool analyzes the screenshot for styling cues (colors, spacing, typography) and applies them to your existing code through the full pipeline.

---

### Scenario 7: Iterative Refinement

After the first generation, update your prompt and click **Regenerate** to iterate.

**First prompt:**
> Create a data table with sortable columns

**Second prompt (after reviewing output):**
> Add pagination below the table and a search input above it

**What happens:** Each generation runs the full pipeline with your current code + new instructions. The tool preserves existing structure while applying changes.

---

## Prompt Writing Guide

### General Best Practices

1. **Be specific about what you want, not how to implement it**
   - Good: "Add a search bar above the table that filters rows by name"
   - Avoid: "Create an input element with ngModel binding and a pipe"

2. **Name design system components when you know them**
   - Good: "Use p-table for the data grid and p-dropdown for the filter"
   - OK: "Use a data table and dropdown from the design system"
   - The tool maps components automatically, but explicit names help

3. **Describe the visual structure in spatial terms**
   - Good: "Two-column layout: sidebar on the left (250px), main content on the right"
   - Good: "Stack the cards vertically with 16px gap between them"

4. **Mention content and data explicitly**
   - Good: "Table columns: Name, Email, Role, Status. Show 10 rows."
   - Avoid: "Make a table" (too vague; the tool won't know what columns to create)

5. **One concern per prompt for refinements**
   - Good: "Change all button variants to outlined style"
   - Avoid: "Change buttons to outlined, add a sidebar, fix the table, and update colors" (too many changes at once)

### Prompts for Styling Changes (Fast Path)

These trigger the fast path (single LLM call, no full pipeline):

```
Change the card background to white and add a subtle box shadow
Make the header font size 24px and bold
Set the gap between items to 12px
Center the content vertically and horizontally
Add a 1px solid border to the container with 8px border-radius
Change the primary color to #1a73e8
Make this div position: fixed at the bottom of the page
```

**Keywords that trigger fast styling path:** color, background, font, bold, shadow, border, radius, opacity, theme, padding, margin, gap, align, center, position, flex, grid, layout

### Prompts for Logic Changes (Fast Path)

```
Add a click handler to the button that shows an alert
Create a boolean variable to toggle the sidebar visibility
Add an ngIf to show the error message only when hasError is true
Emit an event when the form is submitted
Add a method that sorts the items array by name
```

**Keywords that trigger fast logic path:** method, function, handler, click, event, emit, subscribe, variable, state, logic

### Prompts for Design System Component Changes (Full Pipeline)

```
Replace the native select with a p-dropdown
Add a p-table with sorting and pagination
Use p-card instead of the plain div wrapper
Add a p-dialog that opens when the user clicks "Details"
Convert all native buttons to p-button with severity="primary"
```

**Keywords that trigger full pipeline:** add/replace/swap/use + button/input/dropdown/table/dialog/card/etc.

### Prompts for Accessibility (Fast Path)

```
Add aria-label attributes to all interactive elements
Set proper role attributes on the navigation landmarks
Add alt text to all images
Make the form keyboard-navigable with proper tabindex
```

### Prompts for Full Generation from Scratch

```
Create a dashboard with:
- A top bar showing the user's name and a notification bell
- A sidebar with navigation links: Home, Analytics, Settings
- A main area with 4 stat cards in a 2x2 grid
- Below the cards, a data table showing recent transactions
```

The more detail you provide, the better the result. Include:
- Layout structure (columns, grids, stacking)
- Specific content (text, labels, column names)
- Interactive behavior (what happens on click, toggling)
- Design system component preferences (if known)

### Prompts to Avoid

| Prompt | Problem | Better Alternative |
|--------|---------|-------------------|
| "Make it look better" | Too vague | "Increase spacing to 16px, add box shadows to cards, use the design system color palette" |
| "Fix the CSS" | No specific issue | "The cards are overflowing their container. Set max-width: 100% and overflow: hidden" |
| "Add authentication" | Out of scope | This tool generates UI components only. Authentication, routing, and backend services are out of scope. |
| "Set up NgModule routing" | Out of scope | The tool generates standalone components. Module/routing configuration is excluded. |
| "Connect to the REST API" | Out of scope | "Add a method stub that will call the user service. Use placeholder data for now." |
| "Make everything responsive" | Too broad | "Add a media query: stack the columns vertically below 768px" |

### Out-of-Scope Topics

The tool will reject prompts about:
- NgModule configuration
- Angular routing (RouterModule)
- Backend services / HTTP client setup
- REST API endpoints / database connections
- Authentication / authorization flows

If your prompt is rejected, rephrase it to focus on the UI component itself.

---

## Working with Existing Code

### Importing Files

1. Click the **.html**, **.scss**, or **.ts** tab in the code editor
2. Click the **Import** button in the tab bar
3. Select a file from your machine
4. The file content loads into the editor

You can also paste code directly into the textarea.

### What to Import

- **HTML template** (.html) - Your Angular component template
- **SCSS/CSS styles** (.scss, .css) - Your component styles
- **TypeScript class** (.ts) - Your component class with logic

You don't need all three. Import only what you have:
- Just HTML? The tool will generate matching TS and SCSS.
- Just TS? The tool will work with the logic and generate a template.
- All three? The tool modifies them as a unit.

### Design System Migration Workflow

To migrate a native HTML component to your design system:

1. Import your existing HTML, SCSS, and TS files
2. Write a prompt like:

> Rewrite this component to use PrimeNG. Replace native elements with design system equivalents. Keep all existing logic and data bindings intact.

3. Click **Generate**
4. Review the output - the tool runs the full pipeline:
   - Analyzes your HTML structure
   - Maps native elements to DS components (buttons, inputs, tables, etc.)
   - Generates new Angular code using correct DS selectors, classes, and imports
   - Validates the output
   - Repairs any issues

### Iterating on Imported Code

After the first generation:
- The code editor updates with the new output
- Your prompt clears automatically
- Type a new prompt to make additional changes
- Click **Regenerate**

Simple follow-up changes (styling, layout, logic) use the fast path for speed.
Complex follow-ups (add new DS components) use the full pipeline automatically.

---

## Fast Path vs Full Pipeline

The tool automatically selects the optimal processing path based on your inputs.

### Fast Path (Single LLM Call)

**Triggers when ALL are true:**
- You have existing code in the editor
- You provided a text prompt
- No screenshot or Figma JSON provided
- The change is simple: styling, layout, logic, or accessibility
- No new design system components are requested

**Performance:** Seconds instead of minutes. One LLM call.

**Categories handled:**

| Category | Example Prompts |
|----------|----------------|
| Visual/Style | "Change color to blue", "Add shadow", "Make text bold" |
| Layout/Structure | "Center this div", "Add padding", "Make it fixed position", "Use flexbox row" |
| Logic/Behavior | "Add a click handler", "Toggle visibility", "Sort the array" |
| Accessibility | "Add aria-labels", "Set role attributes", "Add alt text" |

### Full Pipeline (Multi-Step)

**Triggers when ANY are true:**
- Generating from scratch (no existing code)
- Screenshot or Figma JSON provided (visual analysis needed)
- New DS components requested ("add a dropdown", "use p-table")
- Component swaps ("replace buttons with p-button")
- Ambiguous or complex requests

**Pipeline steps:**
1. **Ingest** - Parse and normalize the design/code structure
2. **Prune** - Remove noise (spacers, tiny shapes, redundant wrappers)
3. **IR** - Build intermediate representation with semantic types
4. **DS Map** - Map each element to design system components
5. **CodeGen** - Generate Angular HTML + SCSS + TypeScript
6. **Validate** - Check for errors, missing imports, invalid selectors
7. **Repair** - Fix issues automatically (up to 2 rounds)

### How to Know Which Path You're On

- The chat panel shows "Processing..." during generation
- Fast path completes in seconds
- Full pipeline takes longer but produces more comprehensive results
- You don't need to do anything different - the tool decides automatically

### Forcing Full Pipeline

If you want the full pipeline even for a simple change (e.g., you want validation and repair), include a screenshot or mention a DS component:

> Change padding to 20px and verify all components are using the correct design system selectors

---

## Design System Setup

### Creating a Catalog

1. Copy the template:
   ```bash
   cp design_systems/template_catalog.json design_systems/myds_catalog.json
   ```

2. Edit the top-level fields:
   ```json
   {
     "name": "MyDesignSystem",
     "framework": "angular",
     "prefix": "mds",
     "base_url": "https://docs.example.com",
     "import_path": "@company/mds"
   }
   ```

3. Fill in the `components` array with your DS components. Each needs at minimum:
   ```json
   {
     "name": "button",
     "selector": "mds-button",
     "description": "Interactive button for user actions",
     "figma_hints": [],
     "figma_node_types": ["INSTANCE", "COMPONENT", "FRAME"],
     "urls": {
       "overview": "https://docs.example.com/components/button",
       "api": "https://docs.example.com/components/button#api"
     }
   }
   ```

4. Fill in documentation URLs in the `layout`, `content`, and `utilities` sections for CSS utility classes.

### Auto-Enriching the Catalog

After filling in URLs, run the enrichment tool:

```bash
# Generate figma_hints from component names
python generate_figma_hints.py myds

# Enrich with import statements, classes, directives, variants from docs
python doc_knowledge_builder.py myds --enrich-catalog
```

This auto-populates:
- `import_statement` - The exact import line
- `component_classes` - Angular module class names
- `directives` - Attribute directive selectors
- `base_classes` - CSS classes always applied
- `variant_class_map` - Variant name to CSS class mapping
- `inner_html_note` - What goes inside the component tag

### Component Catalog Fields Reference

| Field | Required | Description |
|-------|----------|-------------|
| `name` | Yes | Component name (e.g., "button") |
| `selector` | Yes | HTML tag (e.g., "p-button", "mds-button") |
| `description` | Yes | One-line description |
| `figma_hints` | No | Layer name patterns for auto-mapping (auto-generated) |
| `figma_node_types` | No | Figma node types to match (INSTANCE, COMPONENT, FRAME) |
| `import_statement` | No | Full import line (auto-populated by enrichment) |
| `component_classes` | No | Angular module names (auto-populated) |
| `directives` | No | Attribute directives (auto-populated) |
| `base_classes` | No | CSS classes always applied (auto-populated) |
| `variant_class_map` | No | Variant to class mapping (auto-populated) |
| `inner_html_note` | No | Content guidance (auto-populated) |
| `code_gen_instructions` | No | Special implementation notes (manual) |
| `urls.overview` | Recommended | Component overview docs |
| `urls.api` | Recommended | API reference docs |
| `urls.usage` | Recommended | Usage examples docs |

---

## CLI Usage

For batch processing without the web UI:

```bash
# Basic: Figma JSON + design system
python run_agent.py primeng

# With a screenshot for styling hints
python run_agent.py primeng screenshot.png

# Fast mode (skip ambiguous DS mappings, faster)
python run_agent.py primeng --fast

# With screenshot + fast mode
python run_agent.py primeng screenshot.png --fast

# Test in an Angular project (deploys + fixes build errors)
python run_agent.py primeng --test-project /path/to/angular/project
```

### Required Input Files

Place these in the working directory:

| File | Required | Description |
|------|----------|-------------|
| `figma_tree.json` | Yes | Figma design tree export |
| `design_systems/{ds}_catalog.json` | Yes | Design system catalog |
| `design_tokens.json` | No | Design token definitions |
| `figma_screenshots.json` | No | Screenshot URL references |

### Output

Generated files are saved to `output/generated/`:
- `component.html`
- `component.scss`
- `component.ts`
- `metadata.json` (component name, imports, unresolved nodes)
- `pipeline_log.txt` (timing and debug details)

---

## Troubleshooting

### "Out of scope" response
Your prompt mentioned backend services, routing, authentication, or other non-UI topics. Rephrase to focus on the component UI only.

### "Could you clarify?" response
Your prompt was ambiguous. The tool couldn't determine if you want a style change, layout change, component swap, or logic change. Be more specific.

### Generated code uses native HTML instead of DS components
- Check that your design system catalog includes the relevant components
- Make sure `figma_hints` are populated (run `python generate_figma_hints.py <ds>`)
- Try running without `--fast` flag (fast mode skips ambiguous mappings)
- Use explicit DS component names in your prompt: "use p-table" instead of "add a table"

### Inline styles in the output
The tool is instructed to avoid inline styles. If they appear:
- Ensure the catalog has CSS utility class documentation URLs
- Run `python doc_knowledge_builder.py <ds>` to build the utility class knowledge base
- Add a prompt note: "Do not use any inline styles. Use SCSS classes only."

### Generation is slow
- Use `--fast` mode for large designs
- For simple changes on existing code, the tool auto-selects the fast path (single LLM call)
- Avoid providing a screenshot when you only need a text-based change (screenshots trigger the full pipeline)

### Missing imports in TypeScript
The validation + repair steps catch most import issues. If something slips through:
- Re-run generation with a prompt: "Fix all missing imports in the TypeScript file"
- Check that `component_classes` and `import_statement` fields are populated in your catalog (run `--enrich-catalog`)

### Unresolved nodes
After generation, the chat may list "unresolved nodes" - elements that couldn't be mapped to a DS component. You can:
- Describe what each should be: "The unresolved Frame_1037 should be a card component"
- The tool re-runs the pipeline with your guidance

---

## Keyboard Shortcuts

| Shortcut | Action |
|----------|--------|
| `Cmd+Enter` (Mac) / `Ctrl+Enter` (Windows) | Submit prompt (Generate or Regenerate) |

---

## API Reference

### Single Endpoint: `POST /sessions/{session_id}/generate`

All inputs are optional (at least one required). Sent as `multipart/form-data`.

| Field | Type | Description |
|-------|------|-------------|
| `prompt` | string | Text instructions |
| `screenshot` | file | Image file for visual reference |
| `screenshot_base64` | string | Base64-encoded image (alternative to file) |
| `figma_json` | file | Figma design tree JSON |
| `html_content` | string | Existing HTML to modify |
| `scss_content` | string | Existing SCSS to modify |
| `ts_content` | string | Existing TypeScript to modify |
| `component_name` | string | Force a specific component name |

### Response

```json
{
  "session_id": "uuid",
  "action": "APPLY | OUT_OF_SCOPE | CLARIFY",
  "component_name": "MyComponent",
  "files": [
    { "path": "my.component.html", "content": "...", "file_type": "html" },
    { "path": "my.component.ts", "content": "...", "file_type": "typescript" },
    { "path": "my.component.scss", "content": "...", "file_type": "scss" }
  ],
  "imports": ["CommonModule", "ButtonModule"],
  "ds_components_used": [...],
  "unresolved_nodes": [],
  "unresolved_count": 0,
  "ds_coverage": {
    "total_mappable_elements": 12,
    "ds_mapped_elements": 10,
    "coverage_pct": 83.3,
    "uncovered_selectors": ["button", "select"]
  },
  "chat_history": [...]
}
```

When `action` is `OUT_OF_SCOPE` or `CLARIFY`, only `session_id`, `action`, `message`, and `chat_history` are returned (no files).
