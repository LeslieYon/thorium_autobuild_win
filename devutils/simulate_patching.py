#!/usr/bin/env python3
"""
Thorium Patching Simulation Tool (Refactored)
=============================================

Simulate the build.py patching process exactly — without touching the real source tree.

How it works:
1. Parse patches/series and patches/series.external to discover all patches
   and the files each patch modifies (same approach as backup_patch_files.py)
2. Copy ONLY the affected files from a pristine source tree (e.g. build/src_original/)
   to a temporary (or user-specified) working directory
3. Apply all patches in the EXACT same order as build.py:
   a. external patches (from series.external — ungoogled-windows, cromite, etc.)
   b. overlay/ files copied on top
   c. Thorium-specific patches (from patches/series)
4. Record detailed error information per patch (return code, stdout, stderr)
5. Report a comprehensive summary table

The pristine source tree is NEVER modified.

Usage:
  python devutils/simulate_patching.py
  python devutils/simulate_patching.py --source-dir ../chromium
  python devutils/simulate_patching.py --work-dir D:/tmp/patch_test --keep-work-dir
  python devutils/simulate_patching.py --sequential
"""

import sys
import os
import subprocess
import time
import argparse
import shutil
import tempfile
import re
import json
from pathlib import Path
from typing import List, Set, Tuple, Optional, Dict

# ====================================================================
# Configuration — adjust these paths for your environment
# ====================================================================
ROOT_DIR = Path(__file__).resolve().parent.parent
CHROMIUM_DIR = ROOT_DIR.parent / "chromium"
DEFAULT_SOURCE_DIR = ROOT_DIR / "build" / "src_original"
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
EXTERNAL_SERIES_FILE = ROOT_DIR / "patches" / "series.external"
OVERLAY_DIR = ROOT_DIR / "overlay"

sys.path.insert(0, str(UNGOOGLED_UTILS_DIR))
try:
    from _common import ENCODING
except ImportError:
    ENCODING = "UTF-8"
sys.path.pop(0)

def _to_native_path(path: Path) -> str:
    """Convert a path to Windows native format for native binaries (e.g. patch.exe).

    In MSYS2 Python, tempfile returns MSYS2-style paths (/tmp/...) that native
    Windows binaries cannot understand. This function uses cygpath to convert.
    Falls back to str(path) if cygpath is not available.
    """
    path_str = str(path)
    # If it already looks like a Windows path, use as-is
    if ':\\' in path_str or ':/' in path_str.replace('\\', '/'):
        return path_str
    try:
        result = subprocess.run(
            ['cygpath', '-w', path_str],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except Exception:
        pass
    return path_str


# Regex: match "--- a/path/to/file" or "--- path/to/file" (patch file target)
_PATCH_FILE_RE = re.compile(r'^---\s+(?:a/)?(.+?)(?:\t.*)?$')
_PATCH_FILE_DEVNULL_RE = re.compile(r'^---\s+/dev/null')
# Regex: match "Binary files a/path and b/path differ"
_BINARY_PATCH_RE = re.compile(
    r'^Binary\s+files\s+(?:a/)?(.+?)\s+and\s+(?:b/)?.+?\s+differ$',
    re.IGNORECASE,
)
# Regex: match "rename from <path>" (git rename operations — source must exist)
_RENAME_FROM_RE = re.compile(r'^rename\s+from\s+(.+)$')


# ====================================================================
# Data structures
# ====================================================================

class PatchResult:
    """Detailed result of applying a single patch."""

    def __init__(self, patch_path: Path, label: str, index: int, total: int):
        self.patch_path = patch_path
        self.label = label  # 'ungoogled' or 'thorium'
        self.index = index
        self.total = total
        self.success: Optional[bool] = None
        self.returncode: Optional[int] = None
        self.stdout: str = ""
        self.stderr: str = ""
        self.error_summary: str = ""
        self.duration: float = 0.0
        self.files_modified: Set[str] = set()

    @property
    def name(self) -> str:
        return self.patch_path.name

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "path": str(self.patch_path),
            "label": self.label,
            "index": self.index,
            "total": self.total,
            "success": self.success,
            "returncode": self.returncode,
            "stdout": self.stdout,
            "stderr": self.stderr,
            "error_summary": self.error_summary,
            "duration": round(self.duration, 3),
            "files_modified": sorted(self.files_modified),
        }


