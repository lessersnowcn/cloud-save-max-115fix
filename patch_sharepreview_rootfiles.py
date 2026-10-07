#!/usr/bin/env python3
"""补丁4：分享根目录下直接散放的文件被忽略 → 上层误判「无法获取」→ 剧集永远不转存。

根因（cloud-save-max 26.9.6 官方代码）
------------------------------------
`app/services/share_preview_batch.py` 的 `fetch_share_file_list_grouped()` 有两个分支：

  * 有 fid（extracted_pdir_fid 为真）→ `_collect_from_dir(fid)`：会收集该目录下的文件 ✅
  * 无 fid（`extract_url()` 返回 pdir_fid=0，绝大多数分享链接都是这样）→ 只 `for item in raw_items: if is_dir: ...`
    → **根目录下直接散放的视频文件既不进子目录递归，也不进 groups**，函数返回空列表 ❌

后果：调用方拿到空 groups，于是
  * `drama_executor` 的「连贯集数过滤」判定 allowed_eps 为空 → 打印「连贯集数过滤：无连贯集数，取消转存」→ 计划被清空 → 日志里只剩「无可转存文件」；
  * `drama_share_autoupdate` 的换链候选被记为「跳过无法获取」；
  * `task_executor` 的影巢「自动定位目录」定位不到。
凡是「分享根下只有视频文件、没有子文件夹」的分享（很多单集更新分享就是这样）都会中招。

修复
----
在那个分支里把根目录下的非目录项也归为一组，语义与有 fid 分支的 `_collect_from_dir()` 对齐；
并且把这一组放在**最后**，保证子目录组仍然是 groups[0]，
不改变调用方「取第一个有文件的目录的 fid」的既有行为（`if fid:` / `if not f or f == "0"` 守卫都会跳过空 fid）。

用法：python3 patch_sharepreview_rootfiles.py [/app/backend/app/services/share_preview_batch.py]
幂等：重复执行安全（第二次会直接提示已打过）。
"""

from __future__ import annotations

import ast
import hashlib
import pathlib
import sys

DEFAULT_TARGET = "/app/backend/app/services/share_preview_batch.py"

MARK = "_root_level_files_fix"

OLD = """    # 无 fid 的情况：按根目录下的子目录分组
    try:
        detail = adapter.get_detail(pwd_id, stoken, "")
    except Exception:
        return []

    raw_items = (((detail or {}).get("data") or {}).get("list")) or []
    groups: list[tuple[list[dict[str, Any]], str, Any]] = []

    for item in raw_items:
        if not isinstance(item, dict):
            continue
        is_dir = bool(_bool_is_dir(item))
        if is_dir:
            sub_fid = str(_pick_fid(item) or "").strip()
            if not sub_fid:
                continue
            sub_name = str(_pick_name(item) or "").strip()
            sub_groups = _collect_from_dir(sub_fid, _pick_updated_at(item), dir_name=sub_name)
            for g in sub_groups:
                if g[0]:
                    groups.append(g)

    return _sort_by_priority(groups)
"""

NEW = """    # 无 fid 的情况：按根目录下的子目录分组
    try:
        detail = adapter.get_detail(pwd_id, stoken, "")
    except Exception:
        return []

    raw_items = (((detail or {}).get("data") or {}).get("list")) or []
    groups: list[tuple[list[dict[str, Any]], str, Any]] = []

    # _root_level_files_fix: 根目录下直接散放的文件同样要归组，
    # 否则「分享根下只有视频文件、没有子目录」的链接会返回空列表，
    # 上层（连贯集数过滤 / 换链候选 / 自动定位目录）会误判为「无法获取」，导致取消转存。
    root_files: list[dict[str, Any]] = []

    for item in raw_items:
        if not isinstance(item, dict):
            continue
        is_dir = bool(_bool_is_dir(item))
        if is_dir:
            sub_fid = str(_pick_fid(item) or "").strip()
            if not sub_fid:
                continue
            sub_name = str(_pick_name(item) or "").strip()
            sub_groups = _collect_from_dir(sub_fid, _pick_updated_at(item), dir_name=sub_name)
            for g in sub_groups:
                if g[0]:
                    groups.append(g)
        else:
            root_name = str(_pick_name(item) or "").strip()
            if not root_name:
                continue
            root_files.append({
                "file_name": root_name,
                "is_dir": False,
                "size": _pick_size(item),
                "updated_at": _pick_updated_at(item),
            })

    # 根目录这一组放在最后：子目录组仍是 groups[0]，
    # 调用方「取第一个有文件的目录的 fid」的既有行为不受影响（空 fid 会被其守卫跳过）。
    if root_files:
        groups.append((root_files, "", None, ""))

    return _sort_by_priority(groups)
"""


def main() -> int:
    target = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else DEFAULT_TARGET)
    if not target.is_file():
        print(f"ERROR: 目标文件不存在: {target}")
        return 2

    original = target.read_text(encoding="utf-8")
    sha_before = hashlib.sha256(original.encode("utf-8")).hexdigest()

    if MARK in original:
        print(f"SKIP: 目标已包含补丁4标记（{MARK}），无需重复打补丁")
        print(f"      {target} sha256={sha_before}")
        return 0

    if original.count(OLD) != 1:
        print(f"ERROR: 未找到唯一匹配的锚点（匹配数={original.count(OLD)}），官方代码可能已变化，人工介入")
        return 3

    patched = original.replace(OLD, NEW, 1)

    # 结构自检
    if patched.count("root_files.append") != 1:
        print("ERROR: 结构自检失败（root_files.append 数量异常）")
        return 4
    if patched.count('groups.append((root_files, "", None, ""))') != 1:
        print("ERROR: 结构自检失败（根目录分组语句数量异常）")
        return 5

    try:
        ast.parse(patched)
    except SyntaxError as exc:
        print(f"ERROR: 补丁后语法错误: {exc}")
        return 6

    target.write_text(patched, encoding="utf-8")
    sha_after = hashlib.sha256(patched.encode("utf-8")).hexdigest()
    print("OK: 补丁4 已应用")
    print(f"    {target}")
    print(f"    sha256 {sha_before} -> {sha_after}")
    print(f"    新增行数: {patched.count(chr(10)) - original.count(chr(10))}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
