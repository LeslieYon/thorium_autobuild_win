# Thorium Autobuild Win

Thorium Autobuild Win reorganizes Thorium's Chromium changes into a standard patch-and-overlay layout and provides a Windows build pipeline on top of the `ungoogled-chromium-windows` submodule.

## Layout

- `build.py` - main Windows build driver
- `build_all.py` - local multi-variant helper
- `package.py` - installer and archive packager
- `overlay/` - direct source overrides and binary/resource replacements
- `patches/series` - Thorium patch order
- `patches/thorium/` - generated Thorium patches
- `devutils/` - patch maintenance helpers
- `ungoogled-chromium-windows/` - external Windows support submodule

## Build

```cmd
python build.py --simd avx2
python build.py --simd sse4 --x86
python build_all.py
```

The build scripts read the Chromium version from `chromium_version.txt` and use the `ungoogled-chromium-windows` submodule for the Chromium download, patch, and packaging helpers.

In CI, `build.py --ci` tries the official Chromium source tarball first for speed. If that tarball is missing or cannot be retrieved for the pinned version, it automatically falls back to cloning the matching Chromium git tag.

Source pruning is layered: `ungoogled-chromium-windows/pruning.list` is applied first, then the root `pruning.list` is applied for Thorium-only removals. Missing pruning targets are logged as warnings and do not stop the build. Thorium builds do not run ungoogled domain substitution.

## Maintenance

```bash
./devutils/check_patch_files.sh
python devutils/generate_patches.py --thorium-src ../thorium/src --chromium-src ../chromium
python devutils/migrate_patches.py --dry-run
```
