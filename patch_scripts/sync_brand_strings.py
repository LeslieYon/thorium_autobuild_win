#!/usr/bin/env python3
# -*- coding: utf-8 -*-

# Copyright (c) 2026 The Thorium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
"""
Self-contained brand string synchroniser for GRD/GRDP and XTB files.

This script replaces the string-replacement hunks in
patches/thorium/branding/.  It uses an exact list of resource IDs (from
brand_string_ids.py, extracted from the branding patches) so it only
touches messages the patches would have changed.

Workflow (runs after non-branding patches):

  Phase 1 -- GRD/GRDP replacement
    Scan every <message> block whose resource ID is in BRAND_STRING_IDS.
    Replace "Google Chrome" -> "Thorium" and "Chromium" -> "Thorium" in
    both the message body and the desc/meaning attribute.

  Phase 2 -- XTB synchronisation
    For every changed message:
      - Compute the OLD translation ID from pre-replacement text.
      - Compute the NEW translation ID from the Thorium text.
      - In every language's XTB:
          * Look up the old ID -> get the translated string.
          * Apply the same brand replacements.
          * Insert a new <translation> with the new ID.

Replacements (case-sensitive, longest-match first):
    Google Chrome -> Thorium
    Chromium     -> Thorium
"""

import argparse
import copy
import hashlib
import logging
import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Dict, List, Optional, Tuple

# Ensure the project root is on sys.path so the import works
# regardless of whether the script is run as a module or directly.
if __name__ == '__main__' and __package__ is None:
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from patch_scripts.brand_string_ids import BRAND_STRING_IDS

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logger = logging.getLogger('ungoogled.sync_brand_strings')

# ---------------------------------------------------------------------------
# Configuration -- GRD/GRDP -> XTB mapping
# ---------------------------------------------------------------------------
# {lang} in xtb_glob matches any language code ([a-z-]+).
# GRDP parts included via <part file="..."> are auto-resolved.
GRD_XTB_MAP: List[Dict[str, str]] = [
    {'grd': 'chrome/app/chromium_strings.grd',
     'xtb_glob': 'chrome/app/resources/chromium_strings_{lang}.xtb'},
    {'grd': 'chrome/app/generated_resources.grd',
     'xtb_glob': 'chrome/app/resources/generated_resources_{lang}.xtb'},
    {'grd': 'components/components_chromium_strings.grd',
     'xtb_glob': 'components/strings/components_chromium_strings_{lang}.xtb'},
    {'grd': 'components/components_strings.grd',
     'xtb_glob': 'components/strings/components_strings_{lang}.xtb'},
    {'grd': 'chromeos/chromeos_strings.grd',
     'xtb_glob': 'chromeos/strings/chromeos_strings_{lang}.xtb'},
    {'grd': 'ui/chromeos/ui_chromeos_strings.grd',
     'xtb_glob': 'ui/chromeos/translations/ui_chromeos_strings_{lang}.xtb'},
    {'grd': 'chrome/browser/ui/android/strings/android_chrome_strings.grd',
     'xtb_glob': 'chrome/browser/ui/android/strings/translations/'
                'android_chrome_strings_{lang}.xtb'},
]

# ===================================================================
# 1. String replacement
# ===================================================================

def branding_replace(text: str) -> str:
    """Apply brand replacement (case-sensitive, longest-first).

    Order matters -- longer matches first to avoid partial replacement:
        1. Google Chrome -> Thorium
        2. Chromium      -> Thorium
    """
    text = text.replace('Google Chrome', 'Thorium')
    text = text.replace('ChromeOS Flex', 'ThoriumOS')
    text = text.replace('ChromeOS', 'ThoriumOS')
    text = text.replace('The Chromium Authors', 'Alex313031')
    text = text.replace('Chromium', 'Thorium')
    text = re.sub(r'Chrome(?! Web Store)', 'Thorium', text)
    return text


