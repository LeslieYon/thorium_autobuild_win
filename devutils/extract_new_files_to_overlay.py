#!/usr/bin/env python3
"""
从 patches 中提取"创建新文件"的 diff section，转存到 overlay/ 目录。

分析 patches/series 中列出的所有 patch 文件，找出其中创建全新文件
（source 为 /dev/null）的 diff section。
对于每个这样的新文件：
  - 如果 overlay/ 中已存在同名文件，直接从 patch 中移除该 section。
  - 如果 overlay/ 中不存在，先从 patch 中提取文件内容写入 overlay，
    再从 patch 中移除该 section。

如果某个 patch 文件的所有 section 都被移除，整个 patch 文件会被删除，
同时从 series 中注释掉对应条目。

补丁子目录映射：
  某些 patch 是在 Chromium 源码树的子目录中应用的（如 third_party/ffmpeg/），
  其 diff 路径是相对于该子目录的。对于这种情况，需要将 overlay 路径映射到
  正确的源码树位置。通过 PATCH_SUBDIRS 字典配置。

用法:
  python devutils/extract_new_files_to_overlay.py
  python devutils/extract_new_files_to_overlay.py --dry-run   # 仅预览，不修改
"""

import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
PATCHES_DIR = BASE_DIR / 'patches' / 'thorium'
OVERLAY_DIR = BASE_DIR / 'overlay'
SERIES_FILE = BASE_DIR / 'patches' / 'series'
SERIES_PREFIX = 'thorium/'  # 所有 series 条目都以 thorium/ 开头

# 已知的在子目录中应用的 patch（相对于 Chromium 源码树根）
# key = series 条目中的 patch 路径（如 'thorium/original/add-hevc-ffmpeg-decoder-parser.patch'）
# value = patch 中路径相对于 Chromium 源码根的额外前缀
PATCH_SUBDIRS = {
    'thorium/original/add-hevc-ffmpeg-decoder-parser.patch': 'third_party/ffmpeg/',
    'thorium/original/change-libavcodec-header.patch': 'third_party/ffmpeg/',
    'thorium/original/ffmpeg_hevc_ac3.patch': 'third_party/ffmpeg/',
}


def read_series(series_path):
    """读取 series 文件，返回 (原始行列表, 解析后的条目列表)"""
    lines = []
    entries = []
    with open(series_path, 'r', encoding='utf-8', errors='replace') as f:
        for line in f:
            stripped = line.strip()
            lines.append(line)
            if not stripped or stripped.startswith('#'):
                continue
            # 去掉行内注释（从 # 开始到行尾），取第一部分
            entry = stripped.split('#')[0].strip()
            if entry:
                entries.append(entry)
    return lines, entries


def write_series(series_path, lines):
    """写回 series 文件"""
    with open(series_path, 'w', encoding='utf-8', newline='') as f:
        f.writelines(lines)


def patch_path_from_entry(entry):
    """将 series 中的条目（如 'thorium/original/foo.patch'）转为绝对路径"""
    rel = entry[len(SERIES_PREFIX):] if entry.startswith(SERIES_PREFIX) else entry
    return PATCHES_DIR / rel


def read_file_safe(path):
    """安全读取文件，自动检测编码"""
    try:
        with open(path, 'r', encoding='utf-8') as f:
            return f.read()
    except UnicodeDecodeError:
        pass
    with open(path, 'r', encoding='latin-1') as f:
        return f.read()


