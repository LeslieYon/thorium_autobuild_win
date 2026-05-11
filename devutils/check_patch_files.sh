#!/bin/bash
set -euo pipefail

ROOT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
SERIES_FILE="$ROOT_DIR/patches/series"
PATCH_ROOT="$ROOT_DIR/patches"

if [[ ! -f "$SERIES_FILE" ]]; then
  echo "Missing patch series file: $SERIES_FILE" >&2
  exit 1
fi

missing=0
while IFS= read -r entry; do
  entry=${entry%$'\r'}
  [[ -z "$entry" || "$entry" == \#* ]] && continue
  if [[ ! -f "$PATCH_ROOT/$entry" ]]; then
    echo "Missing patch: $PATCH_ROOT/$entry" >&2
    missing=1
  fi
done < "$SERIES_FILE"

if [[ "$missing" -ne 0 ]]; then
  exit 1
fi

echo "Patch series is consistent."
