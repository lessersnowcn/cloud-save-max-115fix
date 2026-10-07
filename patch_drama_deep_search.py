#!/usr/bin/env python3
"""让 cloud-save-max 的「自动换链」搜索不再因为深度搜索返回空而误判 no_candidates。

背景（实测，非推测）：
  自动换链的候选搜索写死 deep=1（= PanSou refresh=true，实时抓取）。
  但实时抓取对部分关键词会返回 0 条，而同一关键词的缓存搜索（refresh=false）有结果。
  实例：「无可替代」deep=1 → 0 条（3/3 复现）；deep=0 → 19 条（含 S01E01–E14）。
  对照组：「庆余年」deep=1 正常 → 说明不是 PanSou 整体故障，是"实时抓取取不到"这个半空场景。
  后果：自动换链搜不到候选 → 追剧任务只能停在种子那一集，不会往后跟集。

改法（只增不减，纯追加）：
  同一关键词搜两轮：deep=1（实时，最新优先）→ deep=0（缓存，兜底），按 shareurl 去重，
  深度结果排在前。原有的候选校验/排序/连贯性判断全部不变。
  —— 为什么不是"仅当 deep=1 为空才回退"：deep=1 有时会返回**少量无关结果**（实测本剧返回 1 条
     不相关的「秘密」），此时"仅空回退"不会触发，换链照样卡住。合并两轮可覆盖这种半空场景。

幂等：重复执行安全（检测到 fetched_deep0_added 就跳过）。
用法：python3 patch_drama_deep_search.py [目标文件路径]
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path

DEFAULT_TARGET = "/app/backend/app/services/drama_share_autoupdate.py"

OLD_BLOCK = '''    for keyword in names:
        items, changed, _msg = fetch_task_suggestions(db, keyword=keyword, deep=1, drive_type=drive_type, search_filter=search_filter, search_exclude=search_exclude, search_date_from=search_date_from, search_filter_mode=search_filter_mode, search_exclude_mode=search_exclude_mode)
        if changed:
            db_changed = True
        if isinstance(items, list):
            all_items.extend([x for x in items if isinstance(x, dict)])
'''

NEW_BLOCK = '''    deep1_total = 0
    deep0_added = 0
    for keyword in names:
        seen_shareurls: set[str] = set()
        # 深度搜索（PanSou refresh=true，实时）优先；再补一轮缓存搜索（refresh=false）兜底：
        # 实测实时抓取对部分关键词会返回空，而缓存里有结果，会导致自动换链误判 no_candidates。
        # 两轮按 shareurl 去重，深度结果排在前。
        for deep in (1, 0):
            items, changed, _msg = fetch_task_suggestions(db, keyword=keyword, deep=deep, drive_type=drive_type, search_filter=search_filter, search_exclude=search_exclude, search_date_from=search_date_from, search_filter_mode=search_filter_mode, search_exclude_mode=search_exclude_mode)
            if changed:
                db_changed = True
            if not isinstance(items, list):
                continue
            for item in items:
                if not isinstance(item, dict):
                    continue
                shareurl = str(item.get("shareurl") or "").strip()
                if shareurl and shareurl in seen_shareurls:
                    continue
                if shareurl:
                    seen_shareurls.add(shareurl)
                if deep == 1:
                    deep1_total += 1
                else:
                    deep0_added += 1
                all_items.append(item)
'''

OLD_STATS = '''        "fetched_total": len(all_items),
        "fetched_samples": [],
'''

NEW_STATS = '''        "fetched_total": len(all_items),
        "fetched_deep1": deep1_total,
        "fetched_deep0_added": deep0_added,
        "fetched_samples": [],
'''


def main() -> int:
    target = Path(sys.argv[1] if len(sys.argv) > 1 else DEFAULT_TARGET)
    if not target.exists():
        print(f"目标文件不存在: {target}", file=sys.stderr)
        return 2

    src = target.read_text(encoding="utf-8")

    if "fetched_deep0_added" in src:
        print(f"已打过补丁，跳过: {target}")
        return 0

    if OLD_BLOCK not in src:
        print("找不到待替换的搜索代码块（上游可能已改版），放弃，未做任何修改", file=sys.stderr)
        return 3
    src = src.replace(OLD_BLOCK, NEW_BLOCK, 1)

    if OLD_STATS not in src:
        print("找不到 stats 插入点，放弃（文件未写回）", file=sys.stderr)
        return 4
    src = src.replace(OLD_STATS, NEW_STATS, 1)

    try:
        ast.parse(src)
    except SyntaxError as exc:
        print(f"打补丁后语法检查失败，放弃（文件未写回）: {exc}", file=sys.stderr)
        return 5

    if src.count("deep=1") or "for deep in (1, 0):" not in src:
        print("自检失败：两轮搜索结构不正确，放弃（文件未写回）", file=sys.stderr)
        return 6

    target.write_text(src, encoding="utf-8")
    print(f"已打补丁: {target}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
