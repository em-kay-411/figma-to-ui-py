#!/usr/bin/env python3
"""
deploy_and_fix.py — Deploy generated Angular files to a test project and
iteratively fix compilation errors using LLM + documentation tools.

Usage (standalone):
    python deploy_and_fix.py <test_project_path> --ds <design_system> [--max-attempts N]

This reads generated files from output/generated/ (last pipeline run).
"""

import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv
from langchain_core.messages import HumanMessage, SystemMessage, ToolMessage, AIMessage
from langchain_core.tools import tool as lc_tool
from langchain_openai import ChatOpenAI

from doc_scraper import DocScraper

load_dotenv()

# ============================================================================
# MODULE-LEVEL STATE (shared with tool functions)
# ============================================================================

_catalog_map: dict = {}   # {component_name_lower: catalog_entry}
_scraper: DocScraper = DocScraper()
MAX_TOOL_ITERS = 10

# Files to skip when detecting app component files
_SKIP_STEMS = {"spec", "main", "routes", "config"}


# ============================================================================
# TOOL DEFINITIONS
# ============================================================================

@lc_tool
def fetch_component_docs_fix(component: str, doc_type: str = "api") -> str:
    """Fetch DS component docs — correct import path, @Input names, directive selectors.

    Args:
        component: Component name (e.g., 'button', 'InputText').
        doc_type: One of 'api', 'usage', 'overview'.
    """
    entry = _catalog_map.get(component.lower())
    if not entry:
        # Try partial match
        for key, val in _catalog_map.items():
            if component.lower() in key or key in component.lower():
                entry = val
                break
    if not entry:
        return f"No catalog entry found for component '{component}'."

    urls = entry.get("urls", {})
    url = urls.get(doc_type) or urls.get("api") or urls.get("overview")
    if not url:
        # Return catalog info we have
        return json.dumps({
            "selector": entry.get("selector"),
            "base_classes": entry.get("base_classes", []),
            "directives": entry.get("directives", []),
            "variant_class_map": entry.get("variant_class_map", {}),
        }, indent=2)

    text = _scraper.fetch(url, max_chars=8000)
    if not text:
        return f"Could not fetch docs for '{component}' ({doc_type})."
    return text[:8000]


@lc_tool
def fetch_doc_url_fix(url: str) -> str:
    """Fetch any documentation URL (cached, up to 8000 chars).

    Args:
        url: Full URL to fetch.
    """
    text = _scraper.fetch(url, max_chars=8000)
    if not text:
        return f"Could not fetch URL: {url}"
    return text[:8000]


# ============================================================================
# FILE DETECTION AND PATCHING
# ============================================================================

def _detect_app_files(app_dir: str) -> tuple[Optional[str], Optional[str], Optional[str]]:
    """Scan src/app/ for the first .html, .css/.scss, and .ts files.

    Skips files whose stem contains 'spec', 'main', 'routes', or 'config'.

    Returns:
        (html_name, css_name, ts_name) — any may be None if not found.
    """
    p = Path(app_dir)
    html_f = css_f = ts_f = None

    for f in sorted(p.iterdir()):
        if not f.is_file():
            continue
        # A file like "app.component.spec.ts" has stem "app.component.spec"
        # Split on '.' to check each part
        stem_parts = set(f.stem.lower().split("."))
        if stem_parts & _SKIP_STEMS:
            continue

        ext = f.suffix.lower()
        if ext == ".html" and html_f is None:
            html_f = f.name
        elif ext in {".css", ".scss"} and css_f is None:
            css_f = f.name
        elif ext == ".ts" and ts_f is None:
            ts_f = f.name

    return html_f, css_f, ts_f


def _detect_root_selector(project_path: str) -> str:
    """Read src/index.html and extract the custom element tag in <body>.

    Returns:
        e.g. 'app-root', or 'app-root' as fallback.
    """
    index_path = Path(project_path) / "src" / "index.html"
    try:
        content = index_path.read_text(encoding="utf-8")
        body_match = re.search(r"<body[^>]*>(.*?)</body>", content, re.DOTALL | re.IGNORECASE)
        if body_match:
            body = body_match.group(1)
            m = re.search(r"<(app-[a-z][a-z0-9-]*)", body)
            if m:
                return m.group(1)
    except OSError:
        pass
    return "app-root"


