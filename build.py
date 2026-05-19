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
import fnmatch
from pathlib import Path
from typing import Any

# Allow external override via THORIUM_BUILD_ROOT environment variable.
# local_build.py sets this to the thorium_autobuild_win directory when
# invoking build.py as a subprocess.
_ROOT_DIR = Path(
    os.environ.get('THORIUM_BUILD_ROOT', str(Path(__file__).resolve().parent))
)
_UNGOOGLED_WINDOWS_DIR = _ROOT_DIR / 'ungoogled-chromium-windows'
_UNGOOGLED_CHROMIUM_DIR = _UNGOOGLED_WINDOWS_DIR / 'ungoogled-chromium'
_UNGOOGLED_UTILS_DIR = _UNGOOGLED_CHROMIUM_DIR / 'utils'

sys.path.insert(0, str(_UNGOOGLED_UTILS_DIR))
import clone as uc_clone  # type: ignore[import-not-found]
import downloads  # type: ignore[import-not-found]
import prune_binaries  # type: ignore[import-not-found]
import patches as _uc_patches  # type: ignore[import-not-found]
from _common import ENCODING, USE_REGISTRY, ExtractorEnum, get_logger  # type: ignore[import-not-found]
sys.path.pop(0)

# Import brand string sync script (runs after Thorium patches to update XTB)
try:
    from patch_scripts.sync_brand_strings import sync_brand_strings
    _HAS_SYNC_BRAND_STRINGS = True
except ImportError:
    _HAS_SYNC_BRAND_STRINGS = False

uc_patches: Any = _uc_patches

_PATCH_BIN_RELPATH = Path('third_party/git/usr/bin/patch.exe')

_THORIUM_PATCH_DIR = _ROOT_DIR / 'patches' / 'thorium'
_THORIUM_SERIES_FILE = _ROOT_DIR / 'patches' / 'series'
_UNGOOGLED_PATCH_DIR = _UNGOOGLED_WINDOWS_DIR / 'patches'
_UNGOOGLED_WINDOWS_SERIES_FILE = _ROOT_DIR / 'patches' / 'series.ungoogled-windows'
_OVERLAY_DIR = _ROOT_DIR / 'overlay'

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


def _first_existing_path(*paths):
    """Return the first existing path, or the last candidate for clear errors."""
    for candidate in paths:
        if candidate.exists():
            return candidate
    return paths[-1]


def _config_file(filename, fallback_dir):
    """Resolve a root override first, then fall back to the submodule copy."""
    return _first_existing_path(_ROOT_DIR / filename, fallback_dir / filename)


def _read_list_file(filepath):
    """Read a line-oriented config file, ignoring blank lines and comments."""
    if not filepath.exists():
        return []
    entries = []
    for line in filepath.read_text(encoding=ENCODING).splitlines():
        line = line.strip()
        if line and not line.startswith('#'):
            entries.append(line)
    return entries


def _prune_files_with_warnings(source_tree, pruning_list, label,
                               keeping_list=None):
    """Prune files and warn, instead of failing, when entries are absent.

    Args:
        source_tree: Root of the Chromium source tree.
        pruning_list: Path to the list of files to delete.
        label: Human-readable label for log messages.
        keeping_list: Optional path to a list of entries from pruning_list
            that must NOT be deleted (e.g. files Thorium needs but the
            upstream ungoogled-chromium-windows pruning removes).
    """
    entries = _read_list_file(pruning_list)
    if not entries:
        get_logger().info('No pruning entries for %s (%s)', label, pruning_list)
        return

    # Apply keeping list: exclude protected entries so they survive pruning.
    # Supports fnmatch wildcards (*, ?, [...]) in keeping list entries.
    if keeping_list is not None:
        keep_entries = _read_list_file(keeping_list)
        if keep_entries:
            before = len(entries)
            # Separate exact and wildcard patterns for efficiency
            exact_patterns = {p for p in keep_entries
                              if '*' not in p and '?' not in p and '[' not in p}
            wild_patterns = [p for p in keep_entries
                             if '*' in p or '?' in p or '[' in p]
            if wild_patterns:
                entries = [
                    e for e in entries
                    if e not in exact_patterns
                    and not any(fnmatch.fnmatch(e, wp) for wp in wild_patterns)
                ]
            else:
                entries = [e for e in entries if e not in exact_patterns]
            skipped = before - len(entries)
            if skipped:
                get_logger().info(
                    'Keeping %d entries from %s (protected by %s)',
                    skipped, label, keeping_list)

    get_logger().info('Pruning %d entries from %s...', len(entries), label)
    missing_files = sorted(prune_binaries.prune_files(source_tree, entries))
    if missing_files:
        preview = ', '.join(missing_files[:10])
        if len(missing_files) > 10:
            preview += ', ...'
        get_logger().warning(
            '%s pruning skipped %d missing files; continuing. Missing: %s',
            label, len(missing_files), preview)


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
        if proc.stdin is None:
            raise RuntimeError('Build process stdin pipe was not created')
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