# ====================================================================
# Helpers
# ====================================================================

def warn(msg):
    print(f"  \u26a0  {msg}")


def _read_list_file(filepath: Path) -> List[str]:
    """Read a line-oriented config file, ignoring blank lines and # comments."""
    if not filepath.exists():
        return []
    entries = []
    for line in filepath.read_text(encoding=ENCODING).splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            entries.append(line)
    return entries


# ====================================================================
# Patch discovery — same approach as backup_patch_files.py
# ====================================================================

def _extract_modified_files_from_patch(patch_path: Path) -> Set[str]:
    """
    Extract all file paths modified by a single patch.

    Returns a set of relative paths (Unix-style, e.g.
    'chrome/browser/BUILD.gn').
    """
    files: Set[str] = set()

    try:
        content = patch_path.read_text(encoding=ENCODING)
    except Exception:
        # Binary patches or unreadable
        return files

    for line in content.splitlines():
        # Skip /dev/null entries (new files that don't exist before patching)
        if _PATCH_FILE_DEVNULL_RE.match(line):
            continue

        m = _PATCH_FILE_RE.match(line)
        if m:
            path = m.group(1).strip()
            if path and path != '/dev/null':
                files.add(path.replace('\\', '/'))
            continue

        # Handle "Binary files a/path and b/path differ"
        m = _BINARY_PATCH_RE.match(line)
        if m:
            path = m.group(1).strip()
            if path:
                files.add(path.replace('\\', '/'))
            continue

        # Handle "rename from <path>" (git rename — source file must exist in
        # work directory or patch.exe fails with "Cannot rename file without
        # two valid file names")
        m = _RENAME_FROM_RE.match(line)
        if m:
            path = m.group(1).strip()
            if path:
                files.add(path.replace('\\', '/'))

    return files


def _collect_external_patches() -> List[Path]:
    """
    Resolve patch paths from series.external.
    Same section-marker logic as build.py's _apply_external_patches().
    Supports sections:
      #[windows]  — relative to <ungoogled-submodule>/patches/
      #[main]     — relative to <ungoogled-submodule>/ungoogled-chromium/patches/
      #[cromite]  — relative to cromite/build/patches/
    """
    if not EXTERNAL_SERIES_FILE.exists():
        return []

    base_dirs = {
        'windows': UNGOOGLED_PATCH_DIR,
        'main': UNGOOGLED_MAIN_PATCH_DIR,
        'cromite': ROOT_DIR / 'cromite' / 'build' / 'patches',
    }

    patches: List[Path] = []
    current_base = base_dirs['windows']

    with open(EXTERNAL_SERIES_FILE, 'r', encoding=ENCODING) as f:
        for line in f:
            stripped = line.strip()
            if not stripped:
                continue
            if stripped.startswith('#'):
                if stripped.startswith('#[') and stripped.endswith(']'):
                    section = stripped[2:-1].strip()
                    if section in base_dirs:
                        current_base = base_dirs[section]
                continue

            patch_path = (current_base / stripped).resolve()
            if not patch_path.exists():
                warn(f"External patch not found (skipping): {patch_path}")
                continue
            patches.append(patch_path)

    return patches


def _collect_thorium_patches() -> List[Path]:
    """
    Resolve patch paths from patches/series.
    Same logic as build.py's _apply_thorium_patches().
    """
    if not THORIUM_SERIES_FILE.exists():
        return []

    patches: List[Path] = []
    for line in _read_list_file(THORIUM_SERIES_FILE):
        patch_path = (ROOT_DIR / 'patches' / line).resolve()
        if not patch_path.exists():
            # Try .prepatch fallback
            prepatch_path = patch_path.with_suffix('.prepatch')
            if prepatch_path.exists():
                patch_path = prepatch_path
            else:
                warn(f"Thorium patch not found (skipping): {patch_path}")
                continue
        patches.append(patch_path)

    return patches


def discover_all_patches() -> List[Tuple[Path, str]]:
    """
    Discover all patches in build.py's application order.
    Includes both external patches (series.external) and thorium patches (series).

    Returns:
        List of (patch_path, label) tuples in application order.
        label is 'ungoogled' or 'thorium'.
    """
    result: List[Tuple[Path, str]] = []

    for p in _collect_external_patches():
        result.append((p, 'ungoogled'))

    for p in _collect_thorium_patches():
        result.append((p, 'thorium'))

    return result


