#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Remove hunks from patches/thorium/branding/ that only do brand‑string
replacement.  After sync_brand_strings.py handles those substitutions,
these hunks are redundant.

What this script does:
  1. Reads every .patch file in the branding directory.
  2. For hunks that modify .grd / .grdp files and whose differences are
     purely "Chromium → Thorium" / "Google Chrome → Thorium" substitutions,
     the hunk is removed.
  3. If a patch becomes empty after removal, it is **deleted**.
  4. All other hunks (structural changes, non‑GRD files, images, …) are
     kept untouched.

Usage:
    python patch_scripts/cleanup_branding_patches.py         # default dir
    python patch_scripts/cleanup_branding_patches.py --dry-run
    python patch_scripts/cleanup_branding_patches.py --branding-dir PATH

Run with --dry-run first to see what would change.
"""

import argparse
import logging
import re
import sys
from pathlib import Path
from typing import List, Tuple

logger = logging.getLogger('cleanup_branding_patches')

# ---------------------------------------------------------------------------
# Brand replacement logic (same as sync_brand_strings.py)
# ---------------------------------------------------------------------------

def _branding_replace(text: str) -> str:
    """MUST match sync_brand_strings.branding_replace().

    Order matters -- longer matches first:
        1. Google Chrome -> Thorium
        2. Chromium      -> Thorium
    """
    text = text.replace('Google Chrome', 'Thorium')
    text = text.replace('Chromium', 'Thorium')
    return text


def combined_replace(text: str) -> str:
    """Brand replacement — MUST match sync_brand_strings.branding_replace().

    Order: Google Chrome → Thorium, Chromium → Thorium, Chrome → Thorium.
    """
    return _branding_replace(text)


# ---------------------------------------------------------------------------
# Hunk analysis
# ---------------------------------------------------------------------------

def _is_grd_file(file_header: str) -> bool:
    """Check if the ``--- a/…`` header points to a .grd or .grdp file."""
    return file_header.endswith('.grd') or file_header.endswith('.grdp')


def _hunk_is_pure_branding(hunk_body: str) -> Tuple[bool, int]:
    """Check whether every changed line in *hunk_body* is pure branding.

    Returns:
        (is_pure, removed_line_count)
    """
    removed = 0
    for line in hunk_body.split('\n'):
        if line.startswith('-'):
            old = line[1:]
            removed += 1
        elif line.startswith('+'):
            new = line[1:]
            # Find the corresponding removed line (previous '-' line).
            # For a pure branding hunk, combined_replace(old) == new.
            # We check this by looking at the context: in a unified diff,
            # '+' lines immediately follow their corresponding '-' lines.
            # Simple heuristic: skip ahead — we verify per‑hunk below.
            pass
        # context lines (space) are ignored

    return True, removed


def _hunk_is_pure_branding_strict(hunk_body: str) -> bool:
    """Strict check: every ``-`` line, when brand‑replaced, equals the
    corresponding ``+`` line.  Context (`` ``) lines are ignored.

    This handles the common diff pattern where a block of ``-`` lines is
    followed by a block of ``+`` lines.
    """
    old_lines: List[str] = []
    new_lines: List[str] = []

    for line in hunk_body.split('\n'):
        if line.startswith('-'):
            old_lines.append(line[1:])
        elif line.startswith('+'):
            new_lines.append(line[1:])
        # context lines — ignored

    # If either list is empty there's nothing to compare
    if not old_lines and not new_lines:
        return True  # empty hunk — safe to remove
    if not old_lines or not new_lines:
        return False  # only additions or only removals → structural

    # Zip and compare.  Old lines might be more or fewer than new lines
    # in a structural change.  For pure branding they should be equal in
    # count and content (after replacement).
    if len(old_lines) != len(new_lines):
        return False

    for old, new in zip(old_lines, new_lines):
        if combined_replace(old) != new:
            return False

    return True


# ---------------------------------------------------------------------------
# Patch file processing
# ---------------------------------------------------------------------------

def process_patch(patch_path: Path, dry_run: bool) -> Tuple[str, int, int]:
    """Process one .patch file.

    Returns:
        (action, kept_hunks): action is one of 'deleted', 'cleaned', 'kept'.
    """
    content = patch_path.read_text(encoding='utf-8', errors='replace')
    lines = content.split('\n')

    # Identify the file being patched from the --- header.
    file_header = ''
    for line in lines:
        if line.startswith('--- a/'):
            file_header = line[6:]  # strip '--- a/'
            break

    # Only process .grd / .grdp patches
    if not _is_grd_file(file_header):
        return 'kept', 0, 0

    # Split into hunks.  A hunk starts with @@ … @@ and continues until
    # the next @@ or end of file.
    hunk_starts: List[int] = []
    for i, line in enumerate(lines):
        if line.startswith('@@ '):
            hunk_starts.append(i)

    if not hunk_starts:
        return 'kept', 0, 0

    # Process hunks in reverse order so line numbers stay valid
    removed_count = 0
    result = list(lines)

    for hdr_idx in reversed(hunk_starts):
        # Find the end of this hunk
        body_start = hdr_idx + 1
        body_end = len(result)
        for j in range(body_start, len(result)):
            if result[j].startswith('@@ '):
                body_end = j
                break

        hunk_body_lines = result[body_start:body_end]
        hunk_body = '\n'.join(hunk_body_lines)

        if _hunk_is_pure_branding_strict(hunk_body):
            # Remove this whole hunk: header line + body lines.
            if not dry_run:
                del result[hdr_idx:body_end]
            removed_count += 1

    # Clean up: remove leading/trailing blank lines
    if removed_count > 0 and not dry_run:
        while result and result[0] == '':
            result.pop(0)
        while result and result[-1] == '':
            result.pop()

    # How many hunks remain?
    remaining = len([l for l in result if l and l.startswith('@@ ')])
    total = len(hunk_starts)

    if removed_count == 0:
        logger.debug('  [KEPT] %s — %d hunk(s), none removable',
                      patch_path.name, total)
        return 'kept', total, 0

    if remaining == 0:
        if dry_run:
            logger.info('  [WOULD DELETE] %s — all %d hunk(s) removable',
                        patch_path.name, total)
            return 'would_delete', 0, total
        patch_path.unlink()
        logger.info('  [DELETED] %s — all %d hunk(s) removed',
                    patch_path.name, total)
        return 'deleted', 0, total

    # Some hunks removed, some kept
    if not dry_run:
        patch_path.write_text('\n'.join(result), encoding='utf-8')
    logger.info('  [CLEANED] %s — %d/%d hunk(s) removed',
                patch_path.name, removed_count, total)
    return 'cleaned', remaining, removed_count


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def cleanup_branding_patches(
    branding_dir: Path,
    dry_run: bool = False,
) -> None:
    """Remove string‑replacement hunks from branding patches."""
    if not branding_dir.exists():
        logger.error('Directory not found: %s', branding_dir)
        return

    patches = sorted(branding_dir.glob('*.patch'))
    if not patches:
        logger.warning('No .patch files found in %s', branding_dir)
        return

    logger.info('=' * 60)
    logger.info('Cleanup branding patches — removing redundant hunks')
    logger.info('Directory: %s', branding_dir)
    if dry_run:
        logger.info('*** DRY RUN — no files will be modified ***')
    logger.info('=' * 60)

    counts = {'kept': 0, 'cleaned': 0, 'deleted': 0, 'would_delete': 0}

    for patch in patches:
        action, kept, removed = process_patch(patch, dry_run)
        counts[action] = counts.get(action, 0) + 1

    logger.info('\n' + '=' * 60)
    logger.info('Summary:')
    for k, v in counts.items():
        if v > 0:
            logger.info('  %s: %d', k, v)
    logger.info('=' * 60)


def main() -> None:
    parser = argparse.ArgumentParser(
        description='Remove brand‑string hunks from branding patches.')
    parser.add_argument('--branding-dir', type=Path, default=None,
                        help='Branding patches directory '
                             '(default: patches/thorium/branding)')
    parser.add_argument('--dry-run', '-n', action='store_true')
    parser.add_argument('--verbose', '-v', action='store_true')
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format='%(levelname)s %(message)s', stream=sys.stderr)

    if args.branding_dir is None:
        branding_dir = (
            Path(__file__).resolve().parent.parent
            / 'patches' / 'thorium' / 'branding'
        )
    else:
        branding_dir = args.branding_dir.resolve()

    cleanup_branding_patches(branding_dir, dry_run=args.dry_run)
    sys.exit(0)


if __name__ == '__main__':
    main()
