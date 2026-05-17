# Development Utilities

Scripts in this directory assist with patching workflow, diagnostics, and
maintenance of the Thorium autobuild project.

## Quick Start

```cmd
:: Dry-run: check all patches independently (fast, no source changes)
python devutils\simulate_patching.py

:: Sequential: apply in build.py order, then revert (more accurate)
python devutils\simulate_patching.py --sequential
```

## Script Reference

### `simulate_patching.py`

Simulate the `build.py` patching process on a local Chromium source tree
without running the actual build. Verifies all patches (ungoogled + Thorium),
checks pruning lists, and analyzes overlay file coverage.

**Two modes:**

| Mode | Flag | Description |
|------|------|-------------|
| Dry-run | *(default)* | `git apply --check` on each patch independently. Fast, no source modification. |
| Sequential | `--sequential` | Applies patches in series order, accumulating changes. Then reverts. More accurate but slower. |

**Options:**

| Flag | Default | Description |
|------|---------|-------------|
| `--source-dir PATH` | `../chromium` | Path to the Chromium source tree |
| `--git-cmd PATH` | Auto-detected | Path to the git executable |
| `--quick` | — | Skip pruning & overlay checks; test patches only |
| `--sequential` | — | Enable sequential mode |

**Examples:**

```cmd
:: Default dry-run against ../chromium
python devutils\simulate_patching.py

:: Sequential mode with custom source tree
python devutils\simulate_patching.py --sequential --source-dir D:\chromium\src

:: Quick check (patches only)
python devutils\simulate_patching.py --quick

:: Use depot_tools' bundled git
python devutils\simulate_patching.py --git-cmd ..\..\depot_tools\git\bin\git.exe
```

### `make_patch.py`

Create a single Thorium patch from uncommitted changes in `build/src/`.
Automatically classifies the patch, generates the correctly-named file in
`patches/thorium/<category>/`, and appends to `patches/series`.

**Use when** you've modified a file in `build/src/` and need to turn it into a
proper Thorium patch without manual bookkeeping.

```cmd
:: From the project root:
python devutils\make_patch.py chrome/browser/foo.cc
python devutils\make_patch.py --category media third_party/libjxl/BUILD.gn
python devutils\make_patch.py --dry-run chrome/browser/foo.cc
python devutils\make_patch.py --no-series chrome/browser/foo.cc
```

| Flag | Description |
|------|-------------|
| `--category`, `-c` | Force a category (auto-detected by default) |
| `--no-series` | Skip adding the entry to `patches/series` |
| `--dry-run`, `-n` | Show what would be done without writing |
| `--src-dir PATH` | Path to `build/src/` (default: `<root>/build/src`) |
| `--git-cmd PATH` | Path to git executable (auto-detected) |

**Note:** The file must have uncommitted changes in `build/src/` (i.e. after
overlay and patches have been applied). The script reads the diff via `git diff`
and strips the unstable `diff --git` / `index` header lines.

### Other Scripts

| Script | Purpose |
|--------|---------|
| `generate_patches.py` | Generate Thorium patch files from diff between chromium/ and thorium/src |
| `migrate_patches.py` | Migrate/update patch series when rebasing |
| `batch_generate_patches.py` | Batch generate patches for multiple categories |
| `check_patch_files.sh` | Shell script to validate patch series integrity |

## Notes

- All scripts expect to be run from the project root (`thorium_autobuild_win/`)
- Sequential mode modifies the source tree temporarily, then reverts via
  `git checkout -- .` and `git clean -fd`
- The `--source-dir` must point to a git checkout of Chromium