def _patch_ts_metadata(
    ts_content: str,
    root_selector: str,
    html_filename: str,
    css_filename: str,
) -> str:
    """Patch @Component({...}) decorator fields in a TypeScript file.

    Replaces:
        selector: '<anything>'  →  selector: '<root_selector>'
        templateUrl: '<anything>'  →  templateUrl: './<html_filename>'
        styleUrl(s): ...  →  styleUrl: './<css_filename>'
    """
    # Patch selector (single or double quotes)
    ts_content = re.sub(
        r"selector:\s*'[^']+'",
        f"selector: '{root_selector}'",
        ts_content,
    )
    ts_content = re.sub(
        r'selector:\s*"[^"]+"',
        f"selector: '{root_selector}'",
        ts_content,
    )

    # Patch templateUrl (single or double quotes)
    ts_content = re.sub(
        r"templateUrl:\s*'[^']+'",
        f"templateUrl: './{html_filename}'",
        ts_content,
    )
    ts_content = re.sub(
        r'templateUrl:\s*"[^"]+"',
        f"templateUrl: './{html_filename}'",
        ts_content,
    )

    # Patch styleUrl / styleUrls (handles both forms, array or string)
    ts_content = re.sub(
        r"styleUrls?\s*:.*?(?=,|\n|\})",
        f"styleUrl: './{css_filename}'",
        ts_content,
        flags=re.DOTALL,
    )

    return ts_content


# ============================================================================
# BUILD RUNNER
# ============================================================================

def _run_ng_build(project_path: str) -> tuple[bool, str]:
    """Run ng build --no-progress in the project directory.

    Returns:
        (success, combined_stdout+stderr)
    """
    try:
        result = subprocess.run(
            ["ng", "build", "--no-progress"],
            cwd=project_path,
            capture_output=True,
            text=True,
            timeout=180,
        )
        return result.returncode == 0, result.stdout + result.stderr
    except subprocess.TimeoutExpired:
        return False, "ng build timed out after 180 seconds"
    except FileNotFoundError:
        return False, "ng command not found — is Angular CLI installed and on PATH?"


# ============================================================================
# ERROR PARSER
# ============================================================================

_ERROR_RE = re.compile(r"\bERROR\b|error TS\d+|NG\d+:")
_WARN_RE = re.compile(r"[Ww]arning")


def _parse_errors(output: str) -> list[str]:
    """Extract error lines (and up to 3 context lines each) from ng build output.

    Filters out warning lines. Returns a deduplicated list capped at 60 lines.
    """
    lines = output.splitlines()
    collected: list[str] = []
    continuation = 0
    seen: set[str] = set()

    for line in lines:
        if _WARN_RE.search(line):
            continuation = 0
            continue

        if _ERROR_RE.search(line):
            if line not in seen:
                seen.add(line)
                collected.append(line)
            continuation = 3
        elif continuation > 0:
            if line.strip():
                if line not in seen:
                    seen.add(line)
                    collected.append(line)
                continuation -= 1
            # blank lines don't consume the continuation budget

        if len(collected) >= 60:
            break

    return collected


# ============================================================================
# LLM FIX LOOP
# ============================================================================

_FIX_SYSTEM_PROMPT = """\
You are an Angular expert fixing compilation errors in a generated component.

Available tools:
- fetch_component_docs_fix(component, doc_type): get correct import path, @Input names,
  directive selectors for a DS component
- fetch_doc_url_fix(url): fetch any documentation URL

Error triage:
  "Cannot find module 'X'"       → wrong import path → use fetch_component_docs_fix
  "is not a known element"       → missing module in @Component imports array
  "Can't bind to 'X'"            → wrong property name → check component API
  "Property 'X' does not exist"  → wrong attribute → look up correct name

After researching, output ONLY valid JSON (no markdown):
{"files": [{"filename": "app.ts", "content": "<complete file>"}], "summary": "..."}

Rules:
- Include only files that changed
- Output COMPLETE file content — never truncate
- Do NOT change selector, templateUrl, or styleUrl
- Ignore warnings — fix ERRORs only
"""