def _reset_source_tree(source_tree):
    """Remove any partial source tree and recreate the target directory."""
    if source_tree.exists():
        shutil.rmtree(source_tree)
    source_tree.mkdir(parents=True, exist_ok=True)


def _ensure_depot_tools_deps():
    """Ensure Python dependencies needed by depot_tools' gclient are available.

    The ungoogled-chromium depot_tools.patch changes gclient.bat from vpython3
    to python3, bypassing the virtual environment that normally provides httplib2.
    gerrit_util.py (from depot_tools) imports httplib2.socks, which was removed
    in httplib2 >= 0.20.0. We install the compatible version here so gclient
    can run during source preparation (clone.py -> gclient sync).
    """
    try:
        import httplib2.socks  # type: ignore[import-untyped] # noqa: F401
    except ImportError:
        get_logger().info(
            'Installing httplib2==0.19.1 (required by depot_tools gclient)')
        subprocess.check_call([
            sys.executable, '-m', 'pip', 'install',
            '--disable-pip-version-check', 'httplib2==0.19.1'
        ])


class _GclientSafePath(type(Path())):
    """Path subclass with forward-slash __str__ for .gclient compatibility.

    When a Windows path like C:\\thorium-autobuild-win\\build\\src is written
    into the .gclient file (a Python-evaluated config), the ``\\b`` in
    ``\\build`` is interpreted as a backspace escape character.

    This subclass returns forward slashes from __str__() so the path
    ``C:/thorium-autobuild-win/build/src`` never contains problematic
    ``\\``-based escapes when used in Python string literals inside .gclient.
    """
    def __str__(self):
        return super().__str__().replace('\\', '/')


def _clone_chromium_source(source_tree, chromium_version, args):
    """Clone Chromium from git and checkout the pinned version when available."""
    _ensure_depot_tools_deps()
    uc_clone.clone(argparse.Namespace(
        output=_GclientSafePath(source_tree),
        custom_config=None,
        pgo='win32' if args.x86 else 'win-arm64' if args.arm else 'win64',
        sysroot=None,
    ))

    if chromium_version:
        _checkout_chromium_version(source_tree, chromium_version)


def _download_chromium_tarball(source_tree, downloads_cache, extractors, chromium_version,
                               disable_ssl_verification):
    """Download and unpack the Chromium tarball into the source tree."""
    get_logger().info('Downloading Chromium tarball...')
    download_info = downloads.DownloadInfo([_UNGOOGLED_CHROMIUM_DIR / 'downloads.ini'])
    downloads.retrieve_downloads(download_info, downloads_cache, None, True,
                                 disable_ssl_verification)
    downloads.check_downloads(download_info, downloads_cache, None)

    get_logger().info('Unpacking Chromium tarball...')
    downloads.unpack_downloads(download_info, downloads_cache, None,
                               source_tree, extractors)
    if chromium_version:
        get_logger().info('Using tarball version. Expected Chromium version: %s', chromium_version)


