#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Thorium All-Patches Application Script
=======================================

Applies ALL patches (ungoogled-chromium-windows + Thorium) to the build/src
directory, following build.py's patch application flow exactly.

Key differences from build.py:
1. Uses `git apply` instead of `patch.exe` — avoids MSYS2 /tmp/ permission
   issues on Windows when patch.exe is invoked from Python subprocess.
2. IDEMPOTENT — detects already-applied patches via `git apply --reverse --check`
   and skips them automatically.
3. DETAILED ERROR LOGGING — records full stdout/stderr for every failure.

Usage:
  python devutils/apply_all_patches.py
  python devutils/apply_all_patches.py --source-dir build/src
  python devutils/apply_all_patches.py --log-file apply_results.log
  python devutils/apply_all_patches.py --only-thorium
  python devutils/apply_all_patches.py --only-external
"""

import sys
import os
import subprocess
import time
import argparse
import shutil
import re
from pathlib import Path
from datetime import datetime
from typing import Optional, Tuple, List

# ====================================================================
# Configuration
# ====================================================================
ROOT_DIR = Path(__file__).resolve().parent.parent
DEFAULT_SOURCE_DIR = ROOT_DIR / "build" / "src"
DEFAULT_LOG_FILE = ROOT_DIR / "devutils" / "apply_all_patches.log"

# Submodule paths
UNGOOGLED_WINDOWS_DIR = ROOT_DIR / "ungoogled-chromium-windows"
UNGOOGLED_CHROMIUM_DIR = UNGOOGLED_WINDOWS_DIR / "ungoogled-chromium"
UNGOOGLED_PATCH_DIR = UNGOOGLED_WINDOWS_DIR / "patches"
UNGOOGLED_MAIN_PATCH_DIR = UNGOOGLED_CHROMIUM_DIR / "patches"
THORIUM_PATCH_BASE = ROOT_DIR / "patches"

# Series files
THORIUM_SERIES_FILE = ROOT_DIR / "patches" / "series"
EXTERNAL_SERIES_FILE = ROOT_DIR / "patches" / "series.external"

# Overlay directory (copied verbatim over the source tree)
OVERLAY_DIR = ROOT_DIR / "overlay"

# Where to find git.exe
_PORTABLE_GIT_DIR = ROOT_DIR.parent / "PortableGit"
_PORTABLE_GIT_CMD = _PORTABLE_GIT_DIR / "cmd" / "git.exe"
_CHROMIUM_GIT_CMD = DEFAULT_SOURCE_DIR / "third_party" / "git" / "cmd" / "git.exe"

# ANSI color codes
_GREEN = "\033[92m"
_YELLOW = "\033[93m"
_RED = "\033[91m"
_CYAN = "\033[96m"
_BOLD = "\033[1m"
_DIM = "\033[2m"
_RESET = "\033[0m"


# ====================================================================
# Logging
# ====================================================================

class Logger:
    """Writes to both console and log file."""

    def __init__(self, log_path: Optional[Path] = None, verbose: bool = False):
        self.log_path = log_path
        self.verbose = verbose
        if log_path:
            log_path.parent.mkdir(parents=True, exist_ok=True)
            header = [
                "=" * 72,
                "Thorium All-Patches Application Log",
                f"Started: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
                "=" * 72,
            ]
            log_path.write_text("\n".join(header) + "\n", encoding="utf-8")

    @staticmethod
    def _strip_ansi(text: str) -> str:
        """Remove ANSI escape sequences (color codes) from text."""
        return re.sub(r"\033\[[0-9;]*m", "", text)

    def _write(self, msg: str, console: bool = True):
        timestamp = datetime.now().strftime("%H:%M:%S")
        line = f"[{timestamp}] {msg}"
        if self.log_path:
            plain = self._strip_ansi(line)
            with open(self.log_path, "a", encoding="utf-8") as f:
                f.write(plain + "\n")
        if console:
            print(line)

    def info(self, msg: str):
        self._write(msg)

    def success(self, msg: str):
        self._write(f"{_GREEN}{msg}{_RESET}")

    def warning(self, msg: str):
        self._write(f"{_YELLOW}WARNING: {msg}{_RESET}")

    def error(self, msg: str):
        self._write(f"{_RED}ERROR: {msg}{_RESET}")

    def detail(self, msg: str):
        if self.verbose or not self.log_path:
            self._write(f"  {_DIM}{msg}{_RESET}")
        elif self.log_path:
            plain = self._strip_ansi(f"  {msg}")
            with open(self.log_path, "a", encoding="utf-8") as f:
                f.write(plain + "\n")

    def section(self, msg: str):
        colored = f"\n{_CYAN}{_BOLD}{'=' * 60}{_RESET}"
        self._write(colored)
        self._write(f"{_CYAN}{_BOLD}{msg}{_RESET}")
        self._write(colored)

    def flush_output(self, header: str, stdout: str, stderr: str):
        """Write full command output to log file."""
        if not self.log_path:
            return
        with open(self.log_path, "a", encoding="utf-8") as f:
            f.write(f"\n--- {header} ---\n")
            if stdout.strip():
                f.write(f"[stdout]\n{stdout}\n")
            if stderr.strip():
                f.write(f"[stderr]\n{stderr}\n")
            f.write("--- end ---\n")


# ====================================================================
# Git binary discovery
# ====================================================================

def find_git(logger: Logger) -> Optional[Path]:
    """Find a working git.exe.

    Order:
    1. GIT_CMD environment variable
    2. PortableGit/cmd/git.exe
    3. Source tree's third_party/git/cmd/git.exe
    4. System PATH
    """
    env_git = os.environ.get("GIT_CMD")
    if env_git:
        p = Path(env_git)
        if p.exists():
            logger.info(f"  Using GIT_CMD: {p}")
            return p
        which = shutil.which(env_git)
        if which:
            logger.info(f"  Using GIT_CMD (resolved): {which}")
            return Path(which)

    if _PORTABLE_GIT_CMD.exists():
        logger.info(f"  Using PortableGit git: {_PORTABLE_GIT_CMD}")
        return _PORTABLE_GIT_CMD

    if _CHROMIUM_GIT_CMD.exists():
        logger.info(f"  Using Chromium-bundled git: {_CHROMIUM_GIT_CMD}")
        return _CHROMIUM_GIT_CMD

    which = shutil.which("git")
    if which:
        logger.info(f"  Using system git: {which}")
        return Path(which)

    logger.error("Could not find git.exe anywhere!")
    return None


# ====================================================================
# Series file parsing
# ====================================================================

def read_list_file(filepath: Path) -> List[str]:
    """Read line-oriented config, ignoring blanks and # comments."""
    if not filepath.exists():
        return []
    entries = []
    for line in filepath.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            entries.append(line)
    return entries