def parse_patch_sections(text):
    """
    将 patch 文本分割成 "diff section"。

    返回 list[dict]:
      - type: 'header' = git format-patch 头部（From/Date/Subject/--- 分隔线等）
      - type: 'diff'   = 一个 diff 块

    每个 diff section 的字段:
      - text, lines, start_line, end_line
      - source_file, dest_file, is_new_file
    """
    text = text.replace('\r\n', '\n').replace('\r', '\n')
    lines = text.split('\n')
    while lines and lines[-1] == '':
        lines.pop()

    sections = []
    i = 0

    while i < len(lines):
        line = lines[i]

        if line.startswith('diff --git '):
            sec_start = i
            i += 1
            while i < len(lines) and not lines[i].startswith('diff --git '):
                i += 1
            sec_end = i

            sec_lines = lines[sec_start:sec_end]

            source_file = ''
            dest_file = ''
            is_new_file = False

            for sl in sec_lines:
                if sl.startswith('--- '):
                    src = sl[4:].strip()
                    if src == '/dev/null':
                        is_new_file = True
                        source_file = ''
                    else:
                        source_file = src
                elif sl.startswith('+++ '):
                    dest_file = sl[4:].strip()

            sections.append({
                'type': 'diff',
                'lines': sec_lines,
                'start_line': sec_start,
                'end_line': sec_end,
                'source_file': source_file,
                'dest_file': dest_file,
                'is_new_file': is_new_file,
            })
        else:
            sec_start = i
            i += 1
            while i < len(lines) and not lines[i].startswith('diff --git '):
                i += 1
            sec_end = i

            sec_lines = lines[sec_start:sec_end]
            sections.append({
                'type': 'header',
                'lines': sec_lines,
                'start_line': sec_start,
                'end_line': sec_end,
            })

    return sections


def extract_file_content(section_lines):
    """
    从 new file 的 diff section 中提取文件内容。
    只提取 hunk 中以 '+' 前缀的行，去掉前缀。
    """
    content_lines = []
    in_hunk = False
    for line in section_lines:
        if line.startswith('@@'):
            in_hunk = True
            continue
        if in_hunk:
            if line.startswith('+'):
                content_lines.append(line[1:])
            elif line.startswith(' '):
                content_lines.append(line[1:])
            elif line.startswith('\\'):
                continue
    result = '\n'.join(content_lines)
    if result:
        result += '\n'
    return result


def get_overlay_path(dest_file, entry):
    """
    将 diff 目标路径转为 overlay 路径。
    处理子目录映射（如 ffmpeg patches）。
    """
    path = dest_file
    for prefix in ('b/', 'a/'):
        if path.startswith(prefix):
            path = path[len(prefix):]
            break

    subdir = PATCH_SUBDIRS.get(entry, '')
    if subdir:
        path = subdir + path

    return OVERLAY_DIR / path


def process_patch(patch_path, entry, dry_run=False):
    """
    处理单个 patch 文件。
    返回 (modified, new_sections, actions)：
      - modified: 文件是否被修改
      - new_sections: 移除 new-file sections 后的 sections
      - actions: 操作描述列表
    """
    if not patch_path.exists():
        return False, [], [f"❌ 文件不存在: {patch_path}"]

    original_text = read_file_safe(patch_path)
    sections = parse_patch_sections(original_text)
    actions = []
    new_sections = []

    for sec in sections:
        if sec.get('is_new_file'):
            dest = sec['dest_file']
            overlay_path = get_overlay_path(dest, entry)
            rel_dest = dest
            for prefix in ('b/', 'a/'):
                if rel_dest.startswith(prefix):
                    rel_dest = rel_dest[len(prefix):]
                    break
            subdir = PATCH_SUBDIRS.get(entry, '')
            if subdir:
                rel_dest = subdir + rel_dest

            if overlay_path.exists():
                actions.append(f"  ✅ overlay 中已存在: {rel_dest}")
                continue
            else:
                content = extract_file_content(sec['lines'])
                if dry_run:
                    actions.append(
                        f"  📋 [DRY RUN] 需要创建: {rel_dest}"
                        f" ({len(content)} 字节)"
                    )
                    new_sections.append(sec)
                else:
                    overlay_path.parent.mkdir(parents=True, exist_ok=True)
                    with open(overlay_path, 'w', encoding='utf-8') as f:
                        f.write(content)
                    actions.append(
                        f"  ✅ 已创建 overlay 文件: {rel_dest}"
                        f" ({len(content)} 字节)"
                    )
                    continue
        else:
            new_sections.append(sec)

    modified = len(new_sections) < len(sections)
    return modified, new_sections, actions


