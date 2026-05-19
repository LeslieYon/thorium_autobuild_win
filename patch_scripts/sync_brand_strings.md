# Sync Brand Strings — Self-Contained Brand String Synchroniser

This script **replaces** the string-replacement hunks in `patches/thorium/branding/`. It
is fully self-contained — it does not read patch files; instead it scans
`<message>` blocks whose resource ID is in the precise set `BRAND_STRING_IDS`
(defined in `brand_string_ids.py`, extracted from the branding patches) and
applies brand substitutions directly.

## Background

Traditionally, Thorium uses patches to replace `Chromium` → `Thorium` in GRD/GRDP
files. These patches are fragile, conflict-prone, and must be updated on every
Chromium version bump. This script moves the string substitution **from patches
into code**:

1. **Phase 1 — GRD/GRDP replacement**: single pass over `<message>` blocks whose
   resource ID is in `BRAND_STRING_IDS`, replacing brand strings in both the
   message body and the `desc`/`meaning` attribute.
2. **Phase 2 — XTB synchronisation**: for each modified message, compute old and
   new translation IDs using grit's fingerprint algorithm, look up the old ID in
   every language's XTB file, apply the same brand replacements to the translated
   text, and insert a new entry with the new ID.

## File Locations

| File | Description |
|------|-------------|
| `patch_scripts/sync_brand_strings.py` | Main script (self-contained) |
| `patch_scripts/brand_string_ids.py` | Precise resource ID set auto-generated from branding patches |
| `patch_scripts/sync_brand_strings.md` | This documentation |
| `patches/thorium/branding/` | Branding patches (to be cleaned up) |

## Pipeline

```
 ┌─ non-branding patches ──┐
 │  fixes, config, media…  │  ← applied normally
 └─────────┬───────────────┘
           ▼
 ┌──────────────────────────────────┐
 │ sync_brand_strings.py           │
 │  Phase 1: GRD/GRDP replacement  │
 │  Phase 2: XTB synchronisation   │
 └──────────────┬───────────────────┘
                ▼
         build/src/  ready for GN gen
```

## Dependencies

- Python 3.8+
- Standard library: `hashlib`, `xml.etree.ElementTree`, `re`, `copy`
- OS: Windows (POSIX paths in MSYS2 bash shell)

## Configuration

### GRD/GRDP → XTB mapping

Defined in `GRD_XTB_MAP` inside `sync_brand_strings.py`:

| GRD file | XTB path pattern |
|----------|-----------------|
| `chrome/app/chromium_strings.grd` | `chrome/app/resources/chromium_strings_{lang}.xtb` |
| `chrome/app/generated_resources.grd` | `chrome/app/resources/generated_resources_{lang}.xtb` |
| `components/components_chromium_strings.grd` | `components/strings/components_chromium_strings_{lang}.xtb` |
| `components/components_strings.grd` | `components/strings/components_strings_{lang}.xtb` |
| `chromeos/chromeos_strings.grd` | `chromeos/strings/chromeos_strings_{lang}.xtb` |
| `ui/chromeos/ui_chromeos_strings.grd` | `ui/chromeos/translations/ui_chromeos_strings_{lang}.xtb` |
| `android_chrome_strings.grd` | `...translations/android_chrome_strings_{lang}.xtb` |

GRDP files are auto-resolved via `<part file="...">` in the parent GRD.

### Replacement rules (case-sensitive, order matters)

```python
"Google Chrome"       → "Thorium"       # longest match first
"ChromeOS Flex"       → "ThoriumOS"
"ChromeOS"            → "ThoriumOS"
"The Chromium Authors"→ "Alex313031"
"Chromium"            → "Thorium"
r'Chrome(?! Web Store)' → "Thorium"     # regex, excludes "Chrome Web Store"
```

### Pre-replacement cache (for Phase 2 old-ID computation)

The script does **not** use reverse replacement. Instead, it builds an
`_old_text_cache` **before** Phase 1 modifies the GRD/GRDP files:

1. Parse every GRD/GRDP file listed in `GRD_XTB_MAP`.
2. For each `<message>` whose resource ID is in `BRAND_STRING_IDS`, capture
   `(presentable_text, meaning)` — the original Chromium text.
3. Phase 1 writes brand replacements to the GRD/GRDP files.
4. Phase 2 reads the modified files, then looks up each message's original
   text from the cache to compute the old translation ID.

This avoids the ambiguity of reverse string replacement entirely. The old
translation ID is always derived from the **actual original Chromium text**,
not guessed from the post-replacement text.

## Usage

### Command-line

```bash
cd /e/b/thorium_autobuild_win

# Dry run (no files modified)
python3 patch_scripts/sync_brand_strings.py /path/to/chromium --dry-run -v

# Real execution (after non-branding patches)
python3 patch_scripts/sync_brand_strings.py /path/to/build/src
```

