#!/usr/bin/env python3
"""
Create Thorium patches using git diff --no-index.

Implements the standard patch creation workflow:
  1. Backup original files before editing (--backup)
  2. Ensure CRLF consistency between old and new files
  3. Run git diff --no-index to generate unified diff
  4. Strip diff --git and index header lines
  5. Rewrite paths to be chromium-source-relative (--- a/... and +++ b/...)
  6. Concatenate patches for multiple files
  7. Ensure trailing newline at end of patch file

Usage:
    # Phase 1 — Backup current files before editing
    python devutils/make_patch.py --backup chrome/browser/foo.cc

    # Phase 2 — Generate patch by comparing modified vs original
    python devutils/make_patch.py chrome/browser/foo.cc

    # Point to a specific clean Chromium tree
    python devutils/make_patch.py --old-dir ../chromium chrome/browser/foo.cc

    # Multiple files (concatenated into one patch)
    python devutils/make_patch.py chrome/browser/foo.cc content/bar.cc

    # Write to a specific output file
    python devutils/make_patch.py --output my.patch chrome/browser/foo.cc

    # Force a category (default: auto-detect from path)
    python devutils/make_patch.py --category media third_party/libjxl/BUILD.gn
"""

from __future__ import annotations

import argparse
import hashlib
import subprocess
import sys
from pathlib import Path


# ---------------------------------------------------------------------------
# Category rules — must match batch_generate_patches.py
# ---------------------------------------------------------------------------
CATEGORY_RULES = (
    ("build/config/", "compiler"),
    ("third_party/ffmpeg/", "media"),
    ("media/", "media"),
    ("ui/", "ui"),
    ("chrome/browser/ui/", "ui"),
    ("chrome/app/theme/", "branding"),
    ("chrome/app/vector_icons/", "branding"),
    ("chrome/app/thorium", "branding"),
    ("components/search_engines/", "search"),
    ("chrome/browser/search", "search"),
    ("components/privacy_sandbox/", "privacy"),
    ("chrome/browser/privacy_sandbox/", "privacy"),
    ("chrome/installer/win/", "windows"),
    ("chrome/installer/mini_installer/", "windows"),
    ("sandbox/win/", "windows"),
    ("build/win/", "windows"),
    ("net/", "features"),
    ("content/", "features"),
    ("extensions/", "features"),
    ("third_party/libjxl/", "media"),
    ("third_party/highway/", "media"),
)

# ---------------------------------------------------------------------------
# Path helpers
# ---------------------------------------------------------------------------

def find_project_root() -> Path:
    """Walk up from cwd to find the project root (contains patches/ and build.py)."""
    cwd = Path.cwd().resolve()
    for ancestor in [cwd] + list(cwd.parents):
        if (ancestor / "build.py").exists() and (ancestor / "patches").exists():
            return ancestor
    print("ERROR: Could not find project root (no build.py or patches/ in ancestors).",
          file=sys.stderr)
    print("       Run this script from the thorium_autobuild_win/ directory.",
          file=sys.stderr)
    sys.exit(1)


def find_git() -> str:
    """Locate a usable git executable."""
    candidates = [
        "git",
        r"E:\Tools_Team\SmartGit\git\cmd\git.exe",
        r"C:\Program Files\Git\bin\git.exe",
    ]
    for candidate in candidates:
        try:
            subprocess.run([candidate, "--version"], capture_output=True, check=True)
            return candidate
        except (subprocess.CalledProcessError, FileNotFoundError):
            continue
    print("ERROR: Could not find git. Set GIT_CMD or install Git.", file=sys.stderr)
    sys.exit(1)


def category_for(rel_path: str) -> str:
    """Determine patch category from file path."""
    if rel_path.endswith((".grd", ".grdp")):
        return "branding"
    for prefix, cat in CATEGORY_RULES:
        if rel_path.startswith(prefix):
            return cat
    if rel_path.endswith((".gn", ".gni")) or rel_path.startswith("build/"):
        return "config"
    return "fixes"


def slug_for(rel_path: str) -> str:
    """Convert a source-relative path to a patch filename.

    E.g. 'chrome/browser/foo.cc' → 'chrome__browser__foo.cc.patch'
    """
    stem = rel_path.replace("/", "__").replace(" ", "_")
    stem = "".join(ch if ch.isalnum() or ch in "._-" else "-" for ch in stem)
    if len(stem) > 140:
        digest = hashlib.sha1(rel_path.encode("utf-8")).hexdigest()[:10]
        stem = stem[:120] + "__" + digest
    return stem + ".patch"


# ---------------------------------------------------------------------------
# Step 3 — CRLF consistency
# ---------------------------------------------------------------------------

