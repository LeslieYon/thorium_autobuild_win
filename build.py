#!/usr/bin/env python3
# -*- coding: utf-8 -*-

# Copyright (c) 2026 The Thorium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
"""
Thorium build script for Microsoft Windows

This script automates the process of:
1. Downloading Chromium source code
2. Applying Thorium-specific patches
3. Configuring build flags per SIMD variant
4. Compiling Thorium browser for a specific SIMD variant

SIMD Variants:
  sse3  - SSE3 only (older CPUs, e.g. Pentium 4, AMD K8)
  sse4  - SSE4.1 + SSE4.2 (most Intel Core 2 and newer)
  avx   - AVX (Intel Sandy Bridge / AMD Bulldozer and newer)
  avx2  - AVX2 (Intel Haswell / AMD Excavator and newer)

Usage:
  python3 build.py                    # Build AVX2 variant (default)
  python3 build.py --simd avx         # Build AVX variant
  python3 build.py --simd sse4 --x86  # Build 32-bit SSE4 variant
"""

import sys
import time
import argparse
import os
import re
import shutil
import subprocess
import ctypes
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / 'ungoogled-chromium' / 'utils'))
import downloads
import domain_substitution
import prune_binaries
import patches as uc_patches
from _common import ENCODING, USE_REGISTRY, ExtractorEnum, get_logger
sys.path.pop(0)

_ROOT_DIR = Path(__file__).resolve().parent
_PATCH_BIN_RELPATH = Path('third_party/git/usr/bin/patch.exe')

# Thorium patch directory
_THORIUM_PATCH_DIR = _ROOT_DIR / 'patches' / 'thorium'
_UNGOOGLED_PATCH_DIR = _ROOT_DIR / 'patches' / 'ungoogled-chromium'

# Default Chromium version file
_CHROMIUM_VERSION_FILE = _ROOT_DIR / 'chromium_version.txt'


def _get_chromium_version():
    """
    Read the target Chromium version from chromium_version.txt.
    Returns the version string (e.g. '138.0.7204.306') or None if not found.
    """
    if _CHROMIUM_VERSION_FILE.exists():
        version = _CHROMIUM_VERSION_FILE.read_text(encoding=ENCODING).strip()
        if version:
            return version
    return None


def _checkout_chromium_version(source_tree, version):
    """
    Checkout a specific Chromium version/tag in the source tree.
    This ensures the source matches the version our patches are designed for.
    """
    if not version:
        get_logger().warning('No Chromium version specified. Using HEAD.')
        return

    get_logger().info('Checking out Chromium tag: %s', version)
    try:
        # Try checking out as a tag first
        subprocess.run(
            ['git', 'checkout', 'tags/' + version],
            cwd=source_tree,
            check=True,
            capture_output=True,
            encoding=ENCODING)
        get_logger().info('Successfully checked out tag: tags/%s', version)
    except subprocess.CalledProcessError:
        try:
            # Fall back to branch name
            subprocess.run(
                ['git', 'checkout', version],
                cwd=source_tree,
                check=True,
                capture_output=True,
                encoding=ENCODING)
            get_logger().info('Successfully checked out: %s', version)
        except subprocess.CalledProcessError as exc:
            get_logger().warning(
                'Could not checkout version %s: %s\n'
                'Continuing with current HEAD. Patches may not apply correctly.',
                version, exc)


def _get_vcvars_path(name='64'):
    """
    Returns the path to the corresponding vcvars*.bat path

    As of VS 2017, name can be one of: 32, 64, all, amd64_x86, x86_amd64
    """
    vswhere_exe = '%ProgramFiles(x86)%\\Microsoft Visual Studio\\Installer\\vswhere.exe'
    result = subprocess.run(
        '"{}" -products * -prerelease -latest -property installationPath'.format(vswhere_exe),
        shell=True,
        check=True,
        stdout=subprocess.PIPE,
        universal_newlines=True)
    vcvars_path = Path(result.stdout.strip(), 'VC/Auxiliary/Build/vcvars{}.bat'.format(name))
    if not vcvars_path.exists():
        raise RuntimeError(
            'Could not find vcvars batch script in expected location: {}'.format(vcvars_path))
    return vcvars_path


