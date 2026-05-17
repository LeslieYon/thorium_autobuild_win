#!/usr/bin/env python3
"""
Thorium Patching Simulation Tool
=================================

Simulate the build.py patching process on a local Chromium source tree to
verify all patches apply cleanly before attempting an actual build.

Use cases:
  - Verify patches after upgrading Chromium version
  - Diagnose patch conflicts after modifying ungoogled-chromium-windows submodule
  - Validate overlay/ file coverage
  - Quick feedback loop during patch development

Two verification modes:
  1. Dry-run mode (default) — checks each patch independently against the
     current source tree using 'git apply --check'. Fast, no source modification.
  2. Sequential mode (--sequential) — applies patches IN ORDER, accumulating
     changes so later patches see earlier modifications. Then reverts all changes.
     More accurate but slower.

Usage:
  python devutils/simulate_patching.py                         # dry-run mode
  python devutils/simulate_patching.py --sequential             # sequential mode
  python devutils/simulate_patching.py --source-dir ../chromium # custom source
"""

import sys
import os
import subprocess
import time
import argparse
import shutil
import difflib
from pathlib import Path

# ====================================================================
# Configuration — adjust these paths for your environment
# ====================================================================
ROOT_DIR = Path(__file__).resolve().parent.parent
CHROMIUM_DIR = ROOT_DIR.parent / "chromium"
GIT_CMD = r"/usr/bin/git"

# Derived paths
UNGOOGLED_WINDOWS_DIR = ROOT_DIR / "ungoogled-chromium-windows"
UNGOOGLED_CHROMIUM_DIR = UNGOOGLED_WINDOWS_DIR / "ungoogled-chromium"
UNGOOGLED_UTILS_DIR = UNGOOGLED_CHROMIUM_DIR / "utils"
UNGOOGLED_PATCH_DIR = UNGOOGLED_WINDOWS_DIR / "patches"
UNGOOGLED_MAIN_PATCH_DIR = UNGOOGLED_CHROMIUM_DIR / "patches"
THORIUM_PATCH_DIR = ROOT_DIR / "patches" / "thorium"
THORIUM_SERIES_FILE = ROOT_DIR / "patches" / "series"
UNGOOGLED_SERIES_FILE = ROOT_DIR / "patches" / "series.ungoogled-windows"
OVERLAY_DIR = ROOT_DIR / "overlay"

sys.path.insert(0, str(UNGOOGLED_UTILS_DIR))
try:
    from _common import ENCODING
except ImportError:
    ENCODING = "UTF-8"
sys.path.pop(0)

# Global counters
stats = {"passed": 0, "failed": 0, "total": 0, "warnings": []}


def eprint(*args, **kwargs):
    print(*args, **kwargs)


def warn(msg):
    stats["warnings"].append(msg)
    print(f"  \u26a0  {msg}")


# ====================================================================
# Helpers
# ====================================================================

def read_list_file(filepath):
    """Read a line-oriented config file, ignoring blank lines and # comments."""
    if not filepath.exists():
        return []
    entries = []
    for line in filepath.read_text(encoding=ENCODING).splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            entries.append(line)
    return entries


def parse_series_with_sections(series_path, base_dirs):
    """
    Parse a series file that may contain section markers like #[section_name].

    Returns list of resolved Path objects in order.
    """
    current_base = list(base_dirs.values())[0]
    patches = []
    if not series_path.exists():
        return patches
    with open(series_path, "r", encoding=ENCODING) as f:
        for line in f:
            stripped = line.strip()
            if not stripped:
                continue
            if stripped.startswith("#"):
                # Section marker: #[section_name]
                if stripped.startswith("#[") and stripped.endswith("]"):
                    section = stripped[2:-1].strip()
                    if section in base_dirs:
                        current_base = base_dirs[section]
                    else:
                        warn(f"Unknown section marker [{section}] in {series_path}")
                continue
            patches.append((current_base / stripped).resolve())
    return patches


def check_patch(patch_path, tree_path, check_only=True, git_cmd=GIT_CMD):
    """
    Test if a patch applies. Returns (success: bool, error_msg: str|None).

    In check_only mode, uses 'git apply --check' (no files modified).
    Otherwise, applies the patch for real.
    """
    stats["total"] += 1
    cmd = [
        git_cmd, "-C", str(tree_path),
        "apply", "--allow-empty"
    ]
    if check_only:
        cmd.append("--check")
    cmd += ["--ignore-whitespace", "-p1", str(patch_path)]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode == 0:
        stats["passed"] += 1
        return True, None
    else:
        stats["failed"] += 1
        return False, result.stderr.strip()


# ====================================================================
# Verification steps
# ====================================================================