def _fix_with_llm(
    errors: list[str],
    current_files: dict[str, str],
    attempt: int,
    catalog_map: dict,
) -> Optional[dict[str, str]]:
    """Ask the LLM to fix compilation errors using tool-calling.

    Args:
        errors:        Parsed error lines from ng build.
        current_files: {filename: content} of the current app files.
        attempt:       Current attempt number (for logging).
        catalog_map:   Catalog dict keyed by component name (lowercase).

    Returns:
        {filename: new_content} dict for changed files, or None on failure.
    """
    global _catalog_map
    _catalog_map = catalog_map

    llm = ChatOpenAI(
        model="gpt-4o",
        temperature=0.1,
        api_key=os.getenv("OPENAI_API_KEY"),
    )

    tools = [fetch_component_docs_fix, fetch_doc_url_fix]
    llm_with_tools = llm.bind_tools(tools)

    tool_dispatch = {
        "fetch_component_docs_fix": fetch_component_docs_fix,
        "fetch_doc_url_fix": fetch_doc_url_fix,
    }

    errors_block = "\n".join(errors)
    files_block = "\n\n".join(
        f"=== {fname} ===\n{content}"
        for fname, content in current_files.items()
    )

    human_content = (
        f"Fix attempt {attempt}. Build errors:\n\n"
        f"{errors_block}\n\n"
        f"Current files:\n\n"
        f"{files_block}\n\n"
        "Research the errors using tools, then output the corrected files as JSON."
    )

    messages: list = [
        SystemMessage(content=_FIX_SYSTEM_PROMPT),
        HumanMessage(content=human_content),
    ]

    for iteration in range(MAX_TOOL_ITERS):
        response = llm_with_tools.invoke(messages)
        messages.append(response)

        if not response.tool_calls:
            # Try to extract JSON from response content
            content = response.content
            # Strip markdown code fences if present
            content = re.sub(r"^```(?:json)?\s*", "", content.strip(), flags=re.MULTILINE)
            content = re.sub(r"\s*```$", "", content.strip(), flags=re.MULTILINE)
            content = content.strip()

            try:
                parsed = json.loads(content)
                changed: dict[str, str] = {}
                for file_entry in parsed.get("files", []):
                    fname = file_entry.get("filename", "")
                    fcontent = file_entry.get("content", "")
                    if fname and fcontent:
                        changed[fname] = fcontent
                summary = parsed.get("summary", "")
                if summary:
                    print(f"  Fix: {summary}")
                return changed if changed else None
            except (json.JSONDecodeError, KeyError, TypeError) as e:
                print(f"  Warning: Could not parse LLM JSON response: {e}")
                return None

        # Execute tool calls
        print(
            f"  Fix iteration {iteration + 1}: "
            f"{len(response.tool_calls)} tool call(s) "
            f"({', '.join(tc['name'] for tc in response.tool_calls)})"
        )
        for tc in response.tool_calls:
            tool_fn = tool_dispatch.get(tc["name"])
            if tool_fn is not None:
                print(f"    Tool: {tc['name']}({tc['args']})")
                tool_result = tool_fn.invoke(tc["args"])
            else:
                tool_result = f"Unknown tool: {tc['name']}"
            messages.append(ToolMessage(content=str(tool_result), tool_call_id=tc["id"]))

    print(f"  Fix: hit max tool iterations ({MAX_TOOL_ITERS})")
    return None


# ============================================================================
# MAIN DEPLOY AND FIX FUNCTION
# ============================================================================