def ensure_crlf(file_path: Path) -> None:
    """Ensure a text file uses CRLF line endings (Step 3).

    Skips binary files (detected by null byte). Converts lone LF to CRLF;
    normalises mixed line endings to CRLF.
    """
    try:
        data = file_path.read_bytes()
    except OSError as e:
        print(f"  WARNING: Cannot read {file_path}: {e}", file=sys.stderr)
        return

    # Skip binary files
    if b"\x00" in data:
        return

    old_len = len(data)

    # Normalise: first collapse any CRLF to LF, then convert all LF to CRLF.
    # This handles all cases: pure LF, pure CRLF, mixed.
    normalised = data.replace(b"\r\n", b"\n").replace(b"\n", b"\r\n")

    if normalised != data:
        file_path.write_bytes(normalised)
        print(f"  CRLF: Normalised \u2192 CRLF ({file_path.name})")


# ---------------------------------------------------------------------------
# Steps 4-6 — git diff --no-index + header strip + path rewrite
# ---------------------------------------------------------------------------

def make_diff(
    git_cmd: str,
    old_file: Path,
    new_file: Path,
    rel_path: str,
) -> str | None:
    """Run git diff --no-index and return cleaned patch content.

    Steps implemented:
      4. Run git diff --no-index oldfile newfile
      5. Delete first two lines (diff --git / index)
      6. Rewrite paths to chromium-source-relative (--- a/<rel> / +++ b/<rel>)
    """
    result = subprocess.run(
        [git_cmd, "diff", "--no-color", "--no-index", str(old_file), str(new_file)],
        capture_output=True,
        text=True,
    )

    # Exit code 1 = differences found (normal), 0 = identical, other = error
    if result.returncode not in (0, 1):
        print(f"  ERROR: git diff failed (exit {result.returncode}):\n{result.stderr}",
              file=sys.stderr)
        return None

    stdout = result.stdout
    if not stdout.strip():
        return None  # No differences

    # Process line by line: strip header, fix paths
    lines = stdout.splitlines(keepends=True)
    cleaned: list[str] = []
    in_header = True

    for line in lines:
        # Step 5: Strip "diff --git" and "index" lines
        if in_header and (line.startswith("diff --git") or line.startswith("index ")):
            continue
        # Step 6: Rewrite paths in --- / +++ lines
        if line.startswith("--- "):
            in_header = False
            cleaned.append(f"--- a/{rel_path}\n")
        elif line.startswith("+++ "):
            cleaned.append(f"+++ b/{rel_path}\n")
        else:
            if in_header:
                # Still in header but past ---/+++, so stop skipping
                in_header = False
            cleaned.append(line)

    return "".join(cleaned)


# ---------------------------------------------------------------------------
# Series file management
# ---------------------------------------------------------------------------

