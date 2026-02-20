#!/usr/bin/env python3
"""
Runner script for Figma-to-Angular agent.

Usage:
    python run_agent.py <design_system_name>

Example:
    python run_agent.py primeng

The design_system_name maps to:
    design_systems/<name>_catalog.json

Copy design_systems/template_catalog.json to design_systems/<name>_catalog.json
and fill in your components before running.
"""

import json
import os
import sys
from pathlib import Path
from figma_to_angular_agent import run_figma_to_angular, METRICS


def main():
    if len(sys.argv) < 2:
        print("Usage: python run_agent.py <design_system_name>")
        print("  Example: python run_agent.py primeng")
        print("")
        print("  The name maps to design_systems/<name>_catalog.json")
        print("  Copy design_systems/template_catalog.json and fill it in if the file doesn't exist.")
        sys.exit(1)

    design_system = sys.argv[1]
    catalog_path = f"design_systems/{design_system}_catalog.json"
    print(f"Design system:  {design_system}")
    print(f"Catalog file:   {catalog_path}")

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

    figma_screenshots = None
    if Path("figma_screenshots.json").exists():
        print("  Found Figma screenshots")
        with open("figma_screenshots.json") as f:
            figma_screenshots = json.load(f)

    # 3. Run the agent
    print("\nStarting Figma-to-Angular conversion...")
    print("=" * 80)

    result = run_figma_to_angular(
        figma_json=figma_data,
        ds_json=None,           # no longer used
        design_tokens=design_tokens,
        figma_screenshots=figma_screenshots,
        design_system=design_system,
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
    print("\nNext steps:")
    print("  1. Review generated files in output/generated/")
    print("  2. Copy to your Angular project src/app/")
    print("  3. Add required DS module imports to your app.module.ts")
    print("  4. Run: ng serve")


if __name__ == "__main__":
    main()