def _prepare_chromium_source(source_tree, downloads_cache, extractors, chromium_version, args):
    """Prepare Chromium source using tarball first when appropriate, then fall back to git."""
    use_tarball = args.tarball or (args.ci and chromium_version)

    if use_tarball:
        try:
            _download_chromium_tarball(source_tree, downloads_cache, extractors, chromium_version,
                                       args.disable_ssl_verification)
            return 'tarball'
        except downloads.HashMismatchError as exc:
            if args.tarball:
                get_logger().error('File checksum does not match: %s', exc)
                sys.exit(1)
            get_logger().warning('Chromium tarball checksum failed; falling back to git clone: %s', exc)
        except Exception as exc:  # noqa: BLE001 - fall back to git clone in CI when tarball is unavailable
            if args.tarball:
                raise
            get_logger().warning('Chromium tarball is unavailable; falling back to git clone: %s', exc)

        _reset_source_tree(source_tree)

    get_logger().info('Cloning Chromium source from git...')
    _clone_chromium_source(source_tree, chromium_version, args)
    return 'git'


def _apply_ungoogled_windows_patches(source_tree, patch_bin_path):
    """
    Apply selected ungoogled-chromium-windows patches from the submodule.

    The list of patches to apply is specified in a curated whitelist file
    (patches/series.ungoogled-windows). The whitelist supports section markers
    to resolve patch paths against different base directories:

      #[windows]  (default) — resolve relative to <submodule>/patches/
      #[main]               — resolve relative to <submodule>/ungoogled-chromium/patches/

    This allows prerequisite patches from the main ungoogled-chromium series to
    be referenced without ../ paths.
    """
    series_file = _UNGOOGLED_WINDOWS_SERIES_FILE
    if not series_file.exists():
        get_logger().warning('No whitelist found at %s', series_file)
        return

    # Base directories for each section
    _BASE_DIRS = {
        'windows': _UNGOOGLED_PATCH_DIR,
        'main': _UNGOOGLED_CHROMIUM_DIR / 'patches',
    }

    # Parse entries with section markers
    patch_paths = []
    current_base = _BASE_DIRS['windows']  # default section
    with open(series_file, 'r', encoding=ENCODING) as f:
        for line in f:
            stripped = line.strip()
            if not stripped or stripped.startswith('#'):
                # Check for section marker: #[section_name]
                if stripped.startswith('#[') and stripped.endswith(']'):
                    section = stripped[2:-1].strip()
                    if section in _BASE_DIRS:
                        current_base = _BASE_DIRS[section]
                    else:
                        get_logger().warning('Unknown section in %s: %s',
                                             series_file.name, section)
                continue

            patch_path = (current_base / stripped).resolve()
            if not patch_path.exists():
                get_logger().warning('Whitelisted patch not found: %s', patch_path)
                continue
            patch_paths.append(patch_path)

    if not patch_paths:
        get_logger().warning('No ungoogled-chromium-windows patches to apply')
        return

    get_logger().info('Applying %d ungoogled-chromium-windows patches (from %s)...',
                      len(patch_paths), series_file.name)
    uc_patches.apply_patches(
        patch_paths,
        source_tree,
        patch_bin_path=patch_bin_path
    )
    get_logger().info('ungoogled-chromium-windows patches applied successfully.')


def _apply_thorium_patches(source_tree, patch_bin_path):
    """
    Apply all Thorium-specific patches to the source tree.
    Patches are organized by category in patches/thorium/<category>/
    The series file defines the order of patch application.
    """
    series_file = _THORIUM_SERIES_FILE
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
        patch_paths,
        source_tree,
        patch_bin_path=patch_bin_path
    )
    get_logger().info('Thorium patches applied successfully.')