### As a module

```python
from patch_scripts.sync_brand_strings import sync_brand_strings
sync_brand_strings(source_tree, dry_run=False)
```

### Arguments

| Argument | Description |
|----------|-------------|
| `source_tree` | Chromium source tree root (required) |
| `-v` / `--verbose` | Debug-level logging |
| `-n` / `--dry-run` | Scan only, do not write any files |

## Build.py Integration

Called after all non-branding Thorium patches have been applied:

```python
# In build.py after _apply_thorium_patches():
from patch_scripts.sync_brand_strings import sync_brand_strings
sync_brand_strings(source_tree, dry_run=False)
```

See the `# ----- Stage: Sync Brand Strings (GRD/GRDP -> XTB) -----` block in
`build.py` for the exact integration.

## Algorithm Details

### Translation ID generation (grit replica)

The ID is produced by replicating `grit.extern.tclib.GenerateMessageId()` and
`grit.extern.FP.FingerPrint()`:

```
unsigned_fp = int(md5(text.encode('utf-8')).hexdigest()[:16], 16)
if unsigned_fp & 0x8000000000000000:
    fp = -((~unsigned_fp & 0xFFFFFFFFFFFFFFFF) + 1)   # signed 64-bit
else:
    fp = unsigned_fp

if meaning:
    fp2 = signed_fingerprint(meaning)
    if fp < 0:
        fp = fp2 + (fp << 1) + 1
    else:
        fp = fp2 + (fp << 1)

id = str(fp & 0x7fffffffffffffff)  # strip high bit → always positive
```

### Presentable Content

The "presentable content" fed to `GenerateMessageId()` is built from the
`<message>` XML:

- Text nodes are kept verbatim.
- `<ph name="X">` elements are replaced by the **uppercased `name` attribute**
  (this is what grit uses as placeholder presentation).
- `<if>` / `<then>` / `<else>` branches are recursed into.

Example:

```xml
<message name="IDS_SESSION_CRASHED_VIEW_UMA_OPTIN" desc="...">
  Help make Chromium better and
  <ph name="UMA_LINK">$1<ex>usage statistics</ex></ph> to Google
</message>
```

Presentable content: `Help make Chromium better and UMA_LINK to Google`

### Old/new text derivation

| Stage | Text | Note |
|-------|------|------|
| Pre-replacement (old) | `Chromium is a web browser...` | Original Chromium text (captured in `_old_text_cache`) |
| Post-replacement (new) | `Thorium is a web browser...` | After Phase 1 |
| Reverse replacement | *(not used)* | Replaced by pre-replacement cache |
| Old translation ID | `GenerateMessageId("Chromium is...", meaning)` | Lookup key in XTB |
| New translation ID | `GenerateMessageId("Thorium is...", meaning)` | Key to insert in XTB |

## XTB File Format

```xml
<?xml version="1.0" ?>
<!DOCTYPE translationbundle>
<translationbundle lang="zh-CN">
<translation id="1026101648481255140">Resume installation</translation>
<translation id="1029669172902658969">Relaunch to update &amp;Chromium OS</translation>
...
</translationbundle>
```

Child elements such as `<ph>` inside `<translation>` are fully preserved —
branding replacement is applied only to text/tail nodes via recursive tree
walking.

## Troubleshooting

### Phase 2 shows 0 messages processed

**Cause**: Running in dry-run mode — Phase 1 did not write changes, so the
GRD files still contain the original text and `old_id == new_id` for every
message.

**Fix**: Run without `--dry-run`. Phase 1 writes first, then Phase 2 reads
the modified GRD files and finds changed messages.

### Old translation ID not found in XTB

**Causes**:
1. The message has `translateable="false"` — it does not appear in XTBs.
2. The pre-replacement cache captured the wrong original text (unlikely, but
   possible if the GRD file was already modified before the script ran).

**Fix**: The script uses the pre-replacement cache to compute the old ID
directly from the original Chromium text, so reverse-replacement ambiguity
does not apply. If the ID is still not found, check whether that language
simply lacks a translation for that entry, or whether the GRD file was
already modified before `sync_brand_strings.py` ran.

### Serialised XTB looks different

**Cause**: ElementTree parsing/serialisation may produce slightly different
whitespace or line endings than the original hand-crafted file.

**Fix**: The custom serializer `_serialize_xtb()` is designed to match the
original style (XML declaration, DOCTYPE, one `<translation>` per line). If
differences remain, inspect with `git diff` — they are typically cosmetic.

### Phase 1 replaces strings it should not

**Cause**: The `r'Chrome(?! Web Store)'` regex may match technical terms
in message text or examples (`<ex>`) that happen to contain the word "Chrome".

**Fix**: Review the message content. If a false positive is found, its
resource ID can be added to a skip-list (not yet implemented — file a bug).
