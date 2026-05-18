#!/usr/bin/env python3
"""
Thorium Patching Simulation Tool
=================================

Simulate the build.py patching process exactly on a local Chromium source tree:
1. Verify source tree and reset git submodules to clean state
2. Analyze pruning lists (informational, no files deleted)
3. Apply ungoogled-chromium-windows patches (in order)
4. Apply overlay/ files to source tree
5. Apply Thorium patches (in order)
6. Revert ALL changes — leaves the source tree pristine

Uses the same 'patch -p1' binary as build.py for maximum accuracy.

Usage:
  python devutils/simulate_patching.py
  python devutils/simulate_patching.py --source-dir ../chromium
"""

import sys
import os
import subprocess
import time
import argparse
import shutil
import tempfile
from pathlib import Path

# ====================================================================
# Configuration — adjust these paths for your environment
# ====================================================================
ROOT_DIR = Path(__file__).resolve().parent.parent
CHROMIUM_DIR = ROOT_DIR.parent / "chromium"
GIT_CMD = ROOT_DIR.parent / "PortableGit" / "cmd" / "git.exe"
PATCH_CMD = ROOT_DIR.parent / "PortableGit" / "usr" / "bin" / "patch.exe"

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


def apply_patch(patch_path, tree_path, patch_cmd=PATCH_CMD):
    """
    Apply a single patch using the same 'patch -p1' command as build.py.

    Returns (success: bool, error_msg: str|None).
    error_msg combines stdout + stderr for complete diagnostics.
    """
    stats["total"] += 1
    cmd = [
        str(patch_cmd), "-p1", "--ignore-whitespace",
        "-i", str(patch_path),
        "-d", str(tree_path),
        "--no-backup-if-mismatch", "--forward",
    ]
    # Ensure patch can create temp files: MSYS2's /tmp/ may not be writable
    patch_env = os.environ.copy()
    tmpdir = tempfile.gettempdir()
    for var in ('TMPDIR', 'TMP', 'TEMP'):
        if var not in patch_env or not patch_env[var]:
            patch_env[var] = tmpdir
    result = subprocess.run(cmd, capture_output=True, text=True, env=patch_env)
    if result.returncode == 0:
        stats["passed"] += 1
        return True, None
    else:
        stats["failed"] += 1
        # patch outputs error details to stdout, not stderr
        err = result.stdout.strip() or result.stderr.strip()
        # Add hint for common cases
        if 'Reversed' in result.stdout or 'previously applied' in result.stdout:
            hint = ('HINT: This patch was already applied to the source tree '
                    '(e.g. via overlay files or a prior build). '
                    'Make sure the source tree is clean before running.')
            err = err + '\n' + hint
        elif 'No such file or directory' in (result.stdout + result.stderr):
            hint = ('HINT: A file the patch wants to modify does not exist. '
                    'The source tree may be incomplete (e.g. missing submodule).')
            err = err + '\n' + hint
        return False, err


# ====================================================================
# Verification steps
# ====================================================================

