#!/usr/bin/env python3
"""
Runner script for Figma-to-Angular agent.

Usage:
    python run_agent.py <design_system_name> [screenshot.png|screenshot.jpg] [--fast] [--test-project PATH]

Examples:
    python run_agent.py primeng
    python run_agent.py primeng my_design.png
    python run_agent.py primeng /path/to/screenshot.jpg
    python run_agent.py primeng --fast
    python run_agent.py primeng my_design.png --fast
    python run_agent.py primeng --test-project /path/to/angular/app

The design_system_name maps to:
    design_systems/<name>_catalog.json

Copy design_systems/template_catalog.json to design_systems/<name>_catalog.json
and fill in your components before running.

If no screenshot argument is given, the script falls back to figma_screenshots.json
(if it exists) and then to the thumbnailUrl embedded in figma_tree.json.

--fast  Enable fast mode: skip Tier 2+3 LLM mapping calls (ambiguous/low-confidence
        nodes become native HTML instead of being LLM-resolved), and double the IR
        chunk size (100 vs 50). Tier 1 threshold stays at 70 — only high-confidence
        DS matches are emitted. Use for large designs where speed matters more than
        maximising DS component coverage.

--test-project PATH
        After generation, deploy the generated files into the Angular project at PATH
        and run `ng build` in a fix loop until the build succeeds or max attempts are
        reached. Exit code 0 on build success, 1 on failure.
"""

import argparse
import json
import os
import sys
from pathlib import Path
from figma_to_angular_agent import run_figma_to_angular, METRICS


def screenshot_to_figma_screenshots(image_path: str) -> dict:
    """Convert a local PNG/JPG file into the figma_screenshots dict the agent expects.

    The agent reads figma_screenshots["main"] as a file path or URL and passes it
    to fetch_image_as_base64, so we just store the resolved absolute path here.

    Args:
        image_path: Path to a .png or .jpg/.jpeg file.

    Returns:
        {"main": "<absolute_path>"} ready to pass as figma_screenshots.

    Raises:
        FileNotFoundError: if the file does not exist.
        ValueError: if the file extension is not a supported image type.
    """
    p = Path(image_path).resolve()
    if not p.exists():
        raise FileNotFoundError(f"Screenshot not found: {image_path}")
    if p.suffix.lower() not in {".png", ".jpg", ".jpeg"}:
        raise ValueError(f"Unsupported image format '{p.suffix}'. Use .png or .jpg/.jpeg.")
    return {"main": str(p)}


