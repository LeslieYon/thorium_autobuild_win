# Chromium Version Management

## Current Version

**Chromium Base:** `138.0.7204.306`

## How to Update

### 1. Update the version file

Edit `chromium_version.txt` with the new Chromium version number.

### 2. Update patches

After updating the Chromium base, some patches may need to be rebased:

```bash
# Generate fresh patches against new Chromium version
python3 devutils/generate_patches.py \
    --thorium-src ./thorium/src \
    --chromium-src /path/to/chromium/src \
    --output ./patches/thorium
```

### 3. Verify patch application

```bash
./devutils/check_patch_files.sh
```

### 4. Update ./patches/series.external and ./patches/series

### 5. Check for upstream changes in external projects (ungoogled-chromium-windows, cromite, etc.) and update patches/series.external accordingly.

## Version History

| Date | Chromium Version | Thorium Revision | Notes |
|------|-----------------|------------------|-------|
| 2026-03 | 138.0.7204.306 | 1 | Initial port |