def _run_safe_browsing_patch_extraction(source_tree):
    """
    Run the safe_browsing patch extraction module.

    This regenerates the auto-generated patch file
    patches/thorium/fixes/autogenerated_remove-safebrowsing-prefs-deps.patch
    by scanning ungoogled-chromium source patches for content related to
    safe_browsing.

    The extraction happens before Thorium patches are applied so that the
    auto-generated patch (listed first in patches/series) is always up to
    date.
    """
    import subprocess

    script = _ROOT_DIR / 'devutils' / 'extract_safebrowsing_patches.py'
    if not script.exists():
        get_logger().warning(
            'Safe browsing extraction script not found: %s', script)
        return

    get_logger().info('Running safe_browsing patch extraction...')
    result = subprocess.run(
        [sys.executable, str(script)],
        capture_output=True, text=True, cwd=_ROOT_DIR)

    if result.returncode != 0:
        get_logger().warning(
            'Safe browsing extraction failed (rc=%d): %s',
            result.returncode, result.stderr.strip())
    elif result.stdout.strip():
        get_logger().info(result.stdout.strip())


def _apply_source_overrides(source_tree):
    """
    Apply ALL source overrides from overlay/ to the Chromium source tree.
    
    This is the unified mechanism for three types of modifications:
    1. OVERWRITE existing files — modified versions of Chromium files
       (e.g. chrome/browser/chrome_content_browser_client.cc)
    2. CREATE new files — added files/directories that don't exist in Chromium
       (e.g. libjxl, highway, Thorium flag definitions, branding images)
    3. DELETE removed files — handled via pruning.list (files to remove)
    
    The overlay/ directory mirrors the Chromium source tree structure.
    At copy time, if the target file already exists it's an overwrite;
    if not, it's a new file creation.
    """
    overlay_src = _OVERLAY_DIR
    if not overlay_src.exists():
        get_logger().info('No source overrides in overlay/')
        return

    get_logger().info('Applying source overrides from overlay/...')
    
    # Walk through overlay directory and copy each file
    new_count = 0
    overwrite_count = 0
    for f in overlay_src.rglob('*'):
        if not f.is_file() or f.name == '.gitkeep':
            continue
        rel = f.relative_to(overlay_src)
        dst = source_tree / rel
        existed_before = dst.exists()
        
        # Create parent directory if needed
        dst.parent.mkdir(parents=True, exist_ok=True)
        
        # Copy the file
        shutil.copy2(f, dst)
        
        if existed_before:
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


def _check_admin():
    """Check if the script is running with Administrator privileges.
    Exit with an error if not running as admin."""
    try:
        is_admin = ctypes.windll.shell32.IsUserAnAdmin()
    except (AttributeError, OSError):
        # Not Windows or ctypes not available, try Unix method
        try:
            is_admin = os.geteuid() == 0
        except AttributeError:
            is_admin = False
    if not is_admin:
        print('ERROR: Administrator privileges required.')
        print('  This script must be run as Administrator.')
        print('  Please restart the terminal/command prompt as Administrator and try again.')
        sys.exit(1)