def _replace_in_element(elem: ET.Element, fn) -> None:
    """Recursively apply *fn* to every text/tail node."""
    if elem.text:
        elem.text = fn(elem.text)
    for child in elem:
        _replace_in_element(child, fn)
        if child.tail:
            child.tail = fn(child.tail)


# ===================================================================
# 2. Translation-ID generation (exact grit replica)
# ===================================================================

def _unsigned_fingerprint(text: str) -> int:
    md5 = hashlib.md5(text.encode('utf-8')).hexdigest()
    return int(md5[:16], 16)


def _signed_fingerprint(text: str) -> int:
    fp = _unsigned_fingerprint(text)
    if fp & 0x8000000000000000:
        fp = -((~fp & 0xFFFFFFFFFFFFFFFF) + 1)
    return fp


def generate_message_id(message_text: str, meaning: str = '') -> str:
    """Translation ID identical to grit's GenerateMessageId()."""
    fp = _signed_fingerprint(message_text)
    if meaning:
        fp2 = _signed_fingerprint(meaning)
        if fp < 0:
            fp = fp2 + (fp << 1) + 1
        else:
            fp = fp2 + (fp << 1)
    return str(fp & 0x7fffffffffffffff)


# ===================================================================
# 3. GRD/GRDP -- Phase 1: apply brand replacements
# ===================================================================

# Regex matching a complete <message name="...">...</message> block.
_MESSAGE_RE = re.compile(
    r'(<message\s[^>]*?name="([^"]*)"[^>]*>)'
    r'(.*?)'
    r'(</message>)',
    re.DOTALL,
)


def _replace_tag_attrs(tag: str) -> str:
    """Apply brand replacement inside desc="..." and meaning="..."."""
    tag = re.sub(
        r'(desc\s*=\s*")([^"]*)(")',
        lambda m: m.group(1) + branding_replace(m.group(2)) + m.group(3),
        tag,
    )
    tag = re.sub(
        r'(meaning\s*=\s*")([^"]*)(")',
        lambda m: m.group(1) + branding_replace(m.group(2)) + m.group(3),
        tag,
    )
    return tag


def apply_grd_file_replacement(file_path: Path) -> bool:
    """Apply brand replacement to <message> blocks whose ID is in the
    BRAND_STRING_IDS set.  Returns True if the file was modified."""
    if not file_path.exists():
        return False
    original = file_path.read_text(encoding='utf-8')

    replacements: List[Tuple[int, int, str]] = []
    for m in _MESSAGE_RE.finditer(original):
        rid = m.group(2)
        if rid not in BRAND_STRING_IDS:
            continue
        opening = m.group(1)
        body = m.group(2)  # not used, but kept for clarity
        body = m.group(3)
        closing = m.group(4)
        new_opening = _replace_tag_attrs(opening)
        new_body = branding_replace(body)
        if new_opening != opening or new_body != body:
            replacements.append((m.start(), m.end(),
                                 new_opening + new_body + closing))

    if not replacements:
        return False

    result = original
    for start, end, new_text in sorted(replacements, key=lambda x: -x[0]):
        result = result[:start] + new_text + result[end:]

    file_path.write_text(result, encoding='utf-8')
    logger.info('  [MODIFIED] %s -- %d message(s)', file_path.name,
                len(replacements))
    return True


def _resolve_grd_parts(grd_path: Path) -> List[Path]:
    """Resolve GRDP files included via <part file="...">."""
    out: List[Path] = []
    if not grd_path.exists():
        return out
    try:
        tree = ET.parse(str(grd_path))
    except ET.ParseError:
        return out
    gdir = grd_path.parent
    for p in tree.getroot().iter('part'):
        f = p.get('file', '')
        if f:
            rp = (gdir / f).resolve()
            if rp.exists():
                out.append(rp)
    return out


# ===================================================================
# 4. XTB -- Phase 2: synchronise translations
# ===================================================================