def parse_external_series(series_path: Path) -> List[Tuple[Path, str]]:
    """Parse external series with section markers.

    #[windows]  -> UNGOOGLED_PATCH_DIR
    #[main]     -> UNGOOGLED_MAIN_PATCH_DIR
    #[cromite]  -> <root>/cromite/build/patches/
    """
    base_dirs = {
        "windows": UNGOOGLED_PATCH_DIR,
        "main": UNGOOGLED_MAIN_PATCH_DIR,
        "cromite": ROOT_DIR / "cromite" / "build" / "patches",
    }
    patches: List[Tuple[Path, str]] = []
    if not series_path.exists():
        return patches

    current_base = base_dirs["windows"]
    with open(series_path, "r", encoding="utf-8") as f:
        for line in f:
            stripped = line.strip()
            if not stripped:
                continue
            if stripped.startswith("#"):
                m = re.match(r"^#\[(\w+)\]$", stripped)
                if m and m.group(1) in base_dirs:
                    current_base = base_dirs[m.group(1)]
                continue
            resolved = (current_base / stripped).resolve()
            patches.append((resolved, stripped))
    return patches


def parse_thorium_series(series_path: Path) -> List[Tuple[Path, str]]:
    """Parse Thorium series -- entries relative to patches/ dir."""
    patches: List[Tuple[Path, str]] = []
    if not series_path.exists():
        return patches
    for entry in read_list_file(series_path):
        resolved = (THORIUM_PATCH_BASE / entry).resolve()
        patches.append((resolved, entry))
    return patches


# ====================================================================
# Core: git apply logic
# ====================================================================