def main():
    """CLI Entrypoint"""
    _check_admin()
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
        help='CI mode: prefer tarball source preparation, fall back to git clone when needed')
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
    uc_clone.get_chromium_version = _get_chromium_version
    downloads.get_chromium_version = _get_chromium_version
    if chromium_version:
        downloads.DownloadInfo._ini_vars['_chromium_version'] = chromium_version
    else:
        downloads.DownloadInfo._ini_vars.pop('_chromium_version', None)
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

    # Windows MAX_PATH safety check: the longest known generated file subpath
    # is 184 characters (e.g. the Blink v8_union_*_videoframe.cc path under
    # gen/). If output_dir + 184 would exceed 255 (leaving a 5-char margin
    # below the 260-char MAX_PATH limit), abort early with a clear message.
    _MAX_GEN_SUBPATH = 184
    _PATH_WARN_LIMIT = 255
    if len(str(output_dir)) + _MAX_GEN_SUBPATH > _PATH_WARN_LIMIT:
        get_logger().error(
            'Build path too long (%d + %d = %d > %d). Windows MAX_PATH limit '
            'may cause build failures.\n'
            '  Output directory: %s\n'
            '  Move the project to a shorter path (e.g. C:\\thorium) and retry.',
            len(str(output_dir)), _MAX_GEN_SUBPATH,
            len(str(output_dir)) + _MAX_GEN_SUBPATH, _PATH_WARN_LIMIT,
            output_dir)
        sys.exit(1)

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
        _prepare_chromium_source(source_tree, downloads_cache, extractors, chromium_version, args)

        # Retrieve Windows-specific downloads
        get_logger().info('Downloading required files...')
        download_info_win = downloads.DownloadInfo([
            _config_file('downloads.ini', _UNGOOGLED_WINDOWS_DIR)])
        downloads.retrieve_downloads(download_info_win, downloads_cache, None, True,
                                     args.disable_ssl_verification)
        try:
            downloads.check_downloads(download_info_win, downloads_cache, None)
        except downloads.HashMismatchError as exc:
            get_logger().error('File checksum does not match: %s', exc)
            sys.exit(1)

        # Prune binaries. Apply the upstream Windows list first, then Thorium-only additions.
        # The keeping.list file protects entries from the upstream pruning that
        # Thorium still needs (e.g. signin_pref_names removed by ungoogled).
        _prune_files_with_warnings(
            source_tree,
            _UNGOOGLED_WINDOWS_DIR / 'pruning.list',
            'ungoogled-chromium-windows',
            keeping_list=_ROOT_DIR / 'keeping.list')
        _prune_files_with_warnings(
            source_tree,
            _ROOT_DIR / 'pruning.list',
            'Thorium')

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

        # Apply selected ungoogled-chromium-windows patches
        # Uses the curated whitelist in patches/series.ungoogled-windows.
        get_logger().info('Applying ungoogled-chromium-windows patches...')
        _apply_ungoogled_windows_patches(
            source_tree,
            patch_bin_path=(source_tree / _PATCH_BIN_RELPATH)
        )

        # Apply source overrides (overwrite + create new)
        _apply_source_overrides(source_tree)

        # Run safe_browsing patch extraction (regenerates auto-generated patch)
        _run_safe_browsing_patch_extraction(source_tree)

        # Apply Thorium-specific patches
        _apply_thorium_patches(
            source_tree,
            patch_bin_path=(source_tree / _PATCH_BIN_RELPATH)
        )

        # ----- Stage: Sync Brand Strings (GRD/GRDP -> XTB) -----
        # Replaces string-replacement hunks in patches/thorium/branding/.
        # Phase 1: apply brand substitutions directly to GRD/GRDP files.
        # Phase 2: update XTB translation files with new translation IDs.
        # See patch_scripts/sync_brand_strings.md for details.
        if _HAS_SYNC_BRAND_STRINGS:
            get_logger().info('Syncing brand strings in GRD/GRDP -> XTB files...')
            try:
                sync_brand_strings(source_tree, dry_run=False)
                get_logger().info('Brand string sync completed.')
            except Exception as exc:
                get_logger().warning('Brand string sync failed (non-fatal): %s', exc)
        else:
            get_logger().warning(
                'sync_brand_strings module not found. Run this script from the '
                'thorium_autobuild_win root directory.')

    # ----- Stage: GN Gen -----
    if args.prepare_only:
        get_logger().info('--prepare-only specified. Skipping GN gen and build.')
    else:
        # Setup Rust toolchain (needed before GN gen)
        _setup_rust_toolchain(source_tree)

        if not args.ci or not output_dir.exists():
            # Create output directory and args.gn
            output_dir.mkdir(parents=True, exist_ok=True)
            gn_flags = _read_flags_file(_UNGOOGLED_CHROMIUM_DIR / 'flags.gn')
            gn_flags += '\n'
            windows_flags = _read_flags_file(
                _config_file('flags.windows.gn', _UNGOOGLED_WINDOWS_DIR))
            # Add SIMD-specific flags for the chosen variant
            simd_flags = '' if args.arm else _read_flags_file(
                _ROOT_DIR / ('flags.windows.' + args.simd + '.gn'))
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
            try:
                _run_build_process_timeout(*ninja_commandline, timeout=3.5 * 60 * 60)
            except KeyboardInterrupt:
                get_logger().info('Build timed out, will resume in next stage.')
                sys.exit(2)
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