def collect_all_affected_files(
    patches_info: List[Tuple[Path, str]]
) -> Tuple[Set[str], Dict[str, List[Tuple[Path, str]]]]:
    """
    For each patch, extract which files it modifies.

    Returns:
        (all_files_set, file_to_patches_map)
        - all_files_set: Union of all files modified by any patch
        - file_to_patches_map: {file_path: [(patch_path, label), ...]}
    """
    all_files: Set[str] = set()
    file_to_patches: Dict[str, List[Tuple[Path, str]]] = {}

    for patch_path, label in patches_info:
        files = _extract_modified_files_from_patch(patch_path)
        for f in files:
            all_files.add(f)
            if f not in file_to_patches:
                file_to_patches[f] = []
            file_to_patches[f].append((patch_path, label))

    return all_files, file_to_patches


# ====================================================================
# File copying logic
# ====================================================================

def copy_affected_files(
    source_dir: Path,
    work_dir: Path,
    all_files: Set[str],
) -> Tuple[int, int, List[str]]:
    """
    Copy affected files from source_dir to work_dir.

    Returns:
        (copied_count, missing_count, missing_files_list)
    """
    copied = 0
    missing = 0
    missing_files: List[str] = []

    for rel_path in sorted(all_files):
        src = source_dir / rel_path
        dst = work_dir / rel_path

        if not src.exists():
            missing += 1
            missing_files.append(rel_path)
            continue

        dst.parent.mkdir(parents=True, exist_ok=True)
        try:
            shutil.copy2(src, dst)
            copied += 1
        except Exception as exc:
            warn(f"Failed to copy {rel_path}: {exc}")

    return copied, missing, missing_files


def copy_overlay_files(overlay_path: Path, work_dir: Path) -> Tuple[int, int, List[str]]:
    """
    Copy overlay files to work_dir, matching build.py's _apply_source_overrides().

    Returns:
        (overwritten_count, new_count, copied_files_list)
    """
    if not overlay_path.exists():
        return 0, 0, []

    new_count = 0
    overwrite_count = 0
    copied_files: List[str] = []

    for f in overlay_path.rglob("*"):
        if not f.is_file() or f.name == ".gitkeep":
            continue
        rel = f.relative_to(overlay_path)
        dst = work_dir / rel
        existed_before = dst.exists()

        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(f, dst)
        copied_files.append(str(rel))

        if existed_before:
            overwrite_count += 1
        else:
            new_count += 1

    return overwrite_count, new_count, copied_files


# ====================================================================
# Patch application (mirrors build.py's uc_patches.apply_patches)
# ====================================================================

def apply_single_patch(
    patch_path: Path,
    tree_path: Path,
    patch_cmd: Path,
    env: dict,
) -> Tuple[int, str, str]:
    """
    Apply a single patch using 'patch -p1 --ignore-whitespace --forward'.

    Returns:
        (returncode, stdout_str, stderr_str)
    """
    # Convert to native Windows paths (MSYS2 /tmp/... is not understood by patch.exe)
    native_tree = _to_native_path(tree_path)
    native_patch = _to_native_path(patch_path)
    cmd = [
        str(patch_cmd), "-p1", "--ignore-whitespace",
        "-i", native_patch,
        "-d", native_tree,
        "--no-backup-if-mismatch", "--forward",
    ]
    result = subprocess.run(
        cmd, capture_output=True, text=True, env=env, timeout=120,
    )
    return result.returncode, result.stdout, result.stderr


