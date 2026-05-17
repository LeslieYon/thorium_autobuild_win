#!/usr/bin/env python3
"""
Create a Thorium patch from uncommitted changes in build/src/.

Takes one or more file paths (relative to build/src/), generates unified diffs
via git, classifies each patch into the appropriate category (fixes/config/ui/…),
writes the patch file to patches/thorium/<category>/, and appends entries to
patches/series.

Use this during development when you have modified a file in build/src/ and
want to create a proper Thorium patch without manual file-naming and series
bookkeeping.

Usage (from project root):
    python devutils/make_patch.py chrome/browser/foo.cc
    python devutils/make_patch.py chrome/browser/foo.cc content/bar.cc
    python devutils/make_patch.py --category media third_party/libjxl/BUILD.gn
    python devutils/make_patch.py --dry-run chrome/browser/foo.cc
"""

from __future__ import annotations

import argparse
import hashlib
import os
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
# Helpers
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


def record_in_series(series_file: Path, category: str, patch_name: str) -> bool:
    """Append patch entry to series file if not already present."""
    entry = f"thorium/{category}/{patch_name}"
    if series_file.exists():
        existing = series_file.read_text(encoding="utf-8").splitlines()
        # Also check for comment-annotated entries, but match on the path part
        if any(entry in line for line in existing):
            return False  # already present
        # Ensure file ends with newline before appending
        raw = series_file.read_bytes()
        if raw and raw[-1] != ord("\n"):
            series_file.write_bytes(raw + b"\n")
    with series_file.open("a", encoding="utf-8", newline="\n") as f:
        f.write(entry + "\n")
    return True


def get_git_diff(
    git_cmd: str, src_dir: Path, rel_path: str
) -> str | None:
    """Return unified diff for a single file, or None if unchanged."""
    result = subprocess.run(
        [git_cmd, "diff", "--no-color", "--", rel_path],
        cwd=src_dir,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        print(f"  ERROR: git diff failed for {rel_path}:\n{result.stderr}", file=sys.stderr)
        return None
    diff = result.stdout
    if not diff.strip():
        return None
    # Strip the "diff --git" and "index" lines — they contain unstable hashes.
    lines = diff.splitlines(keepends=True)
    cleaned = []
    skip_header = True
    for line in lines:
        if skip_header and (line.startswith("diff --git") or line.startswith("index ")):
            continue
        if skip_header and (line.startswith("--- ") or line.startswith("+++ ")):
            skip_header = False
            # fall through — keep these lines
        cleaned.append(line)
    return "".join(cleaned)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("files", nargs="+", metavar="PATH",
                        help="File path(s) relative to build/src/")
    parser.add_argument("--category", "-c", default=None,
                        help="Force a specific category instead of auto-detecting")
    parser.add_argument("--no-series", action="store_true",
                        help="Skip adding the patch entry to patches/series")
    parser.add_argument("--dry-run", "-n", action="store_true",
                        help="Show what would be done without writing anything")
    parser.add_argument("--src-dir", type=Path, default=None,
                        help="Path to build/src/ (default: <root>/build/src)")
    parser.add_argument("--git-cmd", default=None,
                        help="Path to git executable")
    return parser.parse_args(argv)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)

    root = find_project_root()
    git_cmd = args.git_cmd or find_git()
    src_dir = (args.src_dir or root / "build" / "src").resolve()
    patches_root = root / "patches" / "thorium"
    series_file = root / "patches" / "series"

    if not src_dir.is_dir():
        print(f"ERROR: Source directory not found: {src_dir}", file=sys.stderr)
        return 1

    if not (src_dir / ".git").is_dir():
        print(f"ERROR: {src_dir} is not a git repository.", file=sys.stderr)
        return 1

    patches_root.mkdir(parents=True, exist_ok=True)

    print(f"Project root : {root}")
    print(f"Source dir   : {src_dir}")
    print(f"Git          : {git_cmd}")
    print(f"Series file  : {series_file}")
    print()

    exit_status = 0

    for rel_path_str in args.files:
        # Normalize path separators
        rel_path_str = rel_path_str.replace("\\", "/")
        abs_path = (src_dir / rel_path_str).resolve()

        print(f"  [{rel_path_str}]")

        if not abs_path.exists():
            print(f"    SKIP: file not found in source tree")
            continue

        # Get the diff
        diff = get_git_diff(git_cmd, src_dir, rel_path_str)
        if diff is None:
            print(f"    SKIP: no uncommitted changes (or git diff failed)")
            continue

        # Determine category
        category = args.category or category_for(rel_path_str)
        patch_name = slug_for(rel_path_str)
        patch_dir = patches_root / category
        patch_path = patch_dir / patch_name

        print(f"    Category  : {category}")
        print(f"    Patch file: {patch_path.relative_to(root)}")
        print(f"    Size      : {len(diff)} bytes")

        if args.dry_run:
            print(f"    (dry-run, not written)")
            continue

        # Write the patch file
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

    return exit_status


if __name__ == "__main__":
    raise SystemExit(main())