def deploy_and_fix(
    generated_files: list,
    test_project_path: str,
    catalog: Optional[dict] = None,
    max_attempts: int = 6,
) -> bool:
    """Deploy generated Angular files to a test project and fix compilation errors.

    Args:
        generated_files:   List of GeneratedFile objects (with .path and .content).
        test_project_path: Path to the Angular test project root.
        catalog:           Design system catalog dict (for doc lookups).
        max_attempts:      Maximum fix iterations before giving up.

    Returns:
        True if ng build succeeds, False otherwise.
    """
    project_path = str(Path(test_project_path).resolve())
    app_dir = str(Path(project_path) / "src" / "app")

    # 1. Detect existing app files
    html_f, css_f, ts_f = _detect_app_files(app_dir)
    if not html_f or not ts_f:
        print(f"Error: Could not detect app files in {app_dir}")
        print(f"  html_f={html_f}, css_f={css_f}, ts_f={ts_f}")
        return False

    # Use .scss if no css/scss detected (Angular default)
    if not css_f:
        css_f = html_f.replace(".html", ".scss")

    print(f"Target files:  {html_f}, {css_f}, {ts_f}")

    # 2. Read root selector
    root_selector = _detect_root_selector(project_path)
    print(f"Root selector: {root_selector}")

    # 3. Build catalog map for tool lookups
    catalog_map: dict = {}
    if catalog:
        for entry in catalog.get("components", []):
            name = entry.get("name", "").lower()
            selector = entry.get("selector", "").lower()
            if name:
                catalog_map[name] = entry
            if selector and selector != name:
                catalog_map[selector] = entry

    # 4. Map generated files to target slots by extension
    file_map: dict[str, str] = {}   # target_filename → content
    for gf in generated_files:
        path = getattr(gf, "path", "") or ""
        content = getattr(gf, "content", "") or ""
        ext = Path(path).suffix.lower()

        if ext == ".html":
            file_map[html_f] = content
        elif ext in {".css", ".scss"}:
            file_map[css_f] = content
        elif ext == ".ts":
            # Patch @Component decorator so it matches the actual app
            patched = _patch_ts_metadata(content, root_selector, html_f, css_f)
            file_map[ts_f] = patched

    if not file_map:
        print("Error: No generated files to deploy.")
        return False

    # 5. Write files to src/app/
    for fname, content in file_map.items():
        dest = Path(app_dir) / fname
        dest.write_text(content, encoding="utf-8")
        print(f"Written: {dest}")

    # Track current on-disk file contents for LLM context
    current_files = dict(file_map)

    # 6. Build + fix loop
    for attempt in range(1, max_attempts + 1):
        print(f"\nBUILD ATTEMPT {attempt}/{max_attempts}")
        print("  Running ng build...")

        success, output = _run_ng_build(project_path)

        if success:
            print("\u2713 Build succeeded!")
            return True

        errors = _parse_errors(output)

        if not errors:
            print("Build failed but no parseable errors found.")
            print("Raw output (last 40 lines):")
            for line in output.splitlines()[-40:]:
                print(f"  {line}")
            return False

        print(f"  {len(errors)} error(s) found:")
        for e in errors[:5]:
            print(f"    {e}")
        if len(errors) > 5:
            print(f"    ... ({len(errors) - 5} more)")

        if attempt == max_attempts:
            print(f"\nMax attempts ({max_attempts}) reached. Remaining errors:")
            for e in errors:
                print(f"  {e}")
            return False

        print(f"\nFix attempt {attempt}...")
        fixed = _fix_with_llm(errors, current_files, attempt, catalog_map)

        if fixed is None:
            print("  LLM fix returned no changes — stopping.")
            return False

        # Apply fixes: map LLM-provided filenames back to actual target filenames
        for fname, content in fixed.items():
            base = Path(fname).name
            target = None
            # Match by exact name first, then by extension
            for tf in [html_f, css_f, ts_f]:
                if tf and base == tf:
                    target = tf
                    break
            if target is None:
                for tf in [html_f, css_f, ts_f]:
                    if tf and Path(base).suffix.lower() == Path(tf).suffix.lower():
                        target = tf
                        break
            if target is None:
                target = base  # fall back: write as-is

            dest = Path(app_dir) / target
            dest.write_text(content, encoding="utf-8")
            print(f"  Updated: {dest}")
            current_files[target] = content

    return False


# ============================================================================
# STANDALONE CLI
# ============================================================================

def main():
    import argparse
    from types import SimpleNamespace

    parser = argparse.ArgumentParser(
        description="Deploy generated Angular files and fix compilation errors.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Reads generated files from output/generated/ (last pipeline run).\n"
            "Example:\n"
            "  python deploy_and_fix.py /path/to/app --ds primeng"
        ),
    )
    parser.add_argument(
        "test_project_path",
        help="Path to the Angular test project root",
    )
    parser.add_argument(
        "--ds", "--design-system",
        dest="design_system",
        required=True,
        help="Design system name (maps to design_systems/<name>_catalog.json)",
    )
    parser.add_argument(
        "--max-attempts",
        type=int,
        default=6,
        help="Maximum fix iterations (default: 6)",
    )
    args = parser.parse_args()

    # Load catalog
    catalog_path = Path("design_systems") / f"{args.design_system}_catalog.json"
    catalog = None
    if catalog_path.exists():
        with open(catalog_path) as f:
            catalog = json.load(f)
        print(f"Catalog loaded: {catalog_path}")
    else:
        print(f"Warning: No catalog found at {catalog_path}")

    # Load generated files from last pipeline run
    output_dir = Path("output/generated")
    if not output_dir.exists():
        print(f"Error: {output_dir} does not exist. Run the pipeline first.")
        sys.exit(1)

    generated_files = []
    for ext in [".html", ".css", ".scss", ".ts"]:
        for p in sorted(output_dir.glob(f"*{ext}")):
            if "spec" in p.stem.lower():
                continue
            content = p.read_text(encoding="utf-8")
            generated_files.append(SimpleNamespace(path=p.name, content=content))
            print(f"Loaded: {p.name} ({len(content)} chars)")

    if not generated_files:
        print(f"Error: No generated files found in {output_dir}")
        sys.exit(1)

    print(f"\nDeploying {len(generated_files)} file(s) to {args.test_project_path}")
    print("=" * 80)

    ok = deploy_and_fix(
        generated_files=generated_files,
        test_project_path=args.test_project_path,
        catalog=catalog,
        max_attempts=args.max_attempts,
    )

    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