def apply_patches_with_details(
    patches_info: List[Tuple[Path, str]],
    tree_path: Path,
    patch_cmd: Path,
    stop_on_failure: bool = False,
) -> List[PatchResult]:
    """
    Apply all patches with detailed error recording.

    Args:
        patches_info: List of (patch_path, label) tuples in application order
        tree_path: Target source tree for patching
        patch_cmd: Path to patch binary
        stop_on_failure: If True, stop after first patch failure

    Returns:
        List of PatchResult objects with detailed results
    """
    # Prepare environment for patch (MSYS2 needs TMPDIR as native Windows path)
    patch_env = os.environ.copy()
    tmpdir = _to_native_path(Path(tempfile.gettempdir()))
    for var in ('TMPDIR', 'TMP', 'TEMP'):
        if var not in patch_env or not patch_env[var]:
            patch_env[var] = tmpdir

    results: List[PatchResult] = []
    total = len(patches_info)

    for i, (patch_path, label) in enumerate(patches_info, 1):
        result = PatchResult(patch_path, label, i, total)
        result.files_modified = _extract_modified_files_from_patch(patch_path)

        if not patch_path.exists():
            result.success = False
            result.returncode = -1
            result.error_summary = "PATCH FILE NOT FOUND"
            result.stderr = f"Patch file does not exist: {patch_path}"
            results.append(result)
            if stop_on_failure:
                break
            continue

        start = time.time()
        try:
            rc, stdout, stderr = apply_single_patch(
                patch_path, tree_path, patch_cmd, patch_env,
            )
            result.returncode = rc
            result.stdout = stdout
            result.stderr = stderr
            result.duration = time.time() - start

            if rc == 0:
                result.success = True
            else:
                result.success = False
                # Build a concise error summary
                err_lines = []
                for line in (stdout + "\n" + stderr).split("\n"):
                    line = line.strip()
                    if line:
                        err_lines.append(line)

                # Extract key diagnostic info
                if 'Reversed' in (stdout + stderr):
                    result.error_summary = (
                        "Reversed (or previously applied) patch"
                    )
                elif 'No such file or directory' in (stdout + stderr):
                    result.error_summary = (
                        "Target file not found — source tree may be incomplete"
                    )
                elif 'Hunk #' in (stdout + stderr) and 'FAILED' in (stdout + stderr):
                    # Count failed hunks
                    failed_hunks = sum(
                        1 for line in err_lines
                        if 'FAILED' in line and 'Hunk' in line
                    )
                    result.error_summary = f"{failed_hunks} hunk(s) FAILED"
                else:
                    # Show first meaningful error line
                    meaningful = [
                        l for l in err_lines
                        if 'error' in l.lower() or 'fail' in l.lower()
                           or 'cannot' in l.lower() or 'no such' in l.lower()
                    ]
                    result.error_summary = (
                        meaningful[0] if meaningful else err_lines[0]
                    ) if err_lines else f"Exit code {rc}"

                if stop_on_failure:
                    results.append(result)
                    break

        except subprocess.TimeoutExpired:
            result.success = False
            result.returncode = -1
            result.error_summary = "TIMEOUT (120s)"
            result.duration = time.time() - start
        except Exception as exc:
            result.success = False
            result.returncode = -1
            result.error_summary = f"EXCEPTION: {exc}"
            result.duration = time.time() - start

        results.append(result)

    return results


# ====================================================================
# Report formatting
# ====================================================================

def print_summary_table(results: List[PatchResult], duration: float):
    """Print a formatted summary table of all patch results."""
    passed = sum(1 for r in results if r.success)
    failed = sum(1 for r in results if not r.success)
    total = len(results)

    print()
    print("=" * 72)
    print("  PATCH APPLICATION RESULTS")
    print("=" * 72)

    if not results:
        print("  (no patches to apply)")
        print("=" * 72)
        return

    # Column widths
    idx_w = max(len(str(total)), 4)
    sep = "  "

    # Header
    hdr = f"{'#'.ljust(idx_w)}{sep}{'STATUS'.ljust(8)}{sep}{'DUR'.ljust(6)}{sep}PATCH"
    print(f"  {hdr}")
    print(f"  {'-' * len(hdr)}")

    # Rows
    for r in results:
        idx = f"[{r.index}/{r.total}]"
        status = "\u2705" if r.success else "\u274c"
        dur = f"{r.duration:.1f}s" if r.duration > 0 else ""
        patch_name = r.name
        print(f"  {idx:<{idx_w+len(str(r.total))+2}}{sep}{status:<8}{sep}{dur:<6}{sep}{patch_name}")

        # Show error summary for failures
        if not r.success and r.error_summary:
            print(f"  {' ' * (idx_w + len(str(r.total)) + 4)}{sep}{'':<8}{sep}{'':<6}{sep}\u2192 {r.error_summary}")
            # Show stdout/stderr excerpts (first 3 meaningful lines)
            err_text = r.stdout + "\n" + r.stderr
            shown = 0
            for line in err_text.split("\n"):
                line = line.strip()
                if line and shown < 3:
                    print(f"  {' ' * (idx_w + len(str(r.total)) + 4)}{sep}{'':<8}{sep}{'':<6}{sep}  {line[:100]}")
                    shown += 1
            if shown:
                print()

    print(f"  {'-' * len(hdr)}")
    print(f"  Total: {total}  |  Passed: {passed}  |  Failed: {failed}  |  Duration: {duration:.1f}s")
    print("=" * 72)
    print()