def main():
    parser = argparse.ArgumentParser(
        description="Convert a Figma design tree to an Angular component.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "design_system",
        help="Design system name (maps to design_systems/<name>_catalog.json)",
    )
    parser.add_argument(
        "screenshot",
        nargs="?",
        default=None,
        help="Optional path to a screenshot PNG/JPG for styling hints",
    )
    parser.add_argument(
        "--fast",
        action="store_true",
        default=False,
        help=(
            "Fast mode: skip Tier 2+3 LLM mapping calls (ambiguous/low-confidence nodes "
            "become native HTML). Tier 1 threshold unchanged at 70. "
            "Also doubles IR chunk size to 100. Use for large designs where speed matters "
            "more than maximising DS component coverage."
        ),
    )
    parser.add_argument(
        "--test-project",
        default=None,
        metavar="PATH",
        help=(
            "Angular test project path. Deploy generated files into the project and "
            "iteratively fix compilation errors using LLM + docs until ng build succeeds."
        ),
    )
    args = parser.parse_args()

    design_system = args.design_system
    catalog_path = f"design_systems/{design_system}_catalog.json"
    print(f"Design system:  {design_system}")
    print(f"Catalog file:   {catalog_path}")
    if args.fast:
        print("Fast mode:      ON")

    # 1. Load Figma tree (required)
    print("\nLoading input files...")

    with open("./figma_tree.json") as f:
        figma_data = json.load(f)
        print(f"  figma_tree.json loaded (keys: {list(figma_data.keys())})")

    # 2. Load optional inputs
    design_tokens = None
    if Path("design_tokens.json").exists():
        print("  Found design tokens")
        with open("design_tokens.json") as f:
            design_tokens = json.load(f)

    # Screenshot: CLI arg takes priority, then figma_screenshots.json, then nothing
    figma_screenshots = None
    if args.screenshot:
        try:
            figma_screenshots = screenshot_to_figma_screenshots(args.screenshot)
            print(f"  Screenshot loaded from argument: {args.screenshot}")
        except (FileNotFoundError, ValueError) as e:
            print(f"  Warning: {e} — skipping screenshot.")
    elif Path("figma_screenshots.json").exists():
        print("  Found figma_screenshots.json")
        with open("figma_screenshots.json") as f:
            figma_screenshots = json.load(f)

    # 3. Run the agent
    print("\nStarting Figma-to-Angular conversion...")
    print("=" * 80)

    result, _ = run_figma_to_angular(
        figma_json=figma_data,
        ds_json=None,           # no longer used
        design_tokens=design_tokens,
        figma_screenshots=figma_screenshots,
        design_system=design_system,
        fast_mode=args.fast,
    )

    print("files:", len(result.files))
    for f in result.files:
        print(f"  {f.path}  (len={len(f.content)})")
    print("unresolved:", len(result.unresolved_nodes))

    # 4. Save generated files
    print("\nSaving generated files...")
    output_dir = Path("output/generated")

    for file in result.files:
        file_path = output_dir / file.path
        file_path.parent.mkdir(parents=True, exist_ok=True)
        with open(file_path, "w") as f:
            f.write(file.content)
        print(f"  {file.path}")

    # 5. Save metadata
    metadata = {
        "component_name": result.component_name,
        "ds_components_used": [
            {
                "figma_node_id": m.figma_node_id,
                "ds_selector": m.ds_selector,
                "inputs": m.inputs,
            }
            for m in result.ds_components_used
        ],
        "imports": result.imports,
        "unresolved_nodes": result.unresolved_nodes,
    }

    output_dir.mkdir(parents=True, exist_ok=True)
    with open(output_dir / "metadata.json", "w") as f:
        json.dump(metadata, f, indent=2)

    # 6. Print summary
    print("\n" + "=" * 80)
    print("GENERATION COMPLETE")
    print("=" * 80)
    print(f"Component:          {result.component_name}")
    print(f"Files generated:    {len(result.files)}")
    print(f"DS components used: {len(result.ds_components_used)}")

    if result.unresolved_nodes:
        print(f"\nUnresolved nodes: {len(result.unresolved_nodes)}")
        for node in result.unresolved_nodes[:3]:
            print(f"  - {node}")

    # 7. Save pipeline log trace
    log_path = output_dir / "pipeline_log.txt"
    with open(log_path, "w") as f:
        f.write(METRICS.summary())
        f.write("\n\nDETAILED LOG TRACE:\n")
        for entry in METRICS.log_trace:
            f.write(f"  [{entry['step']}] {entry['message']}\n")
    print(f"  Pipeline log saved to {log_path}")

    print(f"\nOutput directory: {output_dir.absolute()}")

    if args.test_project:
        from deploy_and_fix import deploy_and_fix
        catalog_data = None
        if Path(catalog_path).exists():
            with open(catalog_path) as f:
                catalog_data = json.load(f)
        print("\n" + "=" * 80)
        print("DEPLOYING TO TEST PROJECT")
        print("=" * 80)
        ok = deploy_and_fix(
            generated_files=result.files,
            test_project_path=args.test_project,
            catalog=catalog_data,
            max_attempts=6,
        )
        sys.exit(0 if ok else 1)
    else:
        print("\nNext steps:")
        print("  1. Review generated files in output/generated/")
        print("  2. Copy to your Angular project src/app/")
        print("  3. Add required DS module imports to your app.module.ts")
        print("  4. Run: ng serve")


if __name__ == "__main__":
    main()
