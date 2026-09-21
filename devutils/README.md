# Development Utilities

Local maintenance tools for the Thorium patch workflow: simulation, validation
and patch-authoring helpers.

Nothing in this directory runs during `build.py` or in CI.
`patches/series`, `patches/series.external` and `build.py` remain the single
sources of truth for what is applied and in which order — these scripts only
help you verify or regenerate that state.

## Scripts at a glance

| Script | Status | Purpose |
|--------|--------|---------|
| `simulate_patching.py` | active | Replay the whole patch pipeline against a pristine Chromium tree in a scratch directory and report per-patch results. |
| `check_patch_files.sh` | active | Assert that every entry in `patches/series` exists on disk. |
| `setup_thorium_source.sh` | active | Thin wrapper for `python build.py --prepare-only`. |
| `set_quilt_vars.sh` | active | `source` it to point quilt at Thorium's `patches/` + `series`. |
| `fix_fuzz_v3.py` | one-off | Regenerate fuzzed hunks in Thorium patches from a pristine test tree. |
| `generate_patches.py` | broken | Wrapper importing `batch_generate_patches`, which is not in the tree. |
| `find_new_brand_strings.py` | broken | Imports `patch_scripts.brand_string_ids` / `patch_scripts.sync_brand_strings`; that code now lives in `patch_scripts/grd_rebase/`. |

The non-functional files are covered in [Stale scripts](#stale-scripts).

## simulate_patching.py

The primary tool. It reproduces the patching phase of `build.py` without running
a build and **without ever modifying the pristine source tree**.

**Pipeline**

1. Discover patches from `patches/series.external` and `patches/series`.
2. Parse each patch to find the files it touches.
3. Copy only those files from the pristine tree into a scratch directory.
4. Apply in `build.py` order: external patches → `overlay/` → Thorium patches.
5. Print a per-patch result table; exit non-zero if anything failed.

**Limits** — a green run is not a full build rehearsal:

- pruning is only *reported*, never executed (`pruning.list` / `keeping.list`);
- archives listed in `downloads.ini` are not unpacked;
- `build.py`'s safe-browsing patch extraction and brand-string sync are skipped.

**Options** (all optional)

| Flag | Default | Description |
|------|---------|-------------|
| `--source-dir PATH` | `build/src_original/` | Pristine Chromium tree; must contain `BUILD.gn`. |
| `--work-dir PATH` | auto temp dir | Scratch directory. Implies `--keep-work-dir`. |
| `--keep-work-dir` | off | Keep the scratch directory after the run. |
| `--sequential` | off | Stop at the first failing patch instead of running them all. |
| `--skip-overlay` | off | Do not copy `overlay/` files. |
| `--skip-pruning` | off | Skip the pruning report. |
| `--export-json PATH` | off | Write `{"summary", "patches"}` results to JSON. |

By default every patch is attempted and failures are collected, so a single run
shows the whole picture. `--sequential` trades that for an early stop.

**Environment**

| Variable | Purpose |
|----------|---------|
| `PATCH_BIN` | Override the `patch` executable. Resolution order: the bundled `patch.exe` shipped with the build toolchain → `$PATCH_BIN` → `patch` on `PATH`. |

`--source-dir` is read-only. Without `--work-dir` the scratch tree is a
`thorium-simulate-*` temp directory that is removed on exit.

**Examples**

```cmd
:: Full simulation against the default pristine tree
python devutils\simulate_patching.py

:: Fast patch-chain-only check
python devutils\simulate_patching.py --skip-overlay --skip-pruning

:: Inspect the scratch tree after a failure
python devutils\simulate_patching.py --work-dir D:\tmp\patch_test

:: Stop at the first failure
python devutils\simulate_patching.py --sequential

:: Machine-readable results for scripting
python devutils\simulate_patching.py --export-json results.json
```

## check_patch_files.sh

Fails if any patch listed in `patches/series` is missing from disk. Run it after
adding, renaming or dropping patches:

```bash
bash devutils/check_patch_files.sh
```

## setup_thorium_source.sh and set_quilt_vars.sh

- `setup_thorium_source.sh` forwards its arguments to
  `python build.py --prepare-only` — use it to fetch, prune and unpack sources
  without compiling.
- `set_quilt_vars.sh` is meant to be `source`d, not executed. It exports
  `QUILT_PATCHES` / `QUILT_SERIES` so quilt treats Thorium's `patches/`
  directory as its patch stack.

## Stale scripts

Present in the tree but not usable as-is; fix or delete them before relying on
them.

| Script | Problem |
|--------|---------|
| `find_new_brand_strings.py` | Imports `patch_scripts.brand_string_ids` and `patch_scripts.sync_brand_strings`; brand-string handling moved to `patch_scripts/grd_rebase/`. |
| `fix_fuzz_v3.py` | Runs, but expects inputs from an older single-file patch layout (`--json <results> --test-dir <tree>`); not part of the normal workflow. |

## Conventions

- Run commands from the project root.
- `build/src_original/` is the pristine tree the patch tools read from;
  `build/src/` is the working tree that gets modified.
- `devutils/test_new/` is scratch output and is git-ignored.
- Patch authoring rules — generate with `git diff`, CRLF line endings, no
  `diff --git` / `index` headers, exact hunk counts, never a blank line between
  hunks — are documented in [`../patches/README.md`](../patches/README.md).