def print_file_summary(
    all_files: Set[str],
    file_to_patches: Dict[str, List[Tuple[Path, str]]],
    source_dir: Path,
    copied: int,
    missing: int,
    missing_files: List[str],
):
    """Print summary of files that would be patched."""
    print()
    print("=" * 72)
    print("  FILES TO BE PATCHED")
    print("=" * 72)
    print(f"  Source tree:  {source_dir}")
    print(f"  Total unique files: {len(all_files)}")
    print(f"  Files in source tree: {copied}")
    print(f"  Files missing from source tree: {missing}")
    print()

    if missing_files:
        print(f"  \u26a0  Missing files ({len(missing_files)}):")
        for f in missing_files[:20]:
            print(f"      \u274c {f} ")
        if len(missing_files) > 20:
            print(f"      ... and {len(missing_files) - 20} more")
        print()

    # Show files touched by multiple patches (potential conflicts)
    multi_patch_files = {f: patches for f, patches in file_to_patches.items() if len(patches) > 1}
    if multi_patch_files:
        print(f"  \u2139\ufe0f  Files touched by multiple patches ({len(multi_patch_files)}):")
        for f, patches in sorted(multi_patch_files.items()):
            patch_names = ", ".join(p[0].name for p in patches)
            print(f"      \u2192 {f}  ({patch_names})")
        print()


def print_overlay_summary(
    overwrite_count: int,
    new_count: int,
    overlay_files: List[str],
):
    """Print summary of overlay files."""
    if not overlay_files:
        return

    print()
    print("=" * 72)
    print("  OVERLAY FILES APPLIED")
    print("=" * 72)
    print(f"  Overwritten: {overwrite_count}  |  New: {new_count}  |  Total: {len(overlay_files)}")

    # Show new files (they might be needed by patches)
    if new_count > 0:
        print(f"  \u2139\ufe0f  New files created by overlay ({new_count}):")
        show = min(new_count, 10)
        for f in overlay_files[:show]:
            print(f"      + {f}")
        if new_count > 10:
            print(f"      ... and {new_count - 10} more new files")
    print()


# ====================================================================
# Pruning analysis (informational)
# ====================================================================

def analyze_pruning(prune_path: Path, label: str):
    """Analyze pruning list — informational only."""
    entries = _read_list_file(prune_path)
    if not entries:
        return

    print()
    print(f"  Pruning analysis: {label}")
    print(f"    File: {prune_path}")
    print(f"    Entries: {len(entries)}")
    for e in entries[:5]:
        print(f"      - {e}")
    if len(entries) > 5:
        print(f"      ... and {len(entries) - 5} more")
    print()


# ====================================================================
# JSON export
# ====================================================================

def export_json(results: List[PatchResult], output_path: Path):
    """Export patch results to a JSON file."""
    data = {
        "summary": {
            "total": len(results),
            "passed": sum(1 for r in results if r.success),
            "failed": sum(1 for r in results if not r.success),
        },
        "patches": [r.to_dict() for r in results],
    }
    output_path.write_text(json.dumps(data, indent=2), encoding=ENCODING)
    print(f"  Results exported to: {output_path}")


# ====================================================================
# Main
# ====================================================================

