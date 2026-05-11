#!/usr/bin/env python3
"""Generate Thorium overlay files and patch series from two source trees."""

from __future__ import annotations

import argparse
import difflib
import hashlib
import os
import shutil
import sys
from pathlib import Path

TEXT_EXTENSIONS = {
    '',
    '.1', '.ac', '.args', '.bat', '.bzl', '.c', '.cc', '.cfg', '.cmake', '.cmd', '.css',
    '.def', '.gni', '.gn', '.grd', '.grdp', '.h', '.hh', '.hpp', '.html', '.icon', '.idl',
    '.inc', '.inl', '.isolate', '.js', '.json', '.list', '.md', '.mm', '.mojom', '.pakinfo',
    '.patch', '.pem', '.pl', '.policy', '.proto', '.py', '.rc', '.rs', '.sh', '.sql', '.story',
    '.template', '.ts', '.txt', '.typemap', '.xml', '.yaml', '.yml',
}

BINARY_OVERLAY_EXTENSIONS = {
    '.7z', '.bin', '.bmp', '.br', '.bz2', '.dat', '.dll', '.dylib', '.eot', '.exe',
    '.gif', '.gz', '.icc', '.ico', '.icns', '.jar', '.jpeg', '.jpg', '.m4a', '.mp3', '.mp4',
    '.msi', '.ogg', '.otf', '.pak', '.pb', '.pdf', '.png', '.profdata', '.so', '.svgz', '.tar',
    '.tflite', '.ttf', '.wasm', '.webm', '.webp', '.woff', '.woff2', '.xz', '.zip',
}

TEXT_OVERLAY_EXTENSIONS = {
    '.css', '.html', '.icon', '.js', '.json', '.md', '.menu', '.release', '.svg', '.template',
    '.ts', '.ver', '.xml', '.yml', '.yaml',
}

BINARY_EXTENSIONS = {
    '.7z', '.bin', '.bmp', '.br', '.bz2', '.class', '.dat', '.dll', '.dylib', '.eot', '.exe',
    '.gif', '.gz', '.icc', '.ico', '.jar', '.jpeg', '.jpg', '.m4a', '.mp3', '.mp4', '.msi',
    '.ogg', '.otf', '.pak', '.pb', '.pdf', '.png', '.profdata', '.so', '.svgz', '.tar', '.tflite',
    '.ttf', '.wasm', '.webm', '.webp', '.woff', '.woff2', '.xz', '.zip',
}

IGNORED_SUFFIXES = {'.bak', '.orig', '.rej', '.tmp', '.old'}

OVERLAY_PATH_MARKERS = {
    '/app/theme/',
    '/app/vector_icons/',
    '/browser/resources/media/',
    '/browser/resources/',
    '/browser/ui/',
    '/chrome/app/theme/',
    '/chrome/app/vector_icons/',
    '/chrome/browser/resources/',
    '/chrome/browser/ui/webui/',
    '/chrome/browser/ui/',
    '/components/',
    '/extensions/',
    '/ui/resources/',
    '/ui/webui/',
    '/webui/',
    '/ash/webui/',
    '/test/data/',
    '/tests/data/',
    '/third_party/libjxl/',
    '/third_party/highway/',
}

CATEGORY_RULES = (
    ('build/config/', 'compiler'),
    ('third_party/ffmpeg/', 'media'),
    ('media/', 'media'),
    ('ui/', 'ui'),
    ('chrome/browser/ui/', 'ui'),
    ('chrome/app/theme/', 'branding'),
    ('chrome/app/vector_icons/', 'branding'),
    ('chrome/app/thorium', 'branding'),
    ('components/search_engines/', 'search'),
    ('chrome/browser/search', 'search'),
    ('components/privacy_sandbox/', 'privacy'),
    ('chrome/browser/privacy_sandbox/', 'privacy'),
    ('chrome/installer/win/', 'windows'),
    ('chrome/installer/mini_installer/', 'windows'),
    ('sandbox/win/', 'windows'),
    ('build/win/', 'windows'),
    ('net/', 'features'),
    ('content/', 'features'),
    ('extensions/', 'features'),
    ('third_party/libjxl/', 'media'),
    ('third_party/highway/', 'media'),
)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--thorium-src', type=Path, required=True,
                        help='Path to the Thorium modified Chromium source subset.')
    parser.add_argument('--chromium-src', type=Path, required=True,
                        help='Path to the clean Chromium source tree.')
    parser.add_argument('--output-root', type=Path, default=Path.cwd(),
                        help='Project root that contains overlay/ and patches/.')
    parser.add_argument('--clean', action='store_true',
                        help='Remove generated overlay and Thorium patches before writing.')
    parser.add_argument('--dry-run', action='store_true',
                        help='Print statistics without writing files.')
    return parser.parse_args(argv)


