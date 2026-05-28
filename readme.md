# Thorium Autobuild Win

Thorium Autobuild Win reorganizes Thorium's Chromium changes into a standard patch-and-overlay layout and provides a Windows build pipeline on top of the `ungoogled-chromium-windows` submodule.

**Pinned Chromium version**: `138.0.7204.310` (see `chromium_version.txt`)
**Submodule**: `ungoogled-chromium-windows` at `138.0.7204.168-1.1` (1 commit ahead)

## Layout

- `build.py` — main Windows build driver (supports `--simd`, `--ci`, `--x86`, `--arm64`)
- `build_all.py` — local multi-variant helper (builds all 4 SIMD variants)
- `package.py` — installer packager (ZIP archive creation disabled by default, enable with `--zip`)
- `chromium_version.txt` — pinned Chromium base version
- `revision.txt` — release revision number
- `downloads.ini` — extra download dependencies (e.g. libjxl source)
- `pruning.list` — Thorium-specific file removal list
- `keeping.list` — protects files from upstream pruning (e.g. signin, safe_browsing)
- `flags.windows.gn` — SIMD-agnostic GN build flags
- `flags.windows.{sse3,sse4,avx,avx2}.gn` — per-variant SIMD overrides
- `flags.windows.x86.gn` / `flags.windows.arm64.gn` — architecture flags
- `overlay/` — direct source overrides and binary/resource replacements
- `patches/series` — Thorium patch order
- `patches/series.external` — external patches from multiple projects (ungoogled-windows, cromite, etc.)
- `patches/README.md` — patch catalog with per-patch descriptions
- `patches/thorium/` — patch files organized by group: `original/`, `new/`, `fixes/`, `fixes_upstream/`
- `patch_scripts/` — build helper scripts (brand string sync, etc.)
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
Chromium source (git clone)
  → Read & lock chromium_version.txt
  → Download Windows dependencies
  → Apply ungoogled-chromium-windows/pruning.list
  → Apply root pruning.list
  → Unpack downloads (including libjxl → third_party/libjxl/src)
  → Apply patches/series.external (external patches: ungoogled-windows, cromite, etc.)
  → Copy overlay/ into source tree
  → Apply patches/series (Thorium patches)
  → Sync Brand Strings (patch_scripts/sync_brand_strings.py)
      • Phase 1: replace brand strings in GRD/GRDP files
      • Phase 2: synchronise XTB translations with new IDs
  → GN gen + Ninja build
```

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
```

`package.py` only produces the installer (`.exe`) by default. Use `package.py --zip` to additionally create a ZIP archive.

## CI/CD Workflows

| Workflow | Trigger | Description |
|----------|---------|-------------|
| `build-x64.yml` | `push tags`, `workflow_dispatch` | Prepares then builds 2 SIMD variants in parallel |
| `build-x86.yml` | `push tags`, `workflow_dispatch` | Single x86 variant build |
| `build-arm64.yml` | `push tags`, `workflow_dispatch` | Single ARM64 variant build |
| `reusable-build.yml` | Called by other workflows | Multi-stage build (up to 16 stages, ~4h budget each, artifact relay to bypass 6h GitHub limit) |
| `publish-release.yml` | After all builds succeed | Downloads all artifacts and creates GitHub Release |

Google API credentials are passed from repository secrets to the reusable build workflow:

- `GOOGLE_API_KEY`
- `GOOGLE_DEFAULT_CLIENT_ID`
- `GOOGLE_DEFAULT_CLIENT_SECRET`
- ... and other keys

## Patch Inventory

All Thorium patches live under `patches/thorium/` and are applied in the
order defined by `patches/series`. They are organized into four groups:

| Group | Directory | Description |
|-------|-----------|-------------|
| Upstream Fixes | `patches/thorium/fixes_upstream/` | Crash fixes that can be removed once upstreamed |
| Standalone Fixes | `patches/thorium/fixes/` | SafeBrowsing deps, VR, absl symbol fix |
| Original | `patches/thorium/original/` | Multi-file patches ported from Thorium's `setup.sh` |
| Merged New | `patches/thorium/new/` | Reorganized patches by purpose (generated from source diffs) |

See [`patches/README.md`](./patches/README.md) for the complete patch catalog
with per-patch descriptions and purpose.

### Brand String Sync

Replacing `Google Chrome` → `Thorium`, `Chromium` → `Thorium`, etc. in 
GRD/GRDP files. This script runs after all Thorium patches have been applied:

- **Phase 1**: Scans GRD/GRDP `<message>` blocks whose resource IDs match
  `BRAND_STRING_IDS` (2337 IDs from `brand_string_ids.py`) and applies brand
  substitutions to message bodies and `desc`/`meaning` attributes.
- **Phase 2**: Computes pre- and post-replacement translation IDs using grit's
  MD5 fingerprint algorithm, then synchronises every language's XTB file with
  the new translation entries.

This approach keeps the branding patches minimal — they only need to cover
structural changes (e.g. adding `<part>` includes, renaming strings), while
the actual text substitution is done in code.

See [`patch_scripts/sync_brand_strings.md`](./patch_scripts/sync_brand_strings.md) for more information.

## Source Pruning

Pruning is layered:
1. `ungoogled-chromium-windows/pruning.list` is applied first
2. Root `pruning.list` is applied for Thorium-only removals

Missing pruning targets are logged as warnings and do not stop the build.
Thorium builds do **not** run ungoogled domain substitution by default.

## License

BSD 3-Clause. See [Chromium LICENSE](https://chromium.googlesource.com/chromium/src/+/refs/heads/main/LICENSE) for the full text.

This project is a fork of Thorium browser. All source code in this repository is subject to the terms of the BSD 3-Clause License, same as Chromium and Thorium.
