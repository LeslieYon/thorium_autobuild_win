#!/usr/bin/env python3
"""
Fix fuzz hunks in Thorium patches by regenerating them from test_new state.

Strategy:
1. For each Thorium patch with fuzz:
2. Copy ALL files it modifies from test_new/ to a temp dir
3. Reverse-apply the patch to get the "before" state
4. For each fuzzed file: diff before vs test_new → regenerated patch
5. Replace fuzzed sections in the original patch file

Usage:
  python devutils/fix_fuzz_v3.py --json test_results.json --test-dir test_new
  python devutils/fix_fuzz_v3.py --json test_results.json --test-dir test_new --patch thorium-2024-ui.patch
"""

import sys, os, subprocess, shutil, tempfile, re, json
from pathlib import Path
from typing import List, Tuple, Set, Dict

ROOT_DIR = Path(__file__).resolve().parent.parent
PATCH_CMD = ROOT_DIR.parent / "PortableGit" / "usr" / "bin" / "patch.exe"
DIFF_CMD = ROOT_DIR.parent / "PortableGit" / "usr" / "bin" / "diff.exe"
ENCODING = "UTF-8"

_PATCH_FILE_RE = re.compile(r'^---\s+(?:a/)?(.+?)(?:\t.*)?$')
_PATCH_FILE_DEVNULL_RE = re.compile(r'^---\s+/dev/null')


def warn(msg):
    print(f"  ⚠  {msg}")


def to_native(path: Path) -> str:
    s = str(path.resolve())
    if ':\\' in s or ':/' in s.replace('\\', '/'):
        return s
    try:
        r = subprocess.run(['cygpath', '-w', s], capture_output=True, text=True, timeout=5)
        if r.returncode == 0:
            return r.stdout.strip()
    except: pass
    return s


def extract_files(patch_path: Path) -> Set[str]:
    files = set()
    try:
        content = patch_path.read_text(encoding=ENCODING)
    except: return files
    for line in content.splitlines():
        if _PATCH_FILE_DEVNULL_RE.match(line):
            continue
        m = _PATCH_FILE_RE.match(line)
        if m:
            p = m.group(1).strip()
            if p and p != '/dev/null':
                files.add(p.replace('\\', '/'))
    return files


def apply_patch(patch_path: Path, tree_path: Path) -> Tuple[int, str, str]:
    args = [str(PATCH_CMD), "-p1", "--ignore-whitespace",
            "-i", to_native(patch_path), "-d", to_native(tree_path),
            "--no-backup-if-mismatch", "--forward"]
    r = subprocess.run(args, capture_output=True, text=True, timeout=120)
    return r.returncode, r.stdout, r.stderr


def apply_patch_reverse(patch_path: Path, tree_path: Path) -> Tuple[int, str, str]:
    args = [str(PATCH_CMD), "-p1", "--ignore-whitespace",
            "-i", to_native(patch_path), "-d", to_native(tree_path),
            "--no-backup-if-mismatch", "--reverse", "--forward"]
    r = subprocess.run(args, capture_output=True, text=True, timeout=120)
    return r.returncode, r.stdout, r.stderr


def generate_diff(before_file: Path, after_file: Path, rel_path: str) -> str:
    patch_rel = rel_path.replace('\\', '/')
    r = subprocess.run([str(DIFF_CMD), "-u", to_native(before_file), to_native(after_file)],
                       capture_output=True, text=True, timeout=30)
    if r.returncode not in (0, 1):
        return ""
    lines_out = []
    for line in r.stdout.split('\n'):
        if line.startswith('--- '):
            lines_out.append(f'--- a/{patch_rel}')
        elif line.startswith('+++ '):
            lines_out.append(f'+++ b/{patch_rel}')
        elif line.startswith('diff --git') or line.startswith('index ') \
                or line.startswith('new file mode') or line.startswith('deleted file mode'):
            continue
        else:
            lines_out.append(line)
    text = '\r\n'.join(lines_out)
    return text


