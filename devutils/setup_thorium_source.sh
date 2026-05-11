#!/bin/bash -eux

ROOT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)

python "$ROOT_DIR/build.py" --prepare-only "$@"