def record_in_series(series_file: Path, category: str, patch_name: str) -> bool:
    """Append patch entry to series file if not already present."""
    entry = f"thorium/{category}/{patch_name}"
    if series_file.exists():
        existing = series_file.read_text(encoding="utf-8").splitlines()
        if any(entry in line for line in existing):
            return False  # already present
        # Ensure file ends with newline before appending
        raw = series_file.read_bytes()
        if raw and raw[-1] != ord("\n"):
            series_file.write_bytes(raw + b"\n")
    with series_file.open("a", encoding="utf-8", newline="\n") as f:
        f.write(entry + "\n")
    return True


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("files", nargs="+", metavar="PATH",
                        help="File path(s) relative to chromium source tree")
    parser.add_argument("--old-dir", type=Path, default=None,
                        help="Directory with original (clean) files "
                             "(default: <project_root>/../chromium/)")
    parser.add_argument("--new-dir", type=Path, default=None,
                        help="Directory with modified files "
                             "(default: <project_root>/build/src/)")
    parser.add_argument("--backup", action="store_true",
                        help="Step 1: copy files from new-dir to old-dir "
                             "(use before editing, then run without --backup)")
    parser.add_argument("--output", "-o", type=Path, default=None,
                        help="Write concatenated patch to this file "
                             "(default: auto-generated path)")
    parser.add_argument("--category", "-c", default=None,
                        help="Force a specific category instead of auto-detecting")
    parser.add_argument("--no-series", action="store_true",
                        help="Skip adding the patch entry to patches/series")
    parser.add_argument("--dry-run", "-n", action="store_true",
                        help="Show what would be done without writing anything")
    parser.add_argument("--git-cmd", default=None,
                        help="Path to git executable (default: auto-detect)")
    return parser.parse_args(argv)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)

    root = find_project_root()
    git_cmd = args.git_cmd or find_git()

    # Resolve directories — default old-dir to ../chromium/, new-dir to build/src/
    new_dir = (args.new_dir or root / "build" / "src").resolve()
    old_dir = (args.old_dir or root.parent / "chromium").resolve()

    patches_root = root / "patches" / "thorium"
    series_file = root / "patches" / "series"

    print(f"Project root : {root}")
    print(f"Old dir      : {old_dir}")
    print(f"New dir      : {new_dir}")
    print(f"Git          : {git_cmd}")
    if not args.backup:
        print(f"Series file  : {series_file}")
    print()

    # -----------------------------------------------------------------------
    # --backup mode: Step 1 — Copy files from new-dir to old-dir
    # -----------------------------------------------------------------------
    if args.backup:
        print("=== Step 1: Backup files before editing ===\n")
        for rel_path_str in args.files:
            rel_path_str = rel_path_str.replace("\\", "/")
            src = (new_dir / rel_path_str).resolve()
            dst = (old_dir / rel_path_str).resolve()

            if not src.exists():
                print(f"  ERROR: File not found: {src}", file=sys.stderr)
                continue

            if args.dry_run:
                print(f"  [dry-run] Would copy: {rel_path_str}")
                continue

            dst.parent.mkdir(parents=True, exist_ok=True)
            dst.write_bytes(src.read_bytes())
            print(f"  BACKUP: {rel_path_str}")
            print(f"    From: {src}")
            print(f"    To:   {dst}")

            # Step 3 — ensure CRLF on both copies
            ensure_crlf(dst)
            ensure_crlf(src)
            print()

        print("Done. Now edit your files in the new-dir, "
              "then run this script without --backup to generate the patch.")
        return 0

    # -----------------------------------------------------------------------
    # Normal (make) mode: Steps 3-7
    # -----------------------------------------------------------------------
    print("=== Generate patch(es) via git diff --no-index ===\n")

    if not new_dir.is_dir():
        print(f"ERROR: New (modified) directory not found: {new_dir}", file=sys.stderr)
        return 1
    if not old_dir.is_dir():
        print(f"ERROR: Old (original) directory not found: {old_dir}", file=sys.stderr)
        return 1

    all_patches: list[str] = []
    exit_status = 0

    for rel_path_str in args.files:
        rel_path_str = rel_path_str.replace("\\", "/")
        old_file = (old_dir / rel_path_str).resolve()
        new_file = (new_dir / rel_path_str).resolve()

        print(f"  [{rel_path_str}]")

        if not old_file.exists():
            print(f"    ERROR: Original file not found: {old_file}", file=sys.stderr)
            exit_status = 1
            continue
        if not new_file.exists():
            print(f"    ERROR: Modified file not found: {new_file}", file=sys.stderr)
            exit_status = 1
            continue

        # Step 3: Ensure CRLF consistency
        if not args.dry_run:
            ensure_crlf(old_file)
            ensure_crlf(new_file)

        # Steps 4-6: git diff --no-index + strip header + fix paths
        diff = make_diff(git_cmd, old_file, new_file, rel_path_str)
        if diff is None:
            print(f"    SKIP: files are identical (or git diff failed)")
            continue

        print(f"    Diff size: {len(diff)} bytes")

        if args.dry_run:
            print(f"    (dry-run, patch preview below)")
            print(f"    {'\u2500' * 60}")
            for line in diff.splitlines():
                print(f"    {line}")
            print(f"    {'\u2500' * 60}")
            print()
            continue

        # Collect for concatenation
        all_patches.append(diff)

        # Determine category and patch name (for series / output file)
        category = args.category or category_for(rel_path_str)
        patch_name = slug_for(rel_path_str)
        patch_dir = patches_root / category
        patch_path = patch_dir / patch_name

        print(f"    Category   : {category}")
        print(f"    Patch file : {patch_path.relative_to(root)}")

        # Write individual patch file
        patch_dir.mkdir(parents=True, exist_ok=True)
        patch_path.write_text(diff, encoding="utf-8", newline="\n")
        print(f"    Written to {patch_path.relative_to(root)}")

        # Update series file
        if args.no_series:
            print(f"    (series update skipped)")
        else:
            added = record_in_series(series_file, category, patch_name)
            if added:
                print(f"    Added to {series_file.relative_to(root)}")
            else:
                print(f"    Already in series, skipping")

        print()

    # -----------------------------------------------------------------------
    # Step 7 — Concatenate all patches and write output
    # -----------------------------------------------------------------------
    if not all_patches:
        print("No patches generated.")
        return exit_status

    if len(all_patches) > 1:
        combined = "\n".join(all_patches)
    else:
        combined = all_patches[0]

    # Ensure trailing newline (as required by patch tool)
    if not combined.endswith("\n"):
        combined += "\n"

    # Write output file if requested
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(combined, encoding="utf-8", newline="\n")
        print(f"Combined patch written to: {args.output}")
        print(f"  Total size: {len(combined)} bytes ({len(all_patches)} file(s))")

    return exit_status


if __name__ == "__main__":
    raise SystemExit(main())
