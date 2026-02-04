#!/usr/bin/env python3
"""
Example runner script for Figma-to-Angular agent.
Demonstrates how to use the workflow with your files.
"""

import json
import os
from pathlib import Path
from figma_to_angular_agent import run_figma_to_angular

def main():
    # 1. Load required inputs
    print("ðŸ“ Loading input files...")

    with open("./figma_tree.json") as f:
        figma_data = json.load(f)
        print(figma_data.keys())

    with open("./documentation/documentation.json") as f:
        ds_data = json.load(f)

    # 2. Load optional inputs
    design_tokens = None
    if Path("design_tokens.json").exists():
        print("âœ… Found design tokens")
        with open("design_tokens.json") as f:
            design_tokens = json.load(f)

    figma_screenshots = None
    if Path("figma_screenshots.json").exists():
        print("âœ… Found Figma screenshots")
        with open("figma_screenshots.json") as f:
            figma_screenshots = json.load(f)

    # 3. Run the agent
    print("\nðŸš€ Starting Figma-to-Angular conversion...")
    print("="*80)

    result = run_figma_to_angular(
        figma_json=figma_data,
        ds_json=ds_data,
        design_tokens=design_tokens,
        figma_screenshots={"main" : ""}
    )

    print("files:", len(result.files))
    for f in result.files:
        print(f.path, "len=", len(f.content))
    print("unresolved:", len(result.unresolved_nodes))

    # 4. Save generated files
    print("\nðŸ“ Saving generated files...")
    output_dir = Path("output/generated")

    for file in result.files:
        file_path = output_dir / file.path
        file_path.parent.mkdir(parents=True, exist_ok=True)
        with open(file_path, "w") as f:
            f.write(file.content)
        print(f"  âœ… {file.path}")

    # 5. Save metadata
    metadata = {
        "component_name": result.component_name,
        "ds_components_used": [
            {
                "figma_node_id": m.figma_node_id,
                "ds_selector": m.ds_selector,
                "inputs": m.inputs
            }
            for m in result.ds_components_used
        ],
        "imports": result.imports,
        "unresolved_nodes": result.unresolved_nodes
    }

    with open(output_dir / "metadata.json", "w") as f:
        json.dump(metadata, f, indent=2)

    # 6. Print summary
    print("\n" + "="*80)
    print("âœ¨ GENERATION COMPLETE")
    print("="*80)
    print(f"Component: {result.component_name}")
    print(f"Files generated: {len(result.files)}")
    print(f"DS components used: {len(result.ds_components_used)}")

    if result.unresolved_nodes:
        print(f"\nâš ï¸  Unresolved nodes: {len(result.unresolved_nodes)}")
        for node in result.unresolved_nodes[:3]:
            print(f"  - {node}")

    print(f"\nðŸ“‚ Output directory: {output_dir.absolute()}")
    print("\nNext steps:")
    print("  1. Review generated files in output/generated/")
    print("  2. Copy to your Angular project src/app/")
    print("  3. Add required DS module imports to your app.module.ts")
    print("  4. Run: ng serve")

if __name__ == "__main__":
    main()