def rebuild_patch(sections, original_text):
    """
    从保留的 sections 重建 patch 文本。
    保留原始换行符风格（\\r\\n 或 \\n）。
    """
    has_crlf = '\r\n' in original_text

    parts = []
    for sec in sections:
        sec_text = '\n'.join(sec['lines'])
        parts.append(sec_text)

    result = '\n'.join(parts)
    if result:
        result += '\n'

    if has_crlf:
        result = result.replace('\n', '\r\n')

    return result


def patch_is_empty(sections):
    """检查 patch 是否只剩下 header（没有实际 diff）"""
    for sec in sections:
        if sec['type'] == 'diff':
            return False
    return True


def main():
    dry_run = '--dry-run' in sys.argv

    if dry_run:
        print("🔍 DRY RUN 模式 — 不会修改任何文件\n")

    print(f"📖 读取 series 文件: {SERIES_FILE}")
    series_lines, entries = read_series(SERIES_FILE)
    print(f"   共 {len(entries)} 个 patch 条目\n")

    total_removed_sections = 0
    total_created_files = 0
    total_patches_deleted = 0
    total_series_commented = 0

    entries_to_comment = set()

    for entry_idx, entry in enumerate(entries):
        patch_path = patch_path_from_entry(entry)
        print(f"📄 处理: {entry}")

        if not patch_path.exists():
            print(f"  ⚠️  文件不存在，跳过\n")
            continue

        modified, new_sections, actions = process_patch(
            patch_path, entry, dry_run=dry_run
        )

        for action in actions:
            print(action)
            if action.startswith("  ✅ 已创建 overlay"):
                total_created_files += 1
            if action.startswith("  ✅ overlay 中已存在"):
                total_removed_sections += 1

        if modified:
            if patch_is_empty(new_sections):
                if not dry_run:
                    patch_path.unlink()
                print(
                    f"  🗑️  patch 已无实际 diff，"
                    f"{'[DRY RUN] 将删除' if dry_run else '已删除'} 文件"
                )
                total_patches_deleted += 1
                entries_to_comment.add(entry_idx)
                total_series_commented += 1
            else:
                new_text = rebuild_patch(new_sections, read_file_safe(patch_path))
                if not dry_run:
                    with open(patch_path, 'w', encoding='utf-8', newline='') as f:
                        f.write(new_text)
                removed = sum(
                    1 for a in actions
                    if a.startswith("  ✅ overlay 中已存在")
                    or a.startswith("  ✅ 已创建 overlay")
                )
                print(f"  ✏️  已更新 patch，移除了 {removed} 个 new-file section(s)")
        else:
            has_new_files = any(
                sec.get('is_new_file') for sec in
                parse_patch_sections(read_file_safe(patch_path))
            )
            if has_new_files:
                print(f"  ℹ️  有 new-file section 但未修改（可能所有目标都存在或 dry-run）")
            else:
                print(f"  ℹ️  没有创建新文件的 section")

        print()

    if entries_to_comment and not dry_run:
        new_series_lines = list(series_lines)
        for entry_idx in sorted(entries_to_comment, reverse=True):
            entry = entries[entry_idx]
            for i, line in enumerate(new_series_lines):
                if line.strip() == entry:
                    new_series_lines[i] = (
                        f'# {line.rstrip()}'
                        f'  # removed by extract_new_files_to_overlay.py\n'
                    )
                    break
        write_series(SERIES_FILE, new_series_lines)

    tag = "[DRY RUN] " if dry_run else ""
    print(f"\n📊 {tag}执行结果:")
    print(f"   从 patch 中移除的 new-file sections: {total_removed_sections}")
    print(f"   已创建到 overlay 的文件数: {total_created_files}")
    print(f"   被完全删除的 patch 文件数: {total_patches_deleted}")
    print(f"   从 series 中注释掉的条目数: {total_series_commented}")

    if dry_run:
        print(f"\n💡 去掉 --dry-run 运行以实际执行修改")


if __name__ == '__main__':
    main()