def verify_source_tree(source_tree, git_cmd=GIT_CMD):
    """Check the source tree exists and is in a known state."""
    print("=" * 60)
    print("SOURCE TREE VERIFICATION")
    print("=" * 60)

    if not (source_tree / "BUILD.gn").exists():
        print("  \u274c Source tree missing BUILD.gn — not a valid Chromium checkout!")
        return False

    print(f"  \u2705 Source tree: {source_tree}")

    # Try to get git tag
    try:
        result = subprocess.run(
            [git_cmd, "-C", str(source_tree), "describe", "--tags", "--always"],
            capture_output=True, text=True,
        )
        if result.returncode == 0:
            print(f"  \u2705 Git tag: {result.stdout.strip()}")
    except FileNotFoundError:
        warn(f"git not found at {git_cmd} — install Git or update GIT_CMD path")

    # Check if source tree is clean
    try:
        result = subprocess.run(
            [git_cmd, "-C", str(source_tree), "status", "--porcelain"],
            capture_output=True, text=True,
        )
        if result.returncode == 0:
            output = result.stdout.strip()
            if not output:
                print(f"  \u2705 Source tree is clean (no uncommitted changes)")
            else:
                n = len(output.split("\n"))
                warn(f"{n} uncommitted change(s) in source tree — patches may behave differently")
    except Exception:
        pass

    return True


def check_pruning(prune_path, label, source_tree):
    """Check pruning list entries against source tree (without deleting)."""
    entries = read_list_file(prune_path)
    if not entries:
        print(f"\n{'='*60}")
        print(f"PRUNING: {label}")
        print(f"{'='*60}")
        print(f"  (empty or comments only — nothing to prune)")
        return

    print(f"\n{'='*60}")
    print(f"PRUNING: {label}")
    print(f"{'='*60}")
    print(f"  Pruning file: {prune_path}")
    print(f"  Entries: {len(entries)}")

    existing = sum(1 for e in entries if (source_tree / e).exists())
    missing = len(entries) - existing
    print(f"  Would remove: {existing} files")
    if missing:
        warn(f"{missing} entries not found in source tree (will be skipped)")
    # Show sample
    count = 0
    for e in entries:
        if (source_tree / e).exists():
            if count < 5:
                print(f"      - {e}")
            count += 1
    if count > 5:
        print(f"      ... and {count - 5} more")


def check_patches(mode, label, series_path, base_dirs, source_tree, git_cmd=GIT_CMD):
    """
    Check all patches in a series file.

    mode: 'dry-run' or 'sequential'
    """
    patches = parse_series_with_sections(series_path, base_dirs)
    if not patches:
        return

    print(f"\n{'='*60}")
    print(f"PATCHES: {label} ({mode})")
    print(f"{'='*60}")
    print(f"  Series: {series_path}")
    print(f"  Count:  {len(patches)}")

    failed_before = stats["failed"]
    start = time.time()
    not_found = 0

    for i, pp in enumerate(patches, 1):
        if not pp.exists():
            print(f"  \u274c [{i}/{len(patches)}] NOT FOUND: {pp.name}")
            warn(f"Missing patch: {pp}")
            stats["total"] += 1
            stats["failed"] += 1
            not_found += 1
            continue

        check_only = (mode == "dry-run")
        ok, err = check_patch(pp, source_tree, check_only=check_only, git_cmd=git_cmd)
        elapsed = time.time() - start

        if ok:
            print(f"  \u2705 [{i}/{len(patches)}] {pp.name}")
        else:
            print(f"  \u274c [{i}/{len(patches)}] {pp.name}")
            # Show first 3 lines of error
            for line in err.split("\n")[:3]:
                if line.strip():
                    print(f"       {line.strip()}")

    new_failed = stats["failed"] - failed_before
    duration = time.time() - start
    passed = len(patches) - new_failed - not_found
    print(f"  Result: {passed}/{len(patches)} passed  ({duration:.1f}s)")


def check_overlay(overlay_path, source_tree, git_cmd=GIT_CMD):
    """Analyze overlay files against source tree."""
    if not overlay_path.exists():
        print(f"\n{'='*60}")
        print("OVERLAY")
        print(f"{'='*60}")
        print("  overlay/ not found")
        return

    print(f"\n{'='*60}")
    print("OVERLAY: Source Overrides")
    print(f"{'='*60}")
    print(f"  Overlay dir: {overlay_path}")

    new_files = []
    overwrite_files = []

    for f in overlay_path.rglob("*"):
        if not f.is_file() or f.name == ".gitkeep":
            continue
        rel = f.relative_to(overlay_path)
        dst = source_tree / rel
        if dst.exists():
            overwrite_files.append(rel)
        else:
            new_files.append(rel)

    print(f"  Total overlay entries: {len(new_files) + len(overwrite_files)}")
    print(f"  Would create {len(new_files)} new files")
    for n in sorted(new_files)[:8]:
        print(f"      + {n}")
    if len(new_files) > 8:
        print(f"      ... and {len(new_files) - 8} more")
    print(f"  Would overwrite {len(overwrite_files)} existing files")
    for o in sorted(overwrite_files)[:8]:
        print(f"      ~ {o}")
    if len(overwrite_files) > 8:
        print(f"      ... and {len(overwrite_files) - 8} more")


