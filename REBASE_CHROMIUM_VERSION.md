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

### 4. Update ./patches/series.ungoogled-windows and ./patches/series

### 5. check for ungoogled project if have any new fix-build patch, and apply them in our project.

## Version History

| Date | Chromium Version | Thorium Revision | Notes |
|------|-----------------|------------------|-------|
| 2026-03 | 138.0.7204.306 | 1 | Initial port |