def _walk_text_parts(elem: ET.Element) -> List[str]:
    """Presentable content -- <ph name="X"> becomes X (upper)."""
    parts: List[str] = []
    if elem.text:
        parts.append(elem.text)
    for ch in elem:
        if ch.tag == 'ph':
            parts.append(ch.get('name', '').upper())
        elif ch.tag in ('if', 'then', 'else', 'part'):
            parts.extend(_walk_text_parts(ch))
        else:
            if ch.text:
                parts.append(ch.text)
        if ch.tail:
            parts.append(ch.tail)
    return parts


def _msg_info(msg: ET.Element) -> Tuple[str, str]:
    """Return (presentable_text, meaning) for a <message>.

    ``meaning`` comes from the XML ``meaning`` attribute (not ``desc``).
    Grit uses it in GenerateMessageId for ID disambiguation.
    The ``desc`` attribute is a translator hint and does NOT affect IDs.
    """
    meaning = msg.get('meaning', '')
    text = ''.join(_walk_text_parts(msg)).strip()
    return text, meaning


def _find_xtb_files(src: Path, glob_pat: str) -> List[Path]:
    if '{lang}' in glob_pat:
        pat = '^' + re.escape(glob_pat).replace(r'\{lang\}', r'[a-zA-Z-]+') + '$'
        rx = re.compile(pat)
        d = src / Path(glob_pat).parent
        if not d.exists():
            return []
        def _rel(p: Path) -> str:
            """Relative path with POSIX separators, for cross‑platform regex match."""
            return p.relative_to(src).as_posix()
        return sorted(p for p in d.iterdir()
                      if p.suffix == '.xtb' and rx.match(_rel(p)))
    return sorted(src.glob(glob_pat))


def _load_xtb(path: Path) -> Tuple[Optional[ET.Element], Optional[ET.Element]]:
    try:
        tree = ET.parse(str(path))
        return tree.getroot(), tree
    except (ET.ParseError, FileNotFoundError):
        return None, None


def _find_trans(root: ET.Element, tid: str) -> Optional[ET.Element]:
    for e in root.iter('translation'):
        if e.get('id', '') == tid:
            return e
    return None


def _serialize_xtb(root: ET.Element) -> bytes:
    lang = root.get('lang', '')
    lines = ['<?xml version="1.0" ?>', '<!DOCTYPE translationbundle>',
             f'<translationbundle lang="{lang}">']
    for ch in root:
        if ch.tag != 'translation':
            continue
        inner = _elem_text(ch)
        lines.append(f'<translation id="{ch.get("id","")}">{inner}</translation>')
    lines.append('</translationbundle>')
    return '\n'.join(lines).encode('utf-8')


def _elem_text(elem: ET.Element) -> str:
    parts = [_xml_esc(elem.text or '')]
    for ch in elem:
        inner = _elem_text(ch)
        a = ' '.join(f'{k}="{_xml_esc(v,attr=True)}"' for k, v in ch.attrib.items())
        if inner:
            parts.append(f'<{ch.tag} {a}>{inner}</{ch.tag}>')
        else:
            parts.append(f'<{ch.tag} {a} />')
        parts.append(_xml_esc(ch.tail or ''))
    return ''.join(parts)


def _xml_esc(text: str, attr: bool = False) -> str:
    text = text.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
    if attr:
        text = text.replace('"', '&quot;')
    return text


# ===================================================================
# 5. Main orchestrator
# ===================================================================

