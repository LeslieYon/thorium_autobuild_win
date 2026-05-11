#!/usr/bin/env python3
# -*- coding: utf-8 -*-

# Copyright (c) 2026 The Thorium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
"""
Thorium packaging script for Microsoft Windows

Creates installer and zip archive from build output.
Supports SIMD variant naming (sse3, sse4, avx, avx2).
"""

import sys
if sys.version_info.major < 3:
    raise RuntimeError('Python 3 is required for this script.')

import argparse
import os
import platform
import re
from pathlib import Path
import shutil

_ROOT_DIR = Path(__file__).resolve().parent
_UNGOOGLED_UTILS_DIR = _ROOT_DIR / 'ungoogled-chromium-windows' / 'ungoogled-chromium' / 'utils'

sys.path.insert(0, str(_UNGOOGLED_UTILS_DIR))
import filescfg
from _common import ENCODING
sys.path.pop(0)


def _get_chromium_version():
    return (_ROOT_DIR / 'chromium_version.txt').read_text(encoding=ENCODING).strip()


def _get_release_revision():
    revision_path = Path(__file__).resolve().parent / 'revision.txt'
    if revision_path.exists():
        return revision_path.read_text(encoding=ENCODING).strip()
    return '0'


def _get_thorium_revision():
    revision_path = Path(__file__).resolve().parent / 'revision.txt'
    if revision_path.exists():
        return revision_path.read_text(encoding=ENCODING).strip()
    return _get_release_revision()


_cached_target_cpu = None


def _get_target_cpu(build_outputs):
    global _cached_target_cpu
    if not _cached_target_cpu:
        args_gn = build_outputs / 'args.gn'
        if args_gn.exists():
            with open(args_gn, 'r') as f:
                args_gn_text = f.read()
            for cpu in ('x64', 'x86', 'arm64'):
                if f'target_cpu="{cpu}"' in args_gn_text:
                    _cached_target_cpu = cpu
                    break
        if not _cached_target_cpu:
            _cached_target_cpu = 'x64'
    return _cached_target_cpu


def _get_simd_variant(build_outputs):
    """Detect SIMD variant from args.gn in the build output directory."""
    args_gn = build_outputs / 'args.gn'
    if args_gn.exists():
        with open(args_gn, 'r') as f:
            content = f.read()
        if re.search(r'^\s*use_avx2\s*=\s*true\s*$', content, re.MULTILINE):
            return 'AVX2'
        elif re.search(r'^\s*use_avx\s*=\s*true\s*$', content, re.MULTILINE):
            return 'AVX'
        elif (re.search(r'^\s*use_sse41\s*=\s*true\s*$', content, re.MULTILINE) or
              re.search(r'^\s*use_sse42\s*=\s*true\s*$', content, re.MULTILINE)):
            return 'SSE4'
        elif re.search(r'^\s*use_sse3\s*=\s*true\s*$', content, re.MULTILINE):
            return 'SSE3'
    return 'AVX2'


def main():
    """Entrypoint"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        '--cpu-arch', metavar='ARCH',
        default=platform.architecture()[0],
        choices=('64bit', '32bit'),
        help=('Filter build outputs by a target CPU. '
              'Default (from platform.architecture()): %(default)s'))
    parser.add_argument(
        '--simd',
        choices=('sse3', 'sse4', 'avx', 'avx2'), default=None,
        help='SIMD variant of the build output to package. '
             'Auto-detected from args.gn if not specified.')
    parser.add_argument(
        '--build-dir',
        default=None,
        help='Build output directory (relative to build/src/out/). '
             'Auto-resolved from --simd if not specified.')
    args = parser.parse_args()

    # Resolve build output directory
    if args.build_dir:
        output_dir_name = args.build_dir
    elif args.simd:
        output_dir_name = 'thorium_' + args.simd
    else:
        output_dir_name = 'thorium_avx2'

    build_outputs = Path('build/src/out') / output_dir_name
    if not build_outputs.exists():
        print('Build output directory not found:', build_outputs)
        sys.exit(1)

    # Determine version info
    chromium_version = 'unknown'
    try:
        chromium_version = _get_chromium_version()
    except Exception as e:
        print('Warning: Could not determine Chromium version:', e)

    release_revision = _get_release_revision()
    thorium_revision = _get_thorium_revision()
    target_cpu = _get_target_cpu(build_outputs)
    simd_variant = args.simd.upper() if args.simd else _get_simd_variant(build_outputs)

    dest_dir = Path('build')
    dest_dir.mkdir(parents=True, exist_ok=True)

    # Copy mini_installer
    mini_installer_src = build_outputs / 'mini_installer.exe'
    if mini_installer_src.exists():
        installer_name = 'thorium_{simd}_{ver}-{rel}.{pkg}_installer_{cpu}.exe'.format(
            simd=simd_variant.lower(),
            ver=chromium_version,
            rel=release_revision,
            pkg=thorium_revision,
            cpu=target_cpu)
        shutil.copyfile(mini_installer_src, dest_dir / installer_name)
        print('Created installer:', installer_name)
    else:
        print('Warning: mini_installer.exe not found')

    # Get timestamp
    timestamp = None
    try:
        with open('build/src/build/util/LASTCHANGE.committime', 'r') as ct:
            timestamp = int(ct.read())
    except (FileNotFoundError, ValueError):
        pass

    # Create zip archive
    output_zip = dest_dir / 'thorium_{simd}_{ver}-{rel}.{pkg}_windows_{cpu}.zip'.format(
        simd=simd_variant.lower(),
        ver=chromium_version,
        rel=release_revision,
        pkg=thorium_revision,
        cpu=target_cpu)

    excluded_files = set([
        Path('mini_installer.exe'),
        Path('mini_installer_exe_version.rc'),
        Path('setup.exe'),
        Path('chrome.packed.7z'),
    ])

    try:
        files_generator = filescfg.filescfg_generator(
            Path('build/src/chrome/tools/build/win/FILES.cfg'),
            build_outputs, args.cpu_arch, excluded_files)
        filescfg.create_archive(
            files_generator, tuple(), build_outputs, output_zip, timestamp)
        print('Created archive:', output_zip)
    except Exception as e:
        print('Error creating archive:', e)

    print('Packaging completed.')


if __name__ == '__main__':
    main()