def main():
    parser = argparse.ArgumentParser(
        description="Simulate Thorium patching on a pristine Chromium source tree "
                    "without modifying the original. Copies affected files to a "
                    "working directory, applies all patches in build.py order, "
                    "and reports detailed results.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s                                    # full simulation (temp dir, auto-cleaned)
  %(prog)s --source-dir ../chromium           # use a different pristine source
  %(prog)s --work-dir D:/tmp/patch_test --keep-work-dir  # inspect work dir after
  %(prog)s --sequential                        # stop on first failure
  %(prog)s --export-json results.json          # export detailed results
        """,
    )
    parser.add_argument(
        "--source-dir", type=str, default=None,
        help=f"Path to pristine Chromium source tree (default: {DEFAULT_SOURCE_DIR})",
    )
    parser.add_argument(
        "--work-dir", type=str, default=None,
        help="Working directory for patching (default: auto-created temp directory)",
    )
    parser.add_argument(
        "--keep-work-dir", action="store_true",
        help="Do not delete the working directory after completion",
    )
    parser.add_argument(
        "--sequential", action="store_true",
        help="Apply patches sequentially and stop on first failure",
    )
    parser.add_argument(
        "--skip-overlay", action="store_true",
        help="Skip copying overlay files",
    )
    parser.add_argument(
        "--skip-pruning", action="store_true",
        help="Skip pruning list analysis",
    )
    parser.add_argument(
        "--export-json", type=str, default=None,
        help="Export detailed results to a JSON file",
    )

    args = parser.parse_args()

    # Resolve paths
    source_tree = Path(args.source_dir).resolve() if args.source_dir else DEFAULT_SOURCE_DIR.resolve()
    work_dir = Path(args.work_dir).resolve() if args.work_dir else None
    keep_work_dir = args.keep_work_dir or (args.work_dir is not None)

    # Find patch binary
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

    # Banner
    print("=" * 72)
    print("  THORIUM PATCHING SIMULATION (REFACTORED)")
    print("=" * 72)
    print(f"  Source tree:    {source_tree}")
    print(f"  Project root:   {ROOT_DIR}")
    print(f"  Patch binary:   {patch_cmd}")

    print(f"  Sequential:     {'yes' if args.sequential else 'no'}")
    version_file = ROOT_DIR / "chromium_version.txt"
    if version_file.exists():
        print(f"  Version:        {version_file.read_text(encoding=ENCODING).strip()}")
    print("=" * 72)

    # Verify source tree
    if not (source_tree / "BUILD.gn").exists():
        print(f"\n  \u274c Source tree missing BUILD.gn — not a valid Chromium checkout!")
        print(f"     Tried: {source_tree}")
        sys.exit(1)
    print(f"  \u2705 Source tree verified: {source_tree}")

    # Step 1: Discover patches
    print(f"\n{'='*72}")
    print("  STEP 1: DISCOVER PATCHES")
    print(f"{'='*72}")

    patches_info = discover_all_patches()
    if not patches_info:
        print("  \u26a0  No patches found. Check your series files.")
        sys.exit(0)

    ung_count = sum(1 for _, l in patches_info if l == 'ungoogled')
    th_count = sum(1 for _, l in patches_info if l == 'thorium')
    print(f"  Total patches: {len(patches_info)}  "
          f"(ungoogled: {ung_count}, thorium: {th_count})")

    # Step 2: Collect affected files
    print(f"\n{'='*72}")
    print("  STEP 2: COLLECT AFFECTED FILES")
    print(f"{'='*72}")

    all_files, file_to_patches = collect_all_affected_files(patches_info)
    print(f"  Unique files modified by patches: {len(all_files)}")

    # Step 3: Copy files to work directory
    print(f"\n{'='*72}")
    print("  STEP 3: COPY FILES TO WORK DIRECTORY")
    print(f"{'='*72}")

    temp_dir = None
    if work_dir is None:
        temp_dir = tempfile.mkdtemp(prefix="thorium-simulate-")
        work_dir = Path(temp_dir)
        print(f"  Created temp directory: {work_dir}")
    else:
        if work_dir.exists():
            shutil.rmtree(work_dir, ignore_errors=True)
            print(f"  Removed existing directory: {work_dir}")
        work_dir.mkdir(parents=True, exist_ok=True)
        print(f"  Using specified directory: {work_dir}")

    copied, missing, missing_files = copy_affected_files(source_tree, work_dir, all_files)
    print(f"  Files copied: {copied}")
    print(f"  Files missing (skipped): {missing}")
    if missing_files:
        print(f"\n  \u26a0  Missing files — patches affecting these will likely fail:")
        for f in missing_files[:15]:
            print(f"      \u274c {f} ")
        if len(missing_files) > 15:
            print(f"      ... and {len(missing_files) - 15} more")

    # Step 3b: Pruning analysis (informational)
    if not args.skip_pruning:
        print(f"\n{'='*72}")
        print("  STEP 3b: PRUNING ANALYSIS (INFORMATIONAL)")
        print(f"{'='*72}")
        analyze_pruning(
            UNGOOGLED_WINDOWS_DIR / "pruning.list",
            "ungoogled-chromium-windows",
        )
        analyze_pruning(
            ROOT_DIR / "pruning.list",
            "Thorium-specific",
        )
    else:
        print(f"\n  (pruning analysis skipped)")

    # Step 4: Apply ungoogled-chromium-windows patches
    all_results: List[PatchResult] = []

    ung_patches = [(p, l) for p, l in patches_info if l == 'ungoogled']
    if ung_patches:
        print(f"\n{'='*72}")
        print("  STEP 4: APPLY UNGOOGLED-CHROMIUM-WINDOWS PATCHES")
        print(f"{'='*72}")
        print(f"  Count: {len(ung_patches)} patches")

        results = apply_patches_with_details(
            ung_patches, work_dir, patch_cmd,
            stop_on_failure=args.sequential,
        )
        all_results.extend(results)

        # Check if we need to stop (sequential mode)
        if args.sequential and any(not r.success for r in results):
            print(f"\n  \u274c Stopping due to patch failure (--sequential mode)")
            _finish_and_exit(args, all_results, work_dir, keep_work_dir, temp_dir)

    # Step 5: Apply overlay files
    if not args.skip_overlay:
        print(f"\n{'='*72}")
        print("  STEP 5: APPLY OVERLAY FILES")
        print(f"{'='*72}")

        overwritten, new_files, overlay_file_list = copy_overlay_files(OVERLAY_DIR, work_dir)
        print(f"  Overwritten: {overwritten}  |  New: {new_files}  |  Total: {len(overlay_file_list)}")
    else:
        print(f"\n  (overlay skipped)")
        overwritten, new_files, overlay_file_list = 0, 0, []

    # Step 6: Apply Thorium patches
    th_patches = [(p, l) for p, l in patches_info if l == 'thorium']
    if th_patches:
        print(f"\n{'='*72}")
        print("  STEP 6: APPLY THORIUM PATCHES")
        print(f"{'='*72}")
        print(f"  Count: {len(th_patches)} patches")

        results = apply_patches_with_details(
            th_patches, work_dir, patch_cmd,
            stop_on_failure=args.sequential,
        )
        all_results.extend(results)

    # Step 7: Summary & export
    total_duration = sum(r.duration for r in all_results)
    print_summary_table(all_results, total_duration)
    print_file_summary(all_files, file_to_patches, source_tree, copied, missing, missing_files)
    print_overlay_summary(overwritten, new_files, overlay_file_list)

    # Export JSON
    if args.export_json:
        export_path = Path(args.export_json).resolve()
        if export_path.exists():
            export_path.unlink()
            print(f"  Removed existing export file: {export_path}")
        export_json(all_results, export_path)

    # Finish
    failed_count = sum(1 for r in all_results if not r.success)

    if keep_work_dir:
        print(f"  Work directory preserved: {work_dir}")
    else:
        if temp_dir and Path(temp_dir).exists():
            shutil.rmtree(temp_dir, ignore_errors=True)
            print(f"  Temp directory deleted.")

    if failed_count == 0:
        print(f"\n  \u2728 ALL {len(all_results)} PATCH(ES) APPLIED SUCCESSFULLY!")
    else:
        print(f"\n  \u274c {failed_count}/{len(all_results)} PATCH(ES) FAILED — review details above")
        sys.exit(1)


def _finish_and_exit(args, all_results, work_dir, keep_work_dir, temp_dir):
    """Print partial results and exit when stopped early."""
    total_duration = sum(r.duration for r in all_results)
    print_summary_table(all_results, total_duration)
    print(f"\n  \u26a0  Simulation stopped early (--sequential mode)")

    if args.export_json and all_results:
        export_path = Path(args.export_json).resolve()
        if export_path.exists():
            export_path.unlink()
            print(f"  Removed existing export file: {export_path}")
        export_json(all_results, export_path)

    if keep_work_dir:
        print(f"  Work directory preserved: {work_dir}")
    elif temp_dir and Path(temp_dir).exists():
        shutil.rmtree(temp_dir, ignore_errors=True)

    sys.exit(1)


if __name__ == "__main__":
    main()