def verify_source_tree(source_tree):
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
            [GIT_CMD, "-C", str(source_tree), "describe", "--tags", "--always"],
            capture_output=True, text=True,
        )
        if result.returncode == 0:
            print(f"  \u2705 Git tag: {result.stdout.strip()}")
    except FileNotFoundError:
        warn(f"git not found at {GIT_CMD}")

    # Check if source tree is clean
    try:
        result = subprocess.run(
            [GIT_CMD, "-C", str(source_tree), "status", "--porcelain"],
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


def analyze_pruning(prune_path, label, source_tree):
    """Analyze pruning list entries against source tree (informational only)."""
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


def apply_patches(label, series_path, base_dirs, source_tree, patch_cmd=PATCH_CMD):
    """
    Apply all patches from a series file in order, matching build.py's
    uc_patches.apply_patches() behavior.
    """
    patches = parse_series_with_sections(series_path, base_dirs)
    if not patches:
        return

    print(f"\n{'='*60}")
    print(f"PATCHES: {label}")
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

        ok, err = apply_patch(pp, source_tree, patch_cmd=patch_cmd)

        if ok:
            print(f"  \u2705 [{i}/{len(patches)}] {pp.name}")
        else:
            print(f"  \u274c [{i}/{len(patches)}] {pp.name}")
            # Show all non-empty lines from the error message
            for line in err.split("\n"):
                line = line.strip()
                if line:
                    print(f"       {line}")

    new_failed = stats["failed"] - failed_before
    duration = time.time() - start
    passed = len(patches) - new_failed - not_found
    print(f"  Result: {passed}/{len(patches)} passed  ({duration:.1f}s)")





def apply_overlay(overlay_path, source_tree):
    """
    Copy overlay files to the source tree, matching build.py's
    _apply_source_overrides() behavior exactly.
    Overlay files either overwrite existing Chromium files or create new ones.
    """
    if not overlay_path.exists():
        print(f"\n{'='*60}")
        print("OVERLAY: Apply")
        print(f"{'='*60}")
        print("  overlay/ not found — nothing to apply")
        return

    print(f"\n{'='*60}")
    print("OVERLAY: Apply to Source Tree")
    print(f"{'='*60}")
    print(f"  Overlay dir: {overlay_path}")

    new_count = 0
    overwrite_count = 0
    for f in overlay_path.rglob("*"):
        if not f.is_file() or f.name == ".gitkeep":
            continue
        rel = f.relative_to(overlay_path)
        dst = source_tree / rel
        existed_before = dst.exists()

        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(f, dst)

        if existed_before:
            overwrite_count += 1
            print(f"      ~ {rel}")
        else:
            new_count += 1
            print(f"      + {rel}")

    print(f"  Applied: {overwrite_count} overwritten, {new_count} new files")


# ====================================================================
# Main
# ====================================================================

def main():
    parser = argparse.ArgumentParser(
        description="Simulate Thorium patching on a local Chromium source tree "
                    "exactly as build.py would. Always applies patches in order, "
                    "copies overlay files, then reverts all changes.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s                          # full simulation with defaults
  %(prog)s --source-dir ../src      # use a different Chromium source
        """,
    )
    parser.add_argument(
        "--source-dir", type=str, default=None,
        help=f"Path to Chromium source tree (default: {CHROMIUM_DIR})"
    )
    args = parser.parse_args()

    source_tree = Path(args.source_dir) if args.source_dir else CHROMIUM_DIR

    # Auto-detect patch binary — same one build.py uses
    if PATCH_CMD.exists():
        patch_cmd = PATCH_CMD
    else:
        patch_cmd = None
        patch_env = os.environ.get('PATCH_BIN')
        if patch_env:
            patch_cmd = Path(patch_env)
            if not patch_cmd.exists():
                patch_cmd = shutil.which(patch_env)
                if patch_cmd:
                    patch_cmd = Path(patch_cmd)
        if patch_cmd is None:
            which_patch = shutil.which('patch')
            if which_patch:
                patch_cmd = Path(which_patch)
    if not patch_cmd or not patch_cmd.exists():
        print("  \u274c patch binary not found — install patch or set PATCH_BIN env var")
        sys.exit(1)
    print(f"  Using patch: {patch_cmd}")

    print("=" * 60)
    print("THORIUM PATCHING SIMULATION")
    print(f"Chromium:  {source_tree}")
    print(f"Thorium:   {ROOT_DIR}")
    version_file = ROOT_DIR / "chromium_version.txt"
    if version_file.exists():
        print(f"Version:   {version_file.read_text(encoding=ENCODING).strip()}")
    print("=" * 60)

    # Step 1: Verify source tree
    if not verify_source_tree(source_tree):
        sys.exit(1)

    # Step 2: Reset dirty git submodules to their committed state.
    # This ensures third_party/ffmpeg and other submodules are clean
    # before patching, matching the real build's fresh source tree.
    # Only resets submodules with local modifications (marked ' M' in status)
    # to avoid the expensive full `submodule update --init` on the entire tree.
    print(f"\n{'='*60}")
    print("RESET SUBMODULES")
    print(f"{'='*60}")
    result = subprocess.run(
        [GIT_CMD, "-C", str(source_tree), "status", "--porcelain"],
        capture_output=True, text=True,
    )
    dirty_submodules = []
    for line in result.stdout.strip().split("\n"):
        line = line.strip()
        if line.startswith("M"):
            # Submodule modification: "M <path>" or " M <path>"
            parts = line.split()
            if len(parts) >= 2:
                dirty_submodules.append(parts[-1])
    if dirty_submodules:
        for sm in dirty_submodules:
            subprocess.run(
                [GIT_CMD, "-C", str(source_tree), "submodule", "update",
                 "--force", "--init", "--no-fetch", sm],
                capture_output=True, text=True,
            )
        print(f"  \u2705 Reset {len(dirty_submodules)} submodule(s): {', '.join(dirty_submodules)}")
    else:
        print(f"  \u2705 No dirty submodules found")

    # Step 3: Analyze pruning (informational only — no files deleted)
    analyze_pruning(
        UNGOOGLED_WINDOWS_DIR / "pruning.list",
        "ungoogled-chromium-windows",
        source_tree,
    )
    analyze_pruning(
        ROOT_DIR / "pruning.list",
        "Thorium-specific",
        source_tree,
    )

    # Step 4: Apply ungoogled-chromium-windows patches
    apply_patches(
        "ungoogled-chromium-windows",
        UNGOOGLED_SERIES_FILE,
        {
            "main": UNGOOGLED_MAIN_PATCH_DIR,
            "windows": UNGOOGLED_PATCH_DIR,
        },
        source_tree,
        patch_cmd=patch_cmd,
    )

    # Step 5: Apply overlay (matches build.py order: overlay BEFORE Thorium patches)
    apply_overlay(OVERLAY_DIR, source_tree)

    # Step 6: Apply Thorium patches
    apply_patches(
        "Thorium",
        THORIUM_SERIES_FILE,
        {"thorium": THORIUM_PATCH_DIR.parent},
        source_tree,
        patch_cmd=patch_cmd,
    )

    # Step 7: Revert ALL changes — restore source tree to pristine state
    print(f"\n{'='*60}")
    print("REVERTING source tree...")
    subprocess.run([GIT_CMD, "-C", str(source_tree), "reset", "--hard", "HEAD"],
                   capture_output=True)
    subprocess.run([GIT_CMD, "-C", str(source_tree), "clean", "-fdx"],
                   capture_output=True)
    # Reset submodules that got dirtied by overlay/patches
    result = subprocess.run(
        [GIT_CMD, "-C", str(source_tree), "status", "--porcelain"],
        capture_output=True, text=True,
    )
    for line in result.stdout.strip().split("\n"):
        line = line.strip()
        if line.startswith("M"):
            parts = line.split()
            if len(parts) >= 2:
                subprocess.run(
                    [GIT_CMD, "-C", str(source_tree), "submodule", "update",
                     "--force", "--no-fetch", parts[-1]],
                    capture_output=True, text=True,
                )

    result = subprocess.run(
        [GIT_CMD, "-C", str(source_tree), "status", "--porcelain"],
        capture_output=True, text=True,
    )
    remaining = result.stdout.strip()
    if remaining:
        lines = remaining.split("\n")
        warn(f"Source tree still has {len(lines)} uncommitted change(s) after revert:")
        for line in lines[:10]:
            print(f"      {line}")
        if len(lines) > 10:
            print(f"      ... and {len(lines) - 10} more")
    else:
        print("  \u2705 Source tree restored to original state.")

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
