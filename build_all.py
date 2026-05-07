#!/usr/bin/env python3
# -*- coding: utf-8 -*-

# Copyright (c) 2026 The Thorium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
"""
Thorium multi-SIMD build script for Microsoft Windows

Builds all 4 SIMD variants (SSE3, SSE4, AVX, AVX2) sequentially.
The Chromium source preparation (download, patch, etc.) is shared
across all variants to save time; only the GN+ninja steps are repeated.

Usage:
  python3 build_all.py                     # Build all 4 variants
  python3 build_all.py -j 8                # Use 8 parallel threads
  python3 build_all.py --ci                # CI mode
"""

import sys
import argparse
import subprocess
from pathlib import Path

_ROOT_DIR = Path(__file__).resolve().parent
_CHROMIUM_VERSION_FILE = _ROOT_DIR / 'chromium_version.txt'


def _get_chromium_version():
    """Read the target Chromium version from chromium_version.txt."""
    if _CHROMIUM_VERSION_FILE.exists():
        return _CHROMIUM_VERSION_FILE.read_text(encoding='utf-8').strip()
    return None

SIMD_VARIANTS = ['sse3', 'sse4', 'avx', 'avx2']

BUILD_STEPS_SHARED = [
    # Step 1: prepare source (download, patch, etc.) using AVX2 as default
    [sys.executable, 'build.py', '--simd', 'avx2', '--prepare-only'],
]

BUILD_STEPS_PER_VARIANT = [
    # Step 2: gn gen for each variant
    [sys.executable, 'build.py', '--simd', '{simd}', '--gn-only'],
    # Step 3: ninja build for each variant
    [sys.executable, 'build.py', '--simd', '{simd}', '--build-only'],
    # Step 4: package for each variant
    [sys.executable, 'package.py', '--simd', '{simd}'],
]


def _run_cmd(cmd, description):
    """Run a command and print its description."""
    print('\n' + '=' * 70)
    print('  {}'.format(description))
    print('  Command: {}'.format(' '.join(cmd)))
    print('=' * 70)
    result = subprocess.run(cmd)
    if result.returncode != 0:
        print('ERROR: {} failed with exit code {}'.format(description, result.returncode))
        sys.exit(result.returncode)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        '-j', type=int, dest='thread_count',
        help='Number of CPU threads to use for compiling')
    parser.add_argument(
        '--ci', action='store_true',
        help='CI mode: pass --ci to build.py')
    parser.add_argument(
        '--skip-prepare', action='store_true',
        help='Skip source preparation (download, patch, etc.)')
    parser.add_argument(
        '--skip-package', action='store_true',
        help='Skip packaging step')
    parser.add_argument(
        '--variants', nargs='+',
        default=SIMD_VARIANTS,
        choices=SIMD_VARIANTS,
        help='SIMD variants to build (default: all 4)')
    args = parser.parse_args()

    variants = args.variants
    extra_args = []
    if args.thread_count is not None:
        extra_args.extend(['-j', str(args.thread_count)])
    if args.ci:
        extra_args.append('--ci')

    chromium_version = _get_chromium_version()
    version_str = chromium_version if chromium_version else 'NOT SET (using HEAD)'

    print('Thorium Multi-SIMD Build')
    print('=' * 70)
    print('Chromium version:  {}'.format(version_str))
    print('Variants to build: {}'.format(', '.join(variants)))
    print('Extra args:        {}'.format(' '.join(extra_args)))
    print()

    # Step 1: Prepare source (shared across all variants)
    if not args.skip_prepare:
        prepare_cmd = [sys.executable, 'build.py']
        if args.ci:
            prepare_cmd.append('--ci')
        prepare_cmd.append('--prepare-only')
        _run_cmd(prepare_cmd, 'Preparing Chromium source (download + patch)')
    else:
        print('\nSkipping source preparation (--skip-prepare)')

    # Steps 2-4: For each SIMD variant, run GN gen, build, and package
    for simd in variants:
        variant_label = '{} ({})'.format(simd.upper(), simd)
        print('\n' + '#' * 70)
        print('#  Building variant: {}'.format(variant_label))
        print('#' * 70)

        # GN gen
        gn_cmd = [sys.executable, 'build.py', '--simd', simd, '--gn-only']
        gn_cmd.extend(extra_args)
        _run_cmd(gn_cmd, 'GN gen for {}'.format(variant_label))

        # Build
        build_cmd = [sys.executable, 'build.py', '--simd', simd, '--build-only']
        build_cmd.extend(extra_args)
        _run_cmd(build_cmd, 'Ninja build for {}'.format(variant_label))

        # Package
        if not args.skip_package:
            pkg_cmd = [sys.executable, 'package.py', '--simd', simd]
            _run_cmd(pkg_cmd, 'Packaging {}'.format(variant_label))

    print('\n' + '=' * 70)
    print('  All variants built successfully!')
    print('  Variants: {}'.format(', '.join(variants)))
    print('  Output: ./build/')
    print('=' * 70)


if __name__ == '__main__':
    main()