def _run_build_process(*args, **kwargs):
    """
    Runs the subprocess with the correct environment variables for building
    """
    cmd_input = ['call "%s" >nul' % _get_vcvars_path()]
    cmd_input.append('set DEPOT_TOOLS_WIN_TOOLCHAIN=0')
    cmd_input.append(' '.join(map('"{}"'.format, args)))
    cmd_input.append('exit\n')
    subprocess.run(('cmd.exe', '/k'),
                   input='\n'.join(cmd_input),
                   check=True,
                   encoding=ENCODING,
                   **kwargs)


def _run_build_process_timeout(*args, timeout):
    """
    Runs the subprocess with timeout for CI environments
    """
    cmd_input = ['call "%s" >nul' % _get_vcvars_path()]
    cmd_input.append('set DEPOT_TOOLS_WIN_TOOLCHAIN=0')
    cmd_input.append(' '.join(map('"{}"'.format, args)))
    cmd_input.append('exit\n')
    with subprocess.Popen(('cmd.exe', '/k'), encoding=ENCODING, stdin=subprocess.PIPE,
                          creationflags=subprocess.CREATE_NEW_PROCESS_GROUP) as proc:
        proc.stdin.write('\n'.join(cmd_input))
        proc.stdin.close()
        try:
            proc.wait(timeout)
            if proc.returncode != 0:
                raise RuntimeError('Build failed!')
        except subprocess.TimeoutExpired:
            print('Sending keyboard interrupt')
            for _ in range(3):
                ctypes.windll.kernel32.GenerateConsoleCtrlEvent(1, proc.pid)
                time.sleep(1)
            try:
                proc.wait(10)
            except Exception:
                proc.kill()
            raise KeyboardInterrupt


def _make_tmp_paths():
    """Creates TMP and TEMP variable dirs so ninja won't fail"""
    tmp_path = Path(os.environ['TMP'])
    if not tmp_path.exists():
        tmp_path.mkdir()
    tmp_path = Path(os.environ['TEMP'])
    if not tmp_path.exists():
        tmp_path.mkdir()


def _apply_thorium_patches(source_tree, patch_bin_path):
    """
    Apply all Thorium-specific patches to the source tree.
    Patches are organized by category in patches/thorium/<category>/
    The series file defines the order of patch application.
    """
    series_file = _THORIUM_PATCH_DIR.parent / 'series'
    if not series_file.exists():
        get_logger().warning('No series file found at %s', series_file)
        return

    # Read patch list from series file
    patch_entries = []
    with open(series_file, 'r', encoding=ENCODING) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith('#'):
                patch_entries.append(line)

    # Generate patch paths from series entries
    patch_paths = []
    for entry in patch_entries:
        # Entry format: thorium/<category>/<patch-name>.patch
        patch_path = _ROOT_DIR / 'patches' / entry
        if not patch_path.exists():
            get_logger().warning('Patch file not found: %s', patch_path)
            continue
        patch_paths.append(patch_path)

    if not patch_paths:
        get_logger().warning('No Thorium patches to apply')
        return

    get_logger().info('Applying %d Thorium patches...', len(patch_paths))
    uc_patches.apply_patches(
        uc_patches.generate_patches_from_list(patch_paths),
        source_tree,
        patch_bin_path=patch_bin_path
    )
    get_logger().info('Thorium patches applied successfully.')


