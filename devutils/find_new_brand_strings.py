#!/usr/bin/env python3
# -*- coding: utf-8 -*-

# Copyright (c) 2026 The Thorium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
"""
Find new brandable string IDs in GRD/GRDP files.

Scans the GRD files listed in sync_brand_strings.py's GRD_XTB_MAP and
reports any <message> entries whose text, desc, or meaning would be
changed by branding_replace() but whose ID is NOT already in the
BRAND_STRING_IDS set (i.e. not yet covered by the branding patches).

Output: for each GRD file, a list of new string IDs that should be
added to brand_string_ids.py (or that need new branding patch hunks).

Usage:
  python devutils/find_new_brand_strings.py build/src
  python devutils/find_new_brand_strings.py build/src -v
  python devutils/find_new_brand_strings.py build/src --show-all
"""

import argparse
import logging
import re
import sys
from pathlib import Path
from typing import Dict, List, Set, Tuple

# Ensure project root is on sys.path for imports from patch_scripts/
if __name__ == '__main__' and __package__ is None:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from patch_scripts.brand_string_ids import BRAND_STRING_IDS
from patch_scripts.sync_brand_strings import (
    GRD_XTB_MAP,
    _MESSAGE_RE,
    branding_replace,
    _replace_tag_attrs,
    _resolve_grd_parts,
)

logger = logging.getLogger('devtools.find_new_brand_strings')


# ---------------------------------------------------------------------------
# Helpers — reuse sync_brand_strings logic directly
# ---------------------------------------------------------------------------

def _scan_file_for_new_ids(content: str) -> List[Tuple[str, str, str]]:
    """Scan a GRD/GRDP file's text content for new brandable string IDs.

    Reuses the same _MESSAGE_RE regex and branding_replace/_replace_tag_attrs
    that sync_brand_strings.apply_grd_file_replacement uses — so detection
    is 100% consistent with what the replacement phase actually does.

    Returns list of (resource_id, raw_body_snippet, reason).
    """
    entries: List[Tuple[str, str, str]] = []

    for m in _MESSAGE_RE.finditer(content):
        rid = m.group(2)
        if not rid or rid in BRAND_STRING_IDS:
            continue

        opening = m.group(1)
        body = m.group(3)

        reasons: List[str] = []
        new_opening = _replace_tag_attrs(opening)
        if new_opening != opening:
            reasons.append('desc/meaning contains brand strings')
        new_body = branding_replace(body)
        if new_body != body:
            reasons.append('text contains brand strings')

        if not reasons:
            continue

        # Build readable text: strip XML tags, keep full length
        text = re.sub(r'<[^>]+>', '', body).strip()

        entries.append((rid, text, '; '.join(reasons)))

    return entries


# ---------------------------------------------------------------------------
# Main scan logic
# ---------------------------------------------------------------------------

def find_new_brand_strings(
    source_tree: Path,
    show_all: bool = False,
    verbose: bool = False,
) -> Dict[str, List[Tuple[str, str, str]]]:
    """Scan GRD/GRDP files and report new brandable string IDs.

    Reuses sync_brand_strings.py's _MESSAGE_RE + branding_replace logic
    to detect messages that would be changed — no separate XML parsing.

    Returns:
        Dict mapping GRD-relative-path -> list of (resource_id, snippet, reason).
    """
    result: Dict[str, List[Tuple[str, str, str]]] = {}

    for mapping in GRD_XTB_MAP:
        grd_rel = mapping['grd']
        grd_path = source_tree / grd_rel
        if not grd_path.exists():
            if verbose:
                logger.info('SKIP: %s not found', grd_rel)
            continue

        entries: List[Tuple[str, str, str]] = []
        total_ids = 0

        def _scan_file(file_path: Path) -> None:
            nonlocal total_ids
            content = file_path.read_text(encoding='utf-8')
            # Count total IDs even if skipped
            total_ids += len(set(
                m.group(2) for m in _MESSAGE_RE.finditer(content) if m.group(2)
            ))
            entries.extend(_scan_file_for_new_ids(content))

        _scan_file(grd_path)
        for grdp in _resolve_grd_parts(grd_path):
            _scan_file(grdp)

        new_count = len(entries)
        if verbose or new_count > 0:
            print(file=sys.stderr)
            print(f'  [{grd_rel}]', file=sys.stderr)
            print(f'    Total IDs: {total_ids}', file=sys.stderr)
            print(f'    NEW brandable IDs (not yet covered): {new_count}', file=sys.stderr)

        if new_count > 0:
            result[grd_rel] = sorted(entries, key=lambda x: x[0])

    return result


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    p = argparse.ArgumentParser(
        description='Find new brandable string IDs not yet in BRAND_STRING_IDS.')
    p.add_argument('source_tree', type=Path,
                   help='Chromium source tree root (e.g. build/src/)')
    p.add_argument('-v', '--verbose', action='store_true',
                   help='Show verbose per-file statistics')
    p.add_argument('--show-all', action='store_true',
                   help='Also list all message IDs (not just new ones)')
    p.add_argument('--summary-only', action='store_true',
                   help='Only print the summary table, skip per-ID listing')
    args = p.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.WARNING,
        format='%(levelname)s %(message)s', stream=sys.stderr)

    source_tree = args.source_tree.resolve()
    if not source_tree.is_dir():
        print(f'Error: {source_tree} is not a directory', file=sys.stderr)
        sys.exit(1)

    print(file=sys.stderr)
    print(f'Scanning GRD files under: {source_tree}', file=sys.stderr)
    print(f'BRAND_STRING_IDS: {len(BRAND_STRING_IDS)} known IDs', file=sys.stderr)
    print(f'GRD_XTB_MAP entries: {len(GRD_XTB_MAP)}', file=sys.stderr)
    print(file=sys.stderr)
    print('=' * 65, file=sys.stderr)

    result = find_new_brand_strings(
        source_tree,
        show_all=args.show_all,
        verbose=args.verbose,
    )

    print(file=sys.stderr)
    print('=' * 65, file=sys.stderr)
    print('RESULTS: New brandable string IDs per GRD file', file=sys.stderr)
    print('=' * 65, file=sys.stderr)

    total_new = 0
    for grd_rel, entries in sorted(result.items()):
        print(f'\n{"=" * 65}')
        print(f'FILE: {grd_rel}')
        print(f'{"=" * 65}')
        print(f'  {len(entries)} new brandable ID(s):')

        for rid, text, reason in entries:
            print(f'\n    {rid}')
            if text:
                print(f'      text: {text!r}')
            print(f'      why:  {reason}')

        total_new += len(entries)

    print(f'\n{"=" * 65}')
    print(f'TOTAL: {total_new} new brandable string ID(s) across '
          f'{len(result)} GRD file(s)')
    print(f'{"=" * 65}')

    if total_new == 0:
        print('\nNo new brandable string IDs found. '
              'BRAND_STRING_IDS is up to date.')
    else:
        print(f'\nTo add these to the branding set, update '
              f'`patch_scripts/brand_string_ids.py` '
              f'with the new IDs above.')

    sys.exit(0)


if __name__ == '__main__':
    main()
