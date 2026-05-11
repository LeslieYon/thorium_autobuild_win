#!/bin/bash -eux

ROOT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)

alias quilt='quilt --quiltrc -'
export QUILT_PATCHES="$ROOT_DIR/patches"
export QUILT_SERIES="series"
export QUILT_PUSH_ARGS="--color=auto"
export QUILT_DIFF_OPTS="--show-c-function"
export QUILT_PATCH_OPTS="--unified --reject-format=unified"
export QUILT_DIFF_ARGS="-p ab --no-timestamps --no-index --color=auto --sort"
export QUILT_REFRESH_ARGS="-p ab --no-timestamps --no-index --sort"
export QUILT_COLORS="diff_hdr=1;32:diff_add=1;34:diff_rem=1;31:diff_hunk=1;33:diff_ctx=35:diff_cctx=33"
export QUILT_SERIES_ARGS="--color=auto"
export QUILT_PATCHES_ARGS="--color=auto"