def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--json", required=True)
    parser.add_argument("--test-dir", required=True)
    parser.add_argument("--work-dir", default=None)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--patch", default=None)
    args = parser.parse_args()

    json_path = Path(args.json).resolve()
    test_dir = Path(args.test_dir).resolve()
    work_base = Path(args.work_dir).resolve() if args.work_dir else Path(tempfile.mkdtemp(prefix="fix-fuzz-"))
    work_base.mkdir(parents=True, exist_ok=True)

    for cmd in [PATCH_CMD, DIFF_CMD]:
        if not cmd.exists():
            print(f"❌ {cmd} not found"); sys.exit(1)

    print("=" * 70)
    print("  FIX FUZZ PATCHES V3")
    print("=" * 70)
    print(f"  Test dir:   {test_dir}")
    print(f"  Dry run:    {'yes' if args.dry_run else 'no'}")
    print("=" * 70)

    # Parse fuzz info
    with open(json_path, encoding=ENCODING) as f:
        data = json.load(f)
    fuzz_patches = []
    for p in data['patches']:
        if p['label'] != 'thorium' or 'with fuzz' not in p.get('stdout', ''):
            continue
        lines = p['stdout'].split('\n')
        cf, file_fuzzes = None, []
        for line in lines:
            fm = re.search(r'patching file (.+)$', line)
            if fm: cf = fm.group(1); continue
            fzm = re.search(r'Hunk #\d+ succeeded at \d+ with fuzz (\d+)', line)
            if fzm and cf:
                file_fuzzes.append({'file': cf, 'fuzz_level': int(fzm.group(1))})
        fuzz_patches.append({'name': p['name'], 'path': p['path'], 'files': file_fuzzes})

    if args.patch:
        fuzz_patches = [fp for fp in fuzz_patches if args.patch in fp['name'] or args.patch in fp['path']]
    print(f"  Found {len(fuzz_patches)} Thorium patches with fuzz")
    if not fuzz_patches:
        print("  Nothing to fix."); return

    stats = {"ok": 0, "skip": 0, "fail": 0}

    for idx, fp in enumerate(fuzz_patches, 1):
        patch_path = Path(fp['path']).resolve()
        pname = fp['name']
        fuzzed_files = set(f['file'] for f in fp['files'])

        print(f"\n  [{idx}/{len(fuzz_patches)}] {pname} ({len(fuzzed_files)} fuzzed)")

        if args.dry_run:
            for f in sorted(fuzzed_files):
                print(f"     - {f}")
            stats['skip'] += 1
            continue

        # Get all files this patch modifies
        all_pf = extract_files(patch_path)

        # Create before dir with all modified files from test_new
        bd = work_base / f".b_{pname.replace('.patch','')}"
        if bd.exists(): shutil.rmtree(bd)
        bd.mkdir(parents=True)

        for rel in all_pf:
            src = test_dir / rel
            if src.exists():
                dst = bd / rel
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dst)

        # Reverse-apply the patch to get "before" state
        rc_rv, out_rv, err_rv = apply_patch_reverse(patch_path, bd)

        # Generate new diffs for fuzzed files
        new_diffs = {}
        for rel in sorted(fuzzed_files):
            bf = bd / rel
            af = test_dir / rel
            if not bf.exists() or not af.exists():
                warn(f"  Missing file: {rel}")
                continue
            diff = generate_diff(bf, af, rel)
            if diff:
                new_diffs[rel] = diff

        if not new_diffs:
            warn(f"  No diffs generated"); stats['skip'] += 1; continue

        # Build new patch content
        old = patch_path.read_text(encoding=ENCODING)
        old_lines = [l.rstrip('\r') for l in old.split('\n')]
        new_lines, i = [], 0
        while i < len(old_lines):
            line = old_lines[i]
            m = _PATCH_FILE_RE.match(line)
            if m:
                fp = m.group(1).strip().replace('\\', '/')
                if fp in new_diffs:
                    i += 1
                    while i < len(old_lines):
                        nl = old_lines[i]
                        if _PATCH_FILE_RE.match(nl) or _PATCH_FILE_DEVNULL_RE.match(nl):
                            break
                        i += 1
                    new_lines.extend(new_diffs[fp].rstrip('\r\n').split('\n'))
                    continue
            elif line.startswith('diff --git') or line.startswith('index ') \
                    or line.startswith('new file mode') or line.startswith('deleted file mode'):
                i += 1; continue
            new_lines.append(line); i += 1

        new_content = '\n'.join(new_lines) + '\n'
        new_content = new_content.replace('\r\n', '\n').replace('\n', '\r\n')

        # Verify each fuzzed file individually using per-file patches
        all_ok = True
        vd = work_base / f".v_{pname.replace('.patch','')}"
        if vd.exists(): shutil.rmtree(vd)
        vd.mkdir(parents=True)

        patch_env = os.environ.copy()
        for var in ('TMPDIR', 'TMP', 'TEMP'):
            if var not in patch_env or not patch_env[var]:
                patch_env[var] = to_native(Path(tempfile.gettempdir()))

        for rel in sorted(fuzzed_files):
            bf = bd / rel
            if not bf.exists():
                warn(f"  Cannot verify {rel}: before file missing"); all_ok = False; continue

            # Create per-file patch from regenerated diff
            pf = vd / f"{Path(rel).name}.patch"
            pf.write_text(new_diffs[rel], encoding=ENCODING)

            # Copy before file
            vf = vd / rel
            vf.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(bf, vf)

            rc, out, err = apply_patch(pf, vd)
            if rc != 0:
                print(f"     ❌ Verify FAILED for {rel} (rc={rc}): {out[:100]}")
                all_ok = False
            elif 'with fuzz' in out:
                print(f"     ⚠️  Fuzz remaining in {rel}")
                all_ok = False

        shutil.rmtree(vd, ignore_errors=True)

        if all_ok:
            backup = patch_path.with_suffix('.patch.bak')
            if not backup.exists():
                shutil.copy2(patch_path, backup)
            patch_path.write_text(new_content, encoding=ENCODING)
            print(f"     ✅ Verified & written ({len(new_diffs)} file(s))")
            stats['ok'] += 1
        else:
            print(f"     ❌ Verification failed, NOT written")
            stats['fail'] += 1

        # Cleanup
        shutil.rmtree(bd, ignore_errors=True)
        shutil.rmtree(vd, ignore_errors=True)

    print(f"\n{'='*70}")
    print(f"  SUMMARY: OK={stats['ok']} Skip={stats['skip']} Fail={stats['fail']}")
    print(f"{'='*70}")


if __name__ == "__main__":
    main()