def _apply_source_overrides(source_tree):
    """
    Apply ALL source overrides from lib/overlay/ to the Chromium source tree.
    
    This is the unified mechanism for three types of modifications:
    1. OVERWRITE existing files — modified versions of Chromium files
       (e.g. chrome/browser/chrome_content_browser_client.cc)
    2. CREATE new files — added files/directories that don't exist in Chromium
       (e.g. libjxl, highway, Thorium flag definitions, branding images)
    3. DELETE removed files — handled via pruning.list (files to remove)
    
    The lib/overlay/ directory mirrors the Chromium source tree structure.
    At copy time, if the target file already exists it's an overwrite;
    if not, it's a new file creation.
    """
    overlay_src = _ROOT_DIR / 'lib' / 'overlay'
    if not overlay_src.exists():
        get_logger().info('No source overrides in lib/overlay/')
        return

    get_logger().info('Applying source overrides from lib/overlay/...')
    
    # Walk through overlay directory and copy each file
    new_count = 0
    overwrite_count = 0
    for f in overlay_src.rglob('*'):
        if not f.is_file():
            continue
        rel = f.relative_to(overlay_src)
        dst = source_tree / rel
        
        # Create parent directory if needed
        dst.parent.mkdir(parents=True, exist_ok=True)
        
        # Copy the file
        shutil.copy2(f, dst)
        
        if (source_tree / rel).exists():
            overwrite_count += 1
        else:
            new_count += 1

    get_logger().info('Source overrides applied: %d overwritten, %d new files',
                      overwrite_count, new_count)


def _read_flags_file(filepath):
    """Read a GN flags file and return as string."""
    if filepath.exists():
        return filepath.read_text(encoding=ENCODING)
    return ''


def _append_google_api_keys(gn_flags):
    """
    Append Google API key GN flags from environment variables.
    
    Supports two sources (in priority order):
    1. Environment variables: GOOGLE_API_KEY, GOOGLE_DEFAULT_CLIENT_ID,
       GOOGLE_DEFAULT_CLIENT_SECRET
    2. .env file in project root (KEY=VALUE format)
    
    These are required for Thorium to access Google services
    (syncing, geolocation, etc.).
    """
    # Try reading from .env file first
    env_file = _ROOT_DIR / '.env'
    env_vars = {}
    if env_file.exists():
        try:
            for line in env_file.read_text(encoding=ENCODING).splitlines():
                line = line.strip()
                if line and not line.startswith('#'):
                    key, _, value = line.partition('=')
                    env_vars[key.strip()] = value.strip()
        except Exception:
            pass

    # Then check environment variables (they override .env)
    for env_key, gn_key in [
        ('GOOGLE_API_KEY', 'google_api_key'),
        ('GOOGLE_DEFAULT_CLIENT_ID', 'google_default_client_id'),
        ('GOOGLE_DEFAULT_CLIENT_SECRET', 'google_default_client_secret'),
    ]:
        value = os.environ.get(env_key) or env_vars.get(env_key, '')
        if value:
            gn_flags.append('{0}="{1}"'.format(gn_key, value))
            get_logger().info('Using Google API key: %s (from env)', env_key)
        else:
            get_logger().warning(
                'Google API key %s not set. Online features may be limited.\n'
                '  Set %s or create .env file.',
                gn_key, env_key)

    return gn_flags


def _setup_rust_toolchain(source_tree):
    """Setup Rust toolchain for building."""
    HOST_CPU_IS_64BIT = sys.maxsize > 2**32
    RUST_DIR_DST = source_tree / 'third_party' / 'rust-toolchain'
    RUST_DIR_SRC64 = source_tree / 'third_party' / 'rust-toolchain-x64'
    RUST_DIR_SRC86 = source_tree / 'third_party' / 'rust-toolchain-x86'
    RUST_DIR_SRCARM = source_tree / 'third_party' / 'rust-toolchain-arm'
    RUST_FLAG_FILE = RUST_DIR_DST / 'INSTALLED_VERSION'

    if RUST_FLAG_FILE.exists():
        get_logger().info('Rust toolchain already set up.')
        return

    DIRS_TO_COPY = ['bin', 'lib']
    for rust_dir_src in [RUST_DIR_SRC64, RUST_DIR_SRC86, RUST_DIR_SRCARM]:
        if not rust_dir_src.exists():
            continue
        for dir_to_copy in DIRS_TO_COPY:
            if (dir_to_copy == 'bin') and (HOST_CPU_IS_64BIT != (rust_dir_src == RUST_DIR_SRC64)):
                continue
            target_dir = RUST_DIR_DST / dir_to_copy
            if not os.path.isdir(target_dir):
                os.makedirs(target_dir)
            for cp_src in rust_dir_src.glob(f'*/{dir_to_copy}/*'):
                cp_dst = target_dir / cp_src.name
                if cp_src.is_dir():
                    shutil.copytree(cp_src, cp_dst, dirs_exist_ok=True)
                else:
                    shutil.copy2(cp_src, cp_dst)

    # Generate version file
    rustc_path = source_tree / 'third_party' / 'rust-toolchain-x64' / 'rustc' / 'bin' / 'rustc.exe'
    if rustc_path.exists():
        with open(RUST_FLAG_FILE, 'w') as f:
            subprocess.run([str(rustc_path), '--version'], stdout=f)


