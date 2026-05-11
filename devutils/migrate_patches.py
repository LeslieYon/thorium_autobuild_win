#!/usr/bin/env python3
"""Generate Thorium patches from the local thorium and chromium source trees."""

from __future__ import annotations

import argparse
from pathlib import Path

from batch_generate_patches import main as generate_main


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--thorium-src', type=Path, default=Path(__file__).resolve().parents[2] / 'thorium' / 'src')
    parser.add_argument('--chromium-src', type=Path, default=Path(__file__).resolve().parents[2] / 'chromium')
    parser.add_argument('--output-root', type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument('--clean', action='store_true')
    parser.add_argument('--dry-run', action='store_true')
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    argv = [
        '--thorium-src', str(args.thorium_src),
        '--chromium-src', str(args.chromium_src),
        '--output-root', str(args.output_root),
    ]
    if args.clean:
        argv.append('--clean')
    if args.dry_run:
        argv.append('--dry-run')
    return generate_main(argv)


if __name__ == '__main__':
    raise SystemExit(main())