def _git_apply_check(
    git_bin: Path,
    patch_path: Path,
    source_tree: Path,
    reverse: bool = False,
    timeout: int = 120,
) -> subprocess.CompletedProcess:
    """Run `git apply --check` on a patch.

    Args:
        reverse: If True, add --reverse (detects already-applied patches).
    """
    cmd = [
        str(git_bin), "apply", "--check", "--ignore-whitespace", "-p1",
        str(patch_path),
    ]
    if reverse:
        cmd.insert(2, "--reverse")
    return subprocess.run(
        cmd,
        cwd=str(source_tree),
        capture_output=True, text=True, timeout=timeout,
    )


def _git_apply_real(
    git_bin: Path,
    patch_path: Path,
    source_tree: Path,
    timeout: int = 120,
) -> subprocess.CompletedProcess:
    """Run `git apply` (for real) on a patch."""
    cmd = [
        str(git_bin), "apply", "--ignore-whitespace", "-p1",
        str(patch_path),
    ]
    return subprocess.run(
        cmd,
        cwd=str(source_tree),
        capture_output=True, text=True, timeout=timeout,
    )


# ====================================================================
# Patch application (single + batch)
# ====================================================================

def _extract_failed_files(stderr: str) -> List[str]:
    """Extract unique relative file paths from git apply --check stderr.

    Git apply outputs lines like:
      error: patch failed: chrome/browser/BUILD.gn:3752
      error: chrome/browser/BUILD.gn: patch does not apply

    Returns sorted unique file paths (Unix separators).
    """
    paths: List[str] = []
    for m in re.finditer(r"error: (?:patch failed: )?(.+?):\d+", stderr):
        paths.append(m.group(1))
    # Also match lines without line numbers
    for m in re.finditer(r"error: (.+?): patch does not apply", stderr):
        paths.append(m.group(1))
    return sorted(set(paths))


def apply_single_patch(
    patch_path: Path,
    patch_entry: str,
    source_tree: Path,
    git_bin: Path,
    logger: Logger,
    index: int,
    total: int,
) -> Tuple[bool, bool, str, List[str]]:
    """Apply a single patch via git apply.

    Strategy:
    1. `git apply --check` (forward)  -> RC 0 = clean, apply for real
    2. `git apply --check --reverse`  -> RC 0 = already applied, skip
    3. Both fail                      -> genuine error, log details

    Returns:
        (applied, skipped_already_applied, error_msg, failed_files)
    """
    prefix = f"[{index}/{total}]"
    short_name = patch_path.name

    # --- Step 1: Check if patch applies cleanly (forward) ---
    try:
        fwd = _git_apply_check(git_bin, patch_path, source_tree, reverse=False)
    except subprocess.TimeoutExpired:
        logger.error(f"{prefix} {short_name} -- forward check timed out (120s)")
        return False, False, "forward check timed out", []
    except Exception as exc:
        logger.error(f"{prefix} {short_name} -- forward check crashed: {exc}")
        return False, False, f"forward check crashed: {exc}", []

    if fwd.returncode == 0:
        # Patch applies cleanly -> apply for real
        try:
            real = _git_apply_real(git_bin, patch_path, source_tree)
        except subprocess.TimeoutExpired:
            logger.error(f"{prefix} {short_name} -- apply timed out (120s)")
            return False, False, "apply timed out", []
        except Exception as exc:
            logger.error(f"{prefix} {short_name} -- apply crashed: {exc}")
            return False, False, f"apply crashed: {exc}", []

        if real.returncode == 0:
            logger.success(f"  {_GREEN}✅{_RESET} {prefix} {short_name}")
            return True, False, "", []
        else:
            combined = (real.stdout + "\n" + real.stderr).strip()
            failed_files = _extract_failed_files(real.stderr)
            logger.error(f"{prefix} {short_name} -- APPLY FAILED (check passed!)")
            logger.detail(f"  (entry: {patch_entry})")
            logger.flush_output(f"APPLY FAILED: {short_name}", real.stdout, real.stderr)
            return False, False, combined, failed_files

    # --- Step 2: Forward check failed -- try reverse check ---
    try:
        rev = _git_apply_check(git_bin, patch_path, source_tree, reverse=True)
    except subprocess.TimeoutExpired:
        logger.error(f"{prefix} {short_name} -- reverse check timed out (120s)")
        return False, False, "reverse check timed out", []
    except Exception as exc:
        logger.error(f"{prefix} {short_name} -- reverse check crashed: {exc}")
        return False, False, f"reverse check crashed: {exc}", []

    if rev.returncode == 0:
        # Reverse applies cleanly -> patch is already applied
        logger.info(f"  {_YELLOW}⏭{_RESET} {prefix} {short_name} -- already applied, skipped")
        logger.detail(f"  (entry: {patch_entry})")
        return False, True, "", []

    # --- Step 3: Both checks failed -> genuine error ---
    combined_out = (fwd.stdout + "\n" + fwd.stderr).strip()
    failed_files = _extract_failed_files(fwd.stderr)
    logger.error(f"{prefix} {short_name} -- FAILED (forward+reverse both fail)")
    logger.detail(f"  (entry: {patch_entry})")
    if failed_files:
        logger.detail(f"  affected files: {len(failed_files)}")
        for ff in failed_files:
            logger.detail(f"    - {ff}")
    logger.flush_output(f"FAILED: {short_name} [forward check]", fwd.stdout, fwd.stderr)
    logger.flush_output(f"FAILED: {short_name} [reverse check]", rev.stdout, rev.stderr)
    return False, False, combined_out, failed_files