def main():
    """CLI Entrypoint"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        '--disable-ssl-verification',
        action='store_true',
        help='Disables SSL verification for downloading')
    parser.add_argument(
        '--7z-path',
        dest='sevenz_path',
        default=USE_REGISTRY,
        help=('Command or path to 7-Zip\'s "7z" binary. If "_use_registry" is '
              'specified, determine the path from the registry. Default: %(default)s'))
    parser.add_argument(
        '--winrar-path',
        dest='winrar_path',
        default=USE_REGISTRY,
        help=('Command or path to WinRAR\'s "winrar.exe" binary. If "_use_registry" is '
              'specified, determine the path from the registry. Default: %(default)s'))
    parser.add_argument(
        '-j', type=int, dest='thread_count',
        help='Number of CPU threads to use for compiling')
    parser.add_argument(
        '--ci', action='store_true',
        help='CI mode: skip steps if already done, use timeout for build')
    parser.add_argument(
        '--x86', action='store_true',
        help='Build for 32-bit x86')
    parser.add_argument(
        '--arm', action='store_true',
        help='Build for ARM64')
    parser.add_argument(
        '--simd', choices=('sse3', 'sse4', 'avx', 'avx2'), default='avx2',
        help='SIMD instruction set variant to build for (default: avx2)')
    parser.add_argument(
        '--tarball', action='store_true',
        help='Use Chromium tarball instead of git clone')
    parser.add_argument(
        '--prepare-only', action='store_true',
        help='Only prepare source (download, patch, etc.), skip build')
    parser.add_argument(
        '--gn-only', action='store_true',
        help='Only run GN gen, skip source prep and ninja build')
    parser.add_argument(
        '--build-only', action='store_true',
        help='Only run ninja build, skip source prep and GN gen')
    args = parser.parse_args()

    # Read target Chromium version from chromium_version.txt
    chromium_version = _get_chromium_version()
    if chromium_version:
        get_logger().info('Target Chromium version: %s (from chromium_version.txt)', chromium_version)
    else:
        get_logger().warning(
            'chromium_version.txt not found. Build version will not be pinned.\n'
            'Create this file with the Chromium version string (e.g. "138.0.7204.306").')

    # Set common variables
    source_tree = _ROOT_DIR / 'build' / 'src'
    downloads_cache = _ROOT_DIR / 'build' / 'download_cache'
    output_dir_name = 'thorium_' + args.simd
    output_dir = source_tree / 'out' / output_dir_name

    # ----- Stage: Prepare Source -----
    if args.gn_only or args.build_only:
        # Skip source preparation when in stage mode
        pass
    elif not args.ci or not (source_tree / 'BUILD.gn').exists():
        # Setup environment
        source_tree.mkdir(parents=True, exist_ok=True)
        downloads_cache.mkdir(parents=True, exist_ok=True)
        _make_tmp_paths()

        # Extractors
        extractors = {
            ExtractorEnum.SEVENZIP: args.sevenz_path,
            ExtractorEnum.WINRAR: args.winrar_path,
        }

        # Prepare source folder
        if args.tarball or args.ci:
            get_logger().info('Downloading Chromium tarball...')
            download_info = downloads.DownloadInfo(
                [_ROOT_DIR / 'ungoogled-chromium' / 'downloads.ini'])
            downloads.retrieve_downloads(download_info, downloads_cache, None, True,
                                         args.disable_ssl_verification)
            try:
                downloads.check_downloads(download_info, downloads_cache, None)
            except downloads.HashMismatchError as exc:
                get_logger().error('File checksum does not match: %s', exc)
                sys.exit(1)

            get_logger().info('Unpacking Chromium tarball...')
            downloads.unpack_downloads(download_info, downloads_cache, None,
                                       source_tree, extractors)
            if chromium_version:
                get_logger().info('Using tarball version. Expected Chromium version: %s', chromium_version)
        else:
            subprocess.run([
                sys.executable,
                str(Path('lib', 'ungoogled', 'utils', 'clone.py')),
                '-o', 'build\\src',
                '-p', 'win32' if args.x86 else 'win-arm64' if args.arm else 'win64'
            ], check=True)

            # After cloning, checkout the pinned Chromium version
            if chromium_version:
                _checkout_chromium_version(source_tree, chromium_version)

        # Retrieve Windows-specific downloads
        get_logger().info('Downloading required files...')
        download_info_win = downloads.DownloadInfo([_ROOT_DIR / 'downloads.ini'])
        downloads.retrieve_downloads(download_info_win, downloads_cache, None, True,
                                     args.disable_ssl_verification)
        try:
            downloads.check_downloads(download_info_win, downloads_cache, None)
        except downloads.HashMismatchError as exc:
            get_logger().error('File checksum does not match: %s', exc)
            sys.exit(1)

        # Prune binaries
        pruning_list = (_ROOT_DIR / 'ungoogled-chromium' / 'pruning.list') if args.tarball \
            else (_ROOT_DIR / 'pruning.list')
        unremovable_files = prune_binaries.prune_files(
            source_tree,
            pruning_list.read_text(encoding=ENCODING).splitlines()
        )
        if unremovable_files:
            get_logger().error('Files could not be pruned: %s', unremovable_files)
            parser.exit(1)

        # Unpack downloads
        DIRECTX = source_tree / 'third_party' / 'microsoft_dxheaders' / 'src'
        ESBUILD = source_tree / 'third_party' / 'devtools-frontend' / 'src' / 'third_party' / 'esbuild'
        if DIRECTX.exists():
            shutil.rmtree(DIRECTX)
            DIRECTX.mkdir()
        if ESBUILD.exists():
            shutil.rmtree(ESBUILD)
            ESBUILD.mkdir()
        get_logger().info('Unpacking downloads...')
        downloads.unpack_downloads(download_info_win, downloads_cache, None,
                                   source_tree, extractors)

        # Apply ungoogled-chromium patches
        get_logger().info('Applying ungoogled-chromium patches...')
        uc_patches.apply_patches(
            uc_patches.generate_patches_from_series(
                _UNGOOGLED_PATCH_DIR, resolve=True),
            source_tree,
            patch_bin_path=(source_tree / _PATCH_BIN_RELPATH)
        )

        # Apply source overrides (overwrite + create new)
        _apply_source_overrides(source_tree)

        # Apply Thorium-specific patches
        _apply_thorium_patches(
            source_tree,
            patch_bin_path=(source_tree / _PATCH_BIN_RELPATH)
        )

        # Substitute domains
        domain_substitution_list = (_ROOT_DIR / 'ungoogled-chromium' / 'domain_substitution.list') \
            if args.tarball else (_ROOT_DIR / 'domain_substitution.list')
        domain_substitution.apply_substitution(
            _ROOT_DIR / 'ungoogled-chromium' / 'domain_regex.list',
            domain_substitution_list,
            source_tree, None
        )

    # ----- Stage: GN Gen -----
    if args.prepare_only:
        get_logger().info('--prepare-only specified. Skipping GN gen and build.')
    else:
        # Setup Rust toolchain (needed before GN gen)
        _setup_rust_toolchain(source_tree)

        if not args.ci or not output_dir.exists():
            # Create output directory and args.gn
            output_dir.mkdir(parents=True, exist_ok=True)
            gn_flags = _read_flags_file(_ROOT_DIR / 'ungoogled-chromium' / 'flags.gn')
            gn_flags += '\n'
            windows_flags = _read_flags_file(_ROOT_DIR / 'flags.windows.gn')
            # Add SIMD-specific flags for the chosen variant
            simd_flags = _read_flags_file(_ROOT_DIR / ('flags.windows.' + args.simd + '.gn'))
            if simd_flags:
                get_logger().info('Using SIMD flags for variant: %s', args.simd)
                windows_flags += '\n' + simd_flags
            if args.x86:
                windows_flags = re.sub(r'target_cpu="x64"', 'target_cpu="x86"', windows_flags)
                x86_flags = _read_flags_file(_ROOT_DIR / 'flags.windows.x86.gn')
                windows_flags += '\n' + x86_flags
            elif args.arm:
                windows_flags = re.sub(r'target_cpu="x64"', 'target_cpu="arm64"', windows_flags)
                arm64_flags = _read_flags_file(_ROOT_DIR / 'flags.windows.arm64.gn')
                windows_flags += '\n' + arm64_flags
            if args.tarball:
                windows_flags += '\nchrome_pgo_phase=0\n'
            gn_flags += windows_flags
            
            # Append Google API keys from environment variables
            gn_flags_lines = gn_flags.split('\n')
            gn_flags_lines = _append_google_api_keys(gn_flags_lines)
            gn_flags = '\n'.join(gn_flags_lines)
            
            (output_dir / 'args.gn').write_text(gn_flags, encoding=ENCODING)

        # Enter source tree to run build commands
        os.chdir(source_tree)

        if not args.ci or not (output_dir / 'gn.exe').exists():
            get_logger().info('Bootstrapping GN...')
            _run_build_process(
                sys.executable, 'tools\\gn\\bootstrap\\bootstrap.py',
                '-o', 'out\\%s\\gn.exe' % output_dir_name, '--skip-generate-buildfiles')

            get_logger().info('Running gn gen...')
            _run_build_process('out\\%s\\gn.exe' % output_dir_name,
                               'gen', 'out\\%s' % output_dir_name,
                               '--fail-on-unused-args')

        if not args.ci or not (source_tree / 'third_party' / 'rust-toolchain' / 'bin' / 'bindgen.exe').exists():
            get_logger().info('Building bindgen...')
            _run_build_process(
                sys.executable, 'tools\\rust\\build_bindgen.py', '--skip-test')

    # ----- Stage: Ninja Build -----
    if args.gn_only or args.prepare_only:
        if args.gn_only:
            get_logger().info('--gn-only specified. Skipping ninja build.')
        os.chdir(_ROOT_DIR)
    else:
        # Ninja commandline
        ninja_commandline = ['third_party\\ninja\\ninja.exe']
        if args.thread_count is not None:
            ninja_commandline.append('-j')
            ninja_commandline.append(str(args.thread_count))
        ninja_commandline.append('-C')
        ninja_commandline.append('out\\%s' % output_dir_name)
        ninja_commandline.append('chrome')
        ninja_commandline.append('chromedriver')
        ninja_commandline.append('mini_installer')

        # Run ninja build
        get_logger().info('Starting Thorium build for SIMD variant: %s', args.simd)
        if args.ci:
            _run_build_process_timeout(*ninja_commandline, timeout=3.5 * 60 * 60)
            get_logger().info('Build completed. Running packaging...')
            os.chdir(_ROOT_DIR)
            subprocess.run([sys.executable, 'package.py', '--simd', args.simd])
        else:
            _run_build_process(*ninja_commandline)
        os.chdir(_ROOT_DIR)

    if not args.prepare_only:
        get_logger().info('Thorium build completed successfully!')


if __name__ == '__main__':
    main()