# ====================================================================
# Main
# ====================================================================

def main():
    parser = argparse.ArgumentParser(
        description="Simulate Thorium patching on a local Chromium source tree.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s                           # dry-run: check all patches independently
  %(prog)s --sequential              # sequential: apply in order, then revert
  %(prog)s --source-dir ../src       # use a custom Chromium source path
  %(prog)s --quick                   # skip pruning & overlay checks
        """,
    )
    parser.add_argument(
        "--sequential", action="store_true",
        help="Apply patches sequentially (not dry-run), then revert. Slower but more accurate."
    )
    parser.add_argument(
        "--source-dir", type=str, default=None,
        help=f"Path to Chromium source tree (default: {CHROMIUM_DIR})"
    )
    parser.add_argument(
        "--quick", action="store_true",
        help="Skip pruning and overlay checks. Test patches only."
    )
    parser.add_argument(
        "--git-cmd", type=str, default=GIT_CMD,
        help=f"Path to git executable (default: {GIT_CMD})"
    )
    args = parser.parse_args()
    git_cmd = args.git_cmd

    source_tree = Path(args.source_dir) if args.source_dir else CHROMIUM_DIR

    print("=" * 60)
    print(f"THORIUM PATCHING SIMULATION  ({args.sequential and 'SEQUENTIAL' or 'DRY-RUN'})")
    print(f"Chromium:  {source_tree}")
    print(f"Thorium:   {ROOT_DIR}")
    version_file = ROOT_DIR / "chromium_version.txt"
    if version_file.exists():
        print(f"Version:   {version_file.read_text(encoding=ENCODING).strip()}")
    print("=" * 60)

    # Step 0: Verify source tree
    if not verify_source_tree(source_tree, git_cmd=git_cmd):
        sys.exit(1)

    # Prune — if not --quick
    if not args.quick:
        check_pruning(
            UNGOOGLED_WINDOWS_DIR / "pruning.list",
            "ungoogled-chromium-windows",
            source_tree,
        )
        check_pruning(
            ROOT_DIR / "pruning.list",
            "Thorium-specific",
            source_tree,
        )

    # Ungoogled-chromium-windows patches
    check_patches(
        args.sequential and "sequential" or "dry-run",
        "ungoogled-chromium-windows",
        UNGOOGLED_SERIES_FILE,
        {
            "main": UNGOOGLED_MAIN_PATCH_DIR,
            "windows": UNGOOGLED_PATCH_DIR,
        },
        source_tree,
        git_cmd=git_cmd,
    )

    # Overlay (unless --quick)
    if not args.quick:
        check_overlay(OVERLAY_DIR, source_tree)

    # Thorium patches
    check_patches(
        args.sequential and "sequential" or "dry-run",
        "Thorium",
        THORIUM_SERIES_FILE,
        {"thorium": THORIUM_PATCH_DIR.parent},
        source_tree,
        git_cmd=git_cmd,
    )

    # Revert if sequential mode
    if args.sequential:
        print(f"\n{'='*60}")
        print("REVERTING source tree...")
        subprocess.run([git_cmd, "-C", str(source_tree), "checkout", "--", "."],
                       capture_output=True)
        subprocess.run([git_cmd, "-C", str(source_tree), "clean", "-fd"],
                       capture_output=True)
        print("  Source tree restored to original state.")

    # Summary
    print(f"\n{'='*60}")
    print("SUMMARY")
    print(f"{'='*60}")
    print(f"  Total:  {stats['total']}")
    print(f"  Passed: {stats['passed']}")
    print(f"  Failed: {stats['failed']}")

    if stats["failed"] == 0:
        print(f"\n  \u2728 ALL CHECKS PASSED!")
    else:
        print(f"\n  \u274c {stats['failed']} CHECK(S) FAILED — review details above")

    if stats["warnings"]:
        print(f"\n  \u26a0  {len(stats['warnings'])} WARNING(S):")
        for w in stats["warnings"]:
            print(f"    \u2022 {w}")

    print()


if __name__ == "__main__":
    main()