def apply_patch_set(
    label: str,
    patches: List[Tuple[Path, str]],
    source_tree: Path,
    git_bin: Path,
    logger: Logger,
) -> dict:
    """Apply a list of patches. Returns stats dict with 'failed_files'."""
    total = len(patches)
    if total == 0:
        logger.warning(f"No patches to apply for {label}")
        return {"total": 0, "applied": 0, "skipped": 0, "errors": 0, "failed_files": []}

    logger.section(f"Applying {label} patches ({total} total)")
    stats = {"total": total, "applied": 0, "skipped": 0, "errors": 0, "failed_files": []}

    for i, (patch_path, patch_entry) in enumerate(patches, 1):
        if not patch_path.exists():
            stats["errors"] += 1
            logger.error(f"  [{i}/{total}] NOT FOUND: {patch_path.name}")
            logger.detail(f"  (entry: {patch_entry})")
            continue

        applied, skipped, _, failed_files = apply_single_patch(
            patch_path, patch_entry, source_tree, git_bin,
            logger, i, total,
        )
        if applied:
            stats["applied"] += 1
        elif skipped:
            stats["skipped"] += 1
        else:
            stats["errors"] += 1
            stats["failed_files"].extend(failed_files)

    pct = (stats["applied"] / stats["total"] * 100) if stats["total"] > 0 else 0
    logger.info(
        f"  {_BOLD}Result: {stats['applied']} applied, "
        f"{stats['skipped']} skipped, "
        f"{stats['errors']} errors "
        f"({stats['total']} total, {pct:.0f}%){_RESET}"
    )
    return stats


# ====================================================================
# Helpers
# ====================================================================

def apply_overlay(source_tree: Path, logger: Logger) -> dict:
    """Copy all files from overlay/ into the source tree.

    Mirrors build.py's _apply_source_overrides():
    - Overwrites existing files.
    - Creates new files (parent directories auto-created).
    - Skips .gitkeep placeholder files.

    Returns a dict with keys: copied_new, overwritten.
    """
    result = {"copied_new": 0, "overwritten": 0}

    if not OVERLAY_DIR.exists():
        logger.info(f"  No overlay directory at {OVERLAY_DIR}, skipping.")
        return result

    logger.info(f"  Copying overlay from {OVERLAY_DIR} ...")
    for f in sorted(OVERLAY_DIR.rglob("*")):
        if not f.is_file() or f.name == ".gitkeep":
            continue
        rel = f.relative_to(OVERLAY_DIR)
        dst = source_tree / rel
        existed_before = dst.exists()
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(f, dst)
        if existed_before:
            result["overwritten"] += 1
        else:
            result["copied_new"] += 1
            logger.detail(f"  [NEW] {rel}")

    logger.success(
        f"  Overlay applied: {result['overwritten']} overwritten, "
        f"{result['copied_new']} new files"
    )
    return result