def rel_posix(path: Path) -> str:
    return path.as_posix()


def is_binary_bytes(data: bytes) -> bool:
    if b'\0' in data:
        return True
    sample = data[:8192]
    if not sample:
        return False
    controls = sum(byte < 9 or (13 < byte < 32) for byte in sample)
    return controls / len(sample) > 0.30


def is_text_candidate(path: Path, data: bytes) -> bool:
    suffix = path.suffix.lower()
    if suffix in BINARY_EXTENSIONS:
        return False
    if suffix in TEXT_EXTENSIONS:
        return not is_binary_bytes(data)
    return not is_binary_bytes(data)


def should_overlay(rel_path: Path, thorium_data: bytes, chromium_exists: bool) -> bool:
    rel = '/' + rel_posix(rel_path)
    suffix = rel_path.suffix.lower()
    if not chromium_exists:
        return True
    if suffix in BINARY_OVERLAY_EXTENSIONS or suffix in TEXT_OVERLAY_EXTENSIONS:
        return True
    if any(marker in rel for marker in OVERLAY_PATH_MARKERS):
        return not is_text_candidate(rel_path, thorium_data)
    return not is_text_candidate(rel_path, thorium_data)


def category_for(rel_path: Path) -> str:
    rel = rel_posix(rel_path)
    if rel_path.suffix.lower() in ('.grd', '.grdp'):
        return 'branding'
    for prefix, category in CATEGORY_RULES:
        if rel.startswith(prefix):
            return category
    if rel.endswith(('.gn', '.gni')) or rel.startswith('build/'):
        return 'config'
    return 'fixes'


def slug_for(rel_path: Path) -> str:
    raw = rel_posix(rel_path)
    stem = raw.replace('/', '__').replace(' ', '_')
    stem = ''.join(ch if ch.isalnum() or ch in '._-' else '-' for ch in stem)
    if len(stem) > 140:
        digest = hashlib.sha1(raw.encode('utf-8')).hexdigest()[:10]
        stem = stem[:120] + '__' + digest
    return stem + '.patch'


def decode_text(data: bytes) -> str:
    for encoding in ('utf-8', 'utf-8-sig', 'latin-1'):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise UnicodeDecodeError('utf-8', data, 0, 1, 'unable to decode file')


def normalize_line_endings(text: str) -> str:
    return text.replace('\r\n', '\n').replace('\r', '\n')


def read_text_lines(path: Path) -> list[str]:
    return normalize_line_endings(decode_text(path.read_bytes())).splitlines(keepends=True)


def normalized_text_equal(first_data: bytes, second_data: bytes) -> bool:
    return normalize_line_endings(decode_text(first_data)) == normalize_line_endings(
        decode_text(second_data))


def write_patch(chromium_file: Path, thorium_file: Path, rel_path: Path, patch_file: Path) -> bool:
    old_lines = read_text_lines(chromium_file)
    new_lines = read_text_lines(thorium_file)
    diff = list(difflib.unified_diff(
        old_lines,
        new_lines,
        fromfile='a/' + rel_posix(rel_path),
        tofile='b/' + rel_posix(rel_path),
        n=3,
    ))
    if not diff:
        return False
    patch_file.parent.mkdir(parents=True, exist_ok=True)
    patch_file.write_text(''.join(diff), encoding='utf-8', newline='')
    return True