def sync_brand_strings(
    source_tree: Path,
    dry_run: bool = False,
) -> bool:
    """Apply brand replacement to GRD/GRDP files and sync XTB translations.

    Args:
        source_tree: Absolute path to the Chromium source tree (build/src/).
        dry_run: If True, only report, don't write.

    Returns:
        True on success.
    """
    logger.info('=' * 60)
    logger.info('Sync Brand Strings -- Phase 1/2: GRD/GRDP replacement')
    logger.info('Source tree: %s', source_tree)
    logger.info('Precise ID set: %d resource IDs from branding patches',
                len(BRAND_STRING_IDS))
    if dry_run:
        logger.info('*** DRY RUN -- no files will be modified ***')
    logger.info('=' * 60)

    # Cache: resource_id -> (old_presentable, old_meaning) captured before
    # Phase 1 modifies the files.  Phase 2 uses this instead of attempting
    # an ambiguous reverse replacement.
    _old_text_cache: Dict[str, Tuple[str, str]] = {}

    # Track which BRAND_STRING_IDS are actually found (to warn about missing ones).
    _found_ids = set()

    # Build cache from current (pre-replacement) GRD/GRDP content.
    for mapping in GRD_XTB_MAP:
        grd = source_tree / mapping['grd']
        if not grd.exists():
            continue
        try:
            tree = ET.parse(str(grd))
            for msg in tree.getroot().iter('message'):
                rid = msg.get('name', '')
                if rid in BRAND_STRING_IDS:
                    _old_text_cache[rid] = _msg_info(msg)
                    _found_ids.add(rid)
        except ET.ParseError:
            continue
        for grdp in _resolve_grd_parts(grd):
            try:
                tree = ET.parse(str(grdp))
                for msg in tree.getroot().iter('message'):
                    rid = msg.get('name', '')
                    if rid in BRAND_STRING_IDS:
                        _old_text_cache[rid] = _msg_info(msg)
                        _found_ids.add(rid)
            except ET.ParseError:
                continue

    logger.debug('Cached %d pre-replacement message texts.', len(_old_text_cache))

    # Warn about BRAND_STRING_IDS not present in any GRD/GRDP file.
    missing_ids = BRAND_STRING_IDS - _found_ids
    if missing_ids:
        logger.warning('WARNING: %d BRAND_STRING_IDS not found in any GRD/GRDP file:',
                       len(missing_ids))
        for rid in sorted(missing_ids):
            logger.warning('  MISSING: %s', rid)
    else:
        logger.info('All %d BRAND_STRING_IDS are present in GRD/GRDP files.',
                    len(BRAND_STRING_IDS))

    # =================================================================
    # Phase 1 -- Apply brand replacement to every GRD/GRDP file
    # =================================================================
    total_grd = 0
    for mapping in GRD_XTB_MAP:
        grd_path = source_tree / mapping['grd']
        if not grd_path.exists():
            continue

        if dry_run:
            content = grd_path.read_text(encoding='utf-8')
            touched = False
            for m in _MESSAGE_RE.finditer(content):
                rid = m.group(2)
                if rid not in BRAND_STRING_IDS:
                    continue
                opening = m.group(1)
                body = m.group(3)
                if 'Google Chrome' in opening or 'Chromium' in opening or \
                   'Google Chrome' in body or 'Chromium' in body:
                    logger.info('  [DRY-RUN] Would replace %s in %s',
                                rid, grd_path.name)
                    touched = True
            if touched:
                total_grd += 1

            for grdp in _resolve_grd_parts(grd_path):
                content = grdp.read_text(encoding='utf-8')
                grdp_touched = False
                for m in _MESSAGE_RE.finditer(content):
                    rid = m.group(2)
                    if rid not in BRAND_STRING_IDS:
                        continue
                    opening = m.group(1)
                    body = m.group(3)
                    if 'Google Chrome' in opening or 'Chromium' in opening or \
                       'Google Chrome' in body or 'Chromium' in body:
                        logger.info('  [DRY-RUN] Would replace %s in %s',
                                    rid, grdp.name)
                        grdp_touched = True
                if grdp_touched:
                    total_grd += 1
        else:
            if apply_grd_file_replacement(grd_path):
                total_grd += 1
            for grdp in _resolve_grd_parts(grd_path):
                if apply_grd_file_replacement(grdp):
                    total_grd += 1

    logger.info('Phase 1 done -- %d GRD/GRDP file(s) touched.', total_grd)

    # =================================================================
    # Phase 2 -- Synchronise XTB files
    # =================================================================
    logger.info('\n' + '=' * 60)
    logger.info('Phase 2/2: XTB synchronisation')
    logger.info('=' * 60)

    total_upd = total_skp = total_nf = 0
    total_msgs = 0

    for mapping in GRD_XTB_MAP:
        grd_path = source_tree / mapping['grd']
        xtb_pat = mapping['xtb_glob']
        if not grd_path.exists():
            continue

        xtb_files = _find_xtb_files(source_tree, xtb_pat)
        if not xtb_files:
            continue

        logger.info('\n  --- %s (%d XTB files) ---',
                    grd_path.name, len(xtb_files))

        # Collect messages whose ID is in the target set
        msgs: Dict[str, Tuple[str, str]] = {}
        try:
            tree = ET.parse(str(grd_path))
            for msg in tree.getroot().iter('message'):
                rid = msg.get('name', '')
                if rid in BRAND_STRING_IDS:
                    msgs[rid] = _msg_info(msg)
        except ET.ParseError:
            continue

        for grdp in _resolve_grd_parts(grd_path):
            try:
                tree = ET.parse(str(grdp))
                for msg in tree.getroot().iter('message'):
                    rid = msg.get('name', '')
                    if rid in BRAND_STRING_IDS:
                        msgs[rid] = _msg_info(msg)
            except ET.ParseError:
                continue

        if not msgs:
            continue

        # Process each message
        for rid, (presentable, meaning) in sorted(msgs.items()):
            new_id = generate_message_id(presentable, meaning)

            # Old ID comes from the pre-replacement cache, avoiding reverse
            # string replacement entirely (no ambiguity to resolve).
            cached = _old_text_cache.get(rid)
            if cached is None:
                logger.debug('    %s: not in pre-replacement cache, skipping', rid)
                continue
            old_text, old_meaning = cached
            old_id = generate_message_id(old_text, old_meaning)

            if old_id == new_id:
                continue

            upd = skp = nf = 0
            for xtb in xtb_files:
                if dry_run:
                    upd += 1
                    continue

                root, _tree = _load_xtb(xtb)
                if root is None:
                    nf += 1
                    continue

                entry = _find_trans(root, old_id)
                if entry is None:
                    nf += 1
                    continue
                if _find_trans(root, new_id) is not None:
                    skp += 1
                    continue

                new_entry = copy.deepcopy(entry)
                new_entry.set('id', new_id)
                _replace_in_element(new_entry, branding_replace)

                idx = list(root).index(entry) + 1
                root.insert(idx, new_entry)
                xtb.write_bytes(_serialize_xtb(root))
                upd += 1

            total_upd += upd
            total_skp += skp
            total_nf += nf
            total_msgs += 1

            if upd > 0 or dry_run:
                logger.info('    %s: old=%s new=%s upd=%d skp=%d nf=%d',
                            rid, old_id, new_id, upd, skp, nf)

    logger.info('\n' + '=' * 60)
    logger.info('Summary: %d messages processed, %d GRD/GRDP files touched.',
                total_msgs, total_grd)
    logger.info('  XTB entries -- updated: %d  skipped: %d  not-found: %d',
                total_upd, total_skp, total_nf)
    logger.info('=' * 60)
    return True


# ===================================================================
# 6. CLI
# ===================================================================

def main() -> None:
    p = argparse.ArgumentParser(
        description='Apply brand replacements to GRD/GRDP + sync XTB.')
    p.add_argument('source_tree', type=Path,
                   help='Chromium source tree root (build/src/)')
    p.add_argument('-v', '--verbose', action='store_true')
    p.add_argument('-n', '--dry-run', action='store_true')
    args = p.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format='%(levelname)s %(message)s', stream=sys.stderr)

    ok = sync_brand_strings(
        source_tree=args.source_tree.resolve(),
        dry_run=args.dry_run)
    sys.exit(0 if ok else 1)


if __name__ == '__main__':
    main()
