# Thorium Autobuild Win

Thorium Autobuild Win reorganizes Thorium's Chromium changes into a standard patch-and-overlay layout and provides a Windows build pipeline on top of the `ungoogled-chromium-windows` submodule.

**Pinned Chromium version**: `138.0.7204.306` (see `chromium_version.txt`)
**Submodule**: `ungoogled-chromium-windows` at tag `138.0.7204.183-1.1`

## Layout

- `build.py` — main Windows build driver (supports `--simd`, `--ci`, `--x86`, `--arm64`)
- `build_all.py` — local multi-variant helper (builds all 4 SIMD variants)
- `package.py` — installer and archive packager
- `chromium_version.txt` — pinned Chromium base version
- `revision.txt` — release revision number (currently 1)
- `downloads.ini` — extra download dependencies (e.g. libjxl source)
- `pruning.list` — Thorium-specific file removal list
- `flags.windows.gn` — SIMD-agnostic GN build flags
- `flags.windows.{sse3,sse4,avx,avx2}.gn` — per-variant SIMD overrides
- `flags.windows.x86.gn` / `flags.windows.arm64.gn` — architecture flags
- `overlay/` — direct source overrides and binary/resource replacements
- `patches/series` — Thorium patch order (225 entries)
- `patches/series.ungoogled-windows` — whitelisted ungoogled-windows patches (20 entries)
- `patches/thorium/` — 225 generated Thorium patches organized by category
- `devutils/` — patch maintenance helpers
- `ungoogled-chromium-windows/` — external Windows support submodule

## Build

```cmd
python build.py --simd avx2          # Build AVX2 variant (default)
python build.py --simd sse4 --x86    # Build x86 SSE4 variant
python build_all.py                  # Build all 4 x64 SIMD variants
```

The build scripts read the Chromium version from `chromium_version.txt` and use the `ungoogled-chromium-windows` submodule for the Chromium download, patch, and packaging helpers.

### Build Flow

```
Chromium source (tarball / git clone)
  → Read & lock chromium_version.txt
  → Download Windows dependencies
  → Apply ungoogled-chromium-windows/pruning.list
  → Apply root pruning.list
  → Unpack downloads (including libjxl → third_party/libjxl/src)
  → Apply ungoogled-chromium-windows patches (whitelist only)
  → Copy overlay/ into source tree
  → Apply patches/series (Thorium patches)
  → GN gen + Ninja build
```

In CI, `build.py --ci` tries the official Chromium source tarball first for speed. If that tarball is missing or cannot be retrieved for the pinned version, it automatically falls back to cloning the matching Chromium git tag.

### SIMD Variants (x64)

| Variant | ISA | Output Directory |
|---------|-----|------------------|
| `sse3` | SSE3 | `out/thorium_sse3` |
| `sse4` | SSE4.1 + SSE4.2 | `out/thorium_sse4` |
| `avx` | SSE4 + AVX | `out/thorium_avx` |
| `avx2` (default) | SSE4 + AVX + AVX2 | `out/thorium_avx2` |

### Output Files

```
build/thorium_sse3_<version>-<release>.<pkg>_installer_x64.exe
build/thorium_sse4_<version>-<release>.<pkg>_installer_x64.exe
build/thorium_avx_<version>-<release>.<pkg>_installer_x64.exe
build/thorium_avx2_<version>-<release>.<pkg>_installer_x64.exe
build/thorium_<...>_windows_x64.zip
```

`package.py` also generates corresponding packages for x86 and ARM64 targets.

## CI/CD Workflows

| Workflow | Trigger | Description |
|----------|---------|-------------|
| `build-x64.yml` | `push tags`, `workflow_dispatch` | Prepares then builds 4 SIMD variants in parallel |
| `build-x86.yml` | `push tags`, `workflow_dispatch` | Single x86 variant build |
| `build-arm64.yml` | `push tags`, `workflow_dispatch` | Single ARM64 variant build |
| `reusable-build.yml` | Called by other workflows | Multi-stage build (up to 9 stages, ~4h budget each, artifact relay to bypass 6h GitHub limit) |
| `publish-release.yml` | After all builds succeed | Downloads all artifacts and creates GitHub Release |

Google API credentials are passed from repository secrets to the reusable build workflow:

- `GOOGLE_API_KEY`
- `GOOGLE_DEFAULT_CLIENT_ID`
- `GOOGLE_DEFAULT_CLIENT_SECRET`

## Patch Inventory

`patches/thorium/` contains **225 per-file patches** organized by category:

| Category | Count | Description |
|----------|-------|-------------|
| fixes | 100 | Bug fixes and compatibility patches |
| ui | 28 | UI changes, dark mode, etc. |
| branding | 27 | Thorium branding elements |
| config | 24 | Build configuration patches |
| features | 30 | Feature patches (FTP, GPC, parallel download, etc.) |
| media | 7 | Codec support (HEVC, AC3, JPEG XL) |
| compiler | 6 | SIMD, LTO, LLVM optimizations |
| windows | 2 | Windows-specific patches |
| privacy | 1 | Privacy sandbox, DoH, DNT |
| **Total** | **225** | |

All 225 `patches/series` entries match the 225 patch files on disk. The patches were generated from diffing `thorium/src` against a pristine `chromium/` checkout, decomposing the original 27 multi-file `.patch` files into individual file-level diffs.

## Source Pruning

Pruning is layered:
1. `ungoogled-chromium-windows/pruning.list` is applied first
2. Root `pruning.list` is applied for Thorium-only removals

Missing pruning targets are logged as warnings and do not stop the build.
Thorium builds do **not** run ungoogled domain substitution by default.

## Maintenance

```bash
# Verify patch series consistency (all entries exist on disk)
./devutils/check_patch_files.sh

# Regenerate patches from source tree diff
python devutils/generate_patches.py --thorium-src ../thorium/src --chromium-src ../chromium

# Dry-run patch migration from original Thorium patches
python devutils/migrate_patches.py --dry-run
```