def copy_overlay(thorium_file: Path, overlay_root: Path, rel_path: Path) -> None:
    destination = overlay_root / rel_path
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(thorium_file, destination)


def iter_files(root: Path) -> list[Path]:
    return sorted(
        path for path in root.rglob('*')
        if path.is_file() and path.suffix.lower() not in IGNORED_SUFFIXES
    )


def clean_outputs(output_root: Path) -> None:
    for path in (output_root / 'overlay', output_root / 'patches' / 'thorium'):
        if path.exists():
            shutil.rmtree(path)
        path.mkdir(parents=True, exist_ok=True)
    series_file = output_root / 'patches' / 'series'
    if series_file.exists():
        series_file.unlink()


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    thorium_src = args.thorium_src.resolve()
    chromium_src = args.chromium_src.resolve()
    output_root = args.output_root.resolve()

    if not thorium_src.exists():
        print(f'Thorium source tree not found: {thorium_src}', file=sys.stderr)
        return 1
    if not chromium_src.exists():
        print(f'Chromium source tree not found: {chromium_src}', file=sys.stderr)
        return 1

    overlay_root = output_root / 'overlay'
    patches_root = output_root / 'patches'
    thorium_patches_root = patches_root / 'thorium'

    if args.clean and not args.dry_run:
        clean_outputs(output_root)
    elif not args.dry_run:
        overlay_root.mkdir(parents=True, exist_ok=True)
        thorium_patches_root.mkdir(parents=True, exist_ok=True)

    stats = {
        'same': 0,
        'overlay_new': 0,
        'overlay_binary_or_asset': 0,
        'patch': 0,
    }
    series_path = patches_root / 'series'
    series_entries: list[str] = []
    if not args.clean and series_path.exists():
        series_entries.extend(
            line.strip() for line in series_path.read_text(encoding='utf-8').splitlines()
            if line.strip() and not line.strip().startswith('#')
        )
    known_series_entries = set(series_entries)

    for thorium_file in iter_files(thorium_src):
        rel_path = thorium_file.relative_to(thorium_src)
        chromium_file = chromium_src / rel_path
        thorium_data = thorium_file.read_bytes()

        if chromium_file.exists():
            chromium_data = chromium_file.read_bytes()
            if chromium_data == thorium_data:
                stats['same'] += 1
                continue
            if (is_text_candidate(rel_path, chromium_data) and
                    is_text_candidate(rel_path, thorium_data) and
                    normalized_text_equal(chromium_data, thorium_data)):
                stats['same'] += 1
                continue

        if should_overlay(rel_path, thorium_data, chromium_file.exists()):
            stats['overlay_new' if not chromium_file.exists() else 'overlay_binary_or_asset'] += 1
            if not args.dry_run:
                copy_overlay(thorium_file, overlay_root, rel_path)
            continue

        category = category_for(rel_path)
        patch_file = thorium_patches_root / category / slug_for(rel_path)
        if chromium_file.exists():
            if not args.dry_run:
                if not write_patch(chromium_file, thorium_file, rel_path, patch_file):
                    stats['same'] += 1
                    continue
            stats['patch'] += 1
            series_entry = 'thorium/' + rel_posix(patch_file.relative_to(thorium_patches_root))
            if series_entry not in known_series_entries:
                series_entries.append(series_entry)
                known_series_entries.add(series_entry)
        else:
            stats['overlay_new'] += 1
            if not args.dry_run:
                copy_overlay(thorium_file, overlay_root, rel_path)

    if not args.dry_run:
        patches_root.mkdir(parents=True, exist_ok=True)
        (patches_root / 'series').write_text('\n'.join(series_entries) + ('\n' if series_entries else ''),
                                             encoding='utf-8', newline='\n')
        (overlay_root / '.gitkeep').touch(exist_ok=True)
        (thorium_patches_root / '.gitkeep').touch(exist_ok=True)

    for key, value in stats.items():
        print(f'{key}: {value}')
    print(f'series entries: {len(series_entries)}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