def verify_source_tree(source_tree: Path, logger: Logger) -> bool:
    """Check source tree exists and has BUILD.gn."""
    if not source_tree.exists():
        logger.error(f"Source tree does not exist: {source_tree}")
        return False
    if not (source_tree / "BUILD.gn").exists():
        logger.error(f"Source tree missing BUILD.gn: {source_tree}")
        return False
    logger.info(f"  Source tree: {source_tree}")
    return True


def verify_git(git_bin: Path, logger: Logger) -> bool:
    """Verify git binary works."""
    try:
        result = subprocess.run(
            [str(git_bin), "--version"],
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode == 0:
            logger.info(f"  Git version: {result.stdout.strip()}")
            return True
        logger.error(f"git --version returned exit code {result.returncode}")
        return False
    except Exception as e:
        logger.error(f"Failed to verify git: {e}")
        return False


# ====================================================================
# Main
# ====================================================================

def main():
    parser = argparse.ArgumentParser(
        description="Apply all patches to build/src (idempotent, uses git apply).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
Examples:
  %(prog)s                               Apply all patches
  %(prog)s --only-thorium                Apply only Thorium patches
  %(prog)s --only-external              Apply only external patches
  %(prog)s --source-dir ../chromium      Use different source tree
  %(prog)s --log-file results.log        Custom log file
  %(prog)s --verbose                     Show detailed progress
        """,
    )
    parser.add_argument(
        "--source-dir", type=Path, default=DEFAULT_SOURCE_DIR,
        help=f"Chromium source tree (default: {DEFAULT_SOURCE_DIR})",
    )
    parser.add_argument(
        "--log-file", type=Path, default=DEFAULT_LOG_FILE,
        help=f"Log file (default: {DEFAULT_LOG_FILE})",
    )
    parser.add_argument(
        "--only-thorium", action="store_true",
        help="Only Thorium patches, skip external patches",
    )
    parser.add_argument(
        "--only-external", action="store_true",
        help="Only external patches, skip Thorium",
    )
    parser.add_argument(
        "--no-overlay", action="store_true",
        help="Skip overlay/ copy step",
    )
    parser.add_argument(
        "--verbose", "-v", action="store_true",
        help="Show detailed progress on console",
    )
    parser.add_argument(
        "--no-log", action="store_true",
        help="Do not write log file",
    )
    parser.add_argument(
        "--restore-failed", action="store_true",
        help="Restore files from --clean-source that were recorded as failed "
        "in the previous run, then exit",
    )
    parser.add_argument(
        "--clean-source", type=Path,
        default=ROOT_DIR.parent / "chromium",
        help="Clean Chromium source tree to restore failed files from "
        "(default: ../chromium)",
    )
    parser.add_argument(
        "--failed-files", type=Path,
        help="File listing failed file paths (one per line). "
        "Auto-derived from --log-file with .failed suffix if not given.",
    )
    args = parser.parse_args()

    log_path = None if args.no_log else args.log_file
    logger = Logger(log_path=log_path, verbose=args.verbose)
    start_time = time.time()

    # === Mode: --restore-failed (restore files from clean source, then exit) ===
    if args.restore_failed:
        clean_src = args.clean_source.resolve()
        failed_file = args.failed_files
        if failed_file is None and log_path is not None:
            failed_file = log_path.with_suffix(log_path.suffix + ".failed")
        if failed_file is None or not failed_file.exists():
            logger.error(
                f"No failed-files list found at {failed_file}. "
                f"Run normally first to generate it, or pass --failed-files."
            )
            sys.exit(1)

        paths = [
            p for p in failed_file.read_text(encoding="utf-8").splitlines()
            if p.strip()
        ]
        if not paths:
            logger.warning("Failed-files list is empty, nothing to restore.")
            sys.exit(0)

        logger.section("Restoring failed files from clean source")
        logger.info(f"  Clean source: {clean_src}")
        logger.info(f"  Target:       {args.source_dir}")
        logger.info(f"  Files to restore: {len(paths)}")

        if not clean_src.exists():
            logger.error(f"Clean source does not exist: {clean_src}")
            sys.exit(1)

        restored = 0
        missing = 0
        for rel_path in paths:
            rel = rel_path.replace("\\", "/").lstrip("/")
            src = clean_src / rel
            dst = args.source_dir / rel
            if not src.exists():
                logger.warning(f"  NOT FOUND in clean source: {rel}")
                missing += 1
                continue
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
            logger.success(f"  Restored: {rel}")
            restored += 1

        logger.info(
            f"  Done: {restored} restored, {missing} missing"
        )
        sys.exit(1 if missing > 0 else 0)

    logger.section("Thorium All-Patches Application (git apply)")
    logger.info(f"  Started: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    logger.info(f"  Root dir: {ROOT_DIR}")

    # === Step 1: Verify source tree ===
    if not verify_source_tree(args.source_dir, logger):
        sys.exit(1)

    # === Step 2: Find git ===
    git_bin = find_git(logger)
    if git_bin is None:
        logger.error("Aborting: no git found.")
        sys.exit(1)
    if not verify_git(git_bin, logger):
        logger.error("Aborting: git binary broken.")
        sys.exit(1)

    # === Step 3: Verify source tree is a git repo (needed by git apply) ===
    if not (args.source_dir / ".git").exists():
        logger.warning(
            "Source tree is not a git repository -- git apply will only handle "
            "patches that touch existing tracked files. New file creation "
            "patches may fail."
        )

    # === Step 4: Apply ungoogled-chromium-windows patches ===
    overall = {"total": 0, "applied": 0, "skipped": 0, "errors": 0}
    all_failed_files: List[str] = []
    overlay_stats = {"copied_new": 0, "overwritten": 0}

    if not args.only_thorium:
        up = parse_external_series(EXTERNAL_SERIES_FILE)
        if up:
            s = apply_patch_set(
                "external", up,
                args.source_dir, git_bin, logger,
            )
            for k in overall:
                overall[k] += s[k]
            all_failed_files.extend(s.get("failed_files", []))
        else:
            logger.warning(
                f"No external patches in {EXTERNAL_SERIES_FILE}"
            )

    # === Step 5: Apply overlay/ (copy verbatim) ===
    if not args.no_overlay:
        logger.section("Applying overlay/ (source overrides)")
        overlay_stats = apply_overlay(args.source_dir, logger)

    # === Step 6: Apply Thorium-specific patches ===
    if not args.only_external:
        tp = parse_thorium_series(THORIUM_SERIES_FILE)
        if tp:
            s = apply_patch_set(
                "Thorium", tp,
                args.source_dir, git_bin, logger,
            )
            for k in overall:
                overall[k] += s[k]
            all_failed_files.extend(s.get("failed_files", []))
        else:
            logger.warning(f"No Thorium patches in {THORIUM_SERIES_FILE}")

    # === Final summary ===
    elapsed = time.time() - start_time
    logger.section("FINAL SUMMARY")
    logger.info(f"  Total patches processed: {overall['total']}")
    if overall["applied"] > 0:
        logger.success(f"  Successfully applied:     {overall['applied']}")
    if overall["skipped"] > 0:
        logger.info(f"  Skipped (already applied): {overall['skipped']}")
    if overall["errors"] > 0:
        logger.error(f"  Errors:                   {overall['errors']}")
        logger.info(f"  {_YELLOW}See log file for details.{_RESET}")

    # Overlay summary
    if overlay_stats["overwritten"] or overlay_stats["copied_new"]:
        logger.info(
            f"  Overlay: {overlay_stats['overwritten']} overwritten, "
            f"{overlay_stats['copied_new']} new files"
        )

    # Save failed-files list to disk
    if log_path and all_failed_files:
        failed_path = log_path.with_suffix(log_path.suffix + ".failed")
        unique = sorted(set(all_failed_files))
        failed_path.write_text("\n".join(unique) + "\n", encoding="utf-8")
        logger.info(
            f"  Failed files list: {failed_path} ({len(unique)} files)"
        )
        logger.info(
            f"  {_YELLOW}Tip: run with --restore-failed to restore these files"
            f" from a clean source tree.{_RESET}"
        )

    logger.info(f"  Time elapsed: {elapsed:.1f}s")
    if log_path:
        logger.info(f"  Detailed log: {log_path}")

    sys.exit(1 if overall["errors"] > 0 else 0)


if __name__ == "__main__":
    main()
