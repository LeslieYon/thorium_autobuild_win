# GRD/XTB Rebase

This directory contains the Thorium GRD/GRDP and XTB rebase tooling
for moving Thorium string changes out of the overlay and into repeatable
scripts.

The runtime surface is intentionally small:

- `sync_grd_strings.py` updates reviewed Chromium GRD/GRDP messages, computes
  old and new GRIT translation IDs, and copies compatible upstream XTB
  translations to the new Thorium IDs.
- `merge_thorium_xtb.py` merges reviewed Thorium-owned translation additions
  from `config/xtb_additions.tsv` into Chromium XTB bundles.
- `update_config_from_patches.py` refreshes low-risk config rows that can be
  derived from the current patch series.

These scripts use only the Python standard library. They do not require
`vpython`, `depot_tools`, or a Chromium checkout's Python wrapper. Python 3.11
or newer is the supported runtime.

## Configuration

The files in `config/` are reviewed inputs, not generated setup output:

- `file_allowlist.csv`: reviewed GRD/GRDP file scope and file ownership role.
  `from_overlay` records the legacy source of the reviewed change; pure
  `overlay_text_sync` files do not need to remain under `src/` once their
  messages are covered by automatic branding discovery or
  `message_allowlist.csv`.
- `message_allowlist.csv`: reviewed message-level exceptions and special
  replacements. Plain branding replacements are auto-discovered from
  `file_allowlist.csv` text-sync files; this CSV only keeps special rows.
- `feature_patch_message_ownership.csv`: feature-patch and overlay-added
  message ownership; used to prevent feature-patch strings from being handled
  by the overlay replacement workflow.
- `xtb_additions.tsv`: canonical reviewed translation additions (currently
  empty; populated when feature-patch messages need translations).

`update_config_from_patches.py` may rewrite
`config/feature_patch_message_ownership.csv` and
`config/file_allowlist.csv` by default. It does not rewrite
`message_allowlist.csv` or `xtb_additions.tsv`; those remain reviewed
inputs because they contain special text behavior or translation-data
decisions.

## Differences from the legacy sync_brand_strings.py

The legacy `patch_scripts/sync_brand_strings.py` used:

- A hard-coded `GRD_XTB_MAP` list
- A static `BRAND_STRING_IDS` set (2227 IDs, manually maintained)
- `xml.etree.ElementTree` for XML parsing (fragile XTB serialization)
- No concept of feature-patch message ownership

The new `sync_grd_strings.py` improves on this with:

- **Configuration-driven file mapping**: `file_allowlist.csv` defines which
  files to process and how they map to XTB files.
- **Auto-discovery**: Branding messages are discovered by applying the
  branding replacements to the source text and checking which messages change.
  This eliminates the need to manually maintain the ID set.
- **Feature-patch isolation**: Messages owned by feature patches (GPC, download
  shelf restore, etc.) are automatically excluded from branding replacement.
- **Better GRIT ID replica**: Handles `use_name_for_id="true"`, `<ph>` placeholder
  extraction, and `meaning` attribute combination.
- **Conflict detection**: When multiple old translation IDs converge to the same
  new ID, conflicts are detected and reported.
- **Machine-readable reporting**: Dry-run mode outputs TSV format for audit.

## Run Order

Run the scripts after Chromium and non-string feature patches are in place:

1. Run `sync_grd_strings.py`.
2. Run `merge_thorium_xtb.py`.

This order keeps overlay-derived old/new ID syncing separate from reviewed
Thorium-owned additions.

## Python Runtime

Use any Python 3.11+ interpreter available on the host:

```bash
python3 patch_scripts/grd_rebase/sync_grd_strings.py --help
python3 patch_scripts/grd_rebase/merge_thorium_xtb.py --help
```

On Windows, either `py -3.11`, a normal `python.exe`, or
`C:\src\depot_tools\python3.bat` can be used.

All config paths stored in this directory use repository-relative POSIX-style
paths. Command-line paths may use native platform separators or `/`; the scripts
normalize them internally where needed.

## Dry Run

Refresh low-risk config from the current patch series:

```bash
python3 patch_scripts/grd_rebase/update_config_from_patches.py --dry-run
```

Dry-run the overlay string sync:

```bash
python3 patch_scripts/grd_rebase/sync_grd_strings.py \
  /path/to/chromium/src \
  --file-allowlist patch_scripts/grd_rebase/config/file_allowlist.csv \
  --message-allowlist patch_scripts/grd_rebase/config/message_allowlist.csv \
  --dry-run \
  --xtb-conflict-report out/grd_rebase/xtb_conflicts_summary.tsv \
  --xtb-missing-report out/grd_rebase/xtb_missing_summary.tsv \
  > out/grd_rebase/grd_sync_dry_run.tsv
```

Dry-run the reviewed additions merge:

```bash
python3 patch_scripts/grd_rebase/merge_thorium_xtb.py \
  /path/to/chromium/src \
  --dry-run
```

Equivalent PowerShell form:

```powershell
py -3.11 patch_scripts/grd_rebase/sync_grd_strings.py `
  C:\src\chromium\src `
  --file-allowlist patch_scripts/grd_rebase/config/file_allowlist.csv `
  --message-allowlist patch_scripts/grd_rebase/config/message_allowlist.csv `
  --dry-run `
  --xtb-conflict-report out/grd_rebase/xtb_conflicts_summary.tsv `
  --xtb-missing-report out/grd_rebase/xtb_missing_summary.tsv `
  > out/grd_rebase/grd_sync_dry_run.tsv
```

## Apply

Refresh low-risk config from the current patch series:

```bash
python3 patch_scripts/grd_rebase/update_config_from_patches.py
```

Apply overlay GRD/GRDP replacements and copied XTB translations:

```bash
python3 patch_scripts/grd_rebase/sync_grd_strings.py \
  /path/to/chromium/src \
  --file-allowlist patch_scripts/grd_rebase/config/file_allowlist.csv \
  --message-allowlist patch_scripts/grd_rebase/config/message_allowlist.csv
```

Apply reviewed XTB additions:

```bash
python3 patch_scripts/grd_rebase/merge_thorium_xtb.py \
  /path/to/chromium/src
```

All apply operations are designed to be idempotent.

## GRIT ID Notes

`sync_grd_strings.py` contains a lightweight GRIT message ID replica for
auto-discovered branding messages and reviewed special messages. It matches
Chromium's `GenerateMessageId()` fingerprint and meaning-combination behavior:

- MD5 first 64 bits interpreted as signed.
- Optional `meaning` fingerprint combined with the message fingerprint.
- The high bit is stripped to produce a positive decimal ID.
- `use_name_for_id="true"` returns the message name.
- `<ph name="...">` uses the placeholder presentation/name in presentable
  content.

## Reports

`sync_grd_strings.py` can write compact audit reports:

- `--xtb-conflict-report`: summarized converged new-ID conflicts where multiple
  old translations map to the same new ID. The script deterministically keeps
  the first candidate and reports grouped review buckets instead of every
  locale row.
- `--xtb-missing-report`: summarized mapped XTB lookups where the old Chromium
  translation ID was not found. Missing translations are reported but do not
  block the run.

## Integration with build.py

The new scripts are designed to be called from `build.py` after non-branding
patches have been applied:

```python
from patch_scripts.grd_rebase.sync_grd_strings import main as sync_grd_main
# Or use the CLI directly:
# python3 patch_scripts/grd_rebase/sync_grd_strings.py ...
```

The legacy `patch_scripts/sync_brand_strings.py` and
`patch_scripts/brand_string_ids.py` remain in the repository for reference
but should be replaced by the new workflow in a future cleanup.
