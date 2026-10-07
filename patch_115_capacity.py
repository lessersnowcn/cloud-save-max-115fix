#!/usr/bin/env python3
"""修正 cloud-save-max 的 115 容量解析崩溃（上游 v26.9.6 仍存在）。

现象：账号卡片状态 error + "invalid literal for int() with base 10: '1205409640142.8'"
原因：115 的 base_info 接口返回的 size_used_raw / size_total_raw 是**带小数的字符串**，
      而 cloud115_adapter.get_account_config() 直接 int() 转换 → ValueError
      → 探测流程把它当成账号故障，写 runtime_status='error'。

本脚本把这两处 int(...) 换成 _safe_int(...)，并注入辅助函数。重复执行安全（幂等）。
用法：python3 patch_115_capacity.py [目标文件路径]
"""
from __future__ import annotations

import sys
from pathlib import Path

DEFAULT_TARGET = "/app/backend/app/extensions/adapters/cloud115_adapter.py"

HELPER = '''

def _safe_int(value: Any) -> Optional[int]:
    """容量值可能是 '数字字符串' / 小数 / None，安全转 int；转不了就返回 None。"""
    if value is None or value == "":
        return None
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None
'''

REPLACEMENTS = (
    ('int(member_data.get("size_used_raw"))', '_safe_int(member_data.get("size_used_raw"))'),
    ('int(member_data.get("size_total_raw"))', '_safe_int(member_data.get("size_total_raw"))'),
)


def main() -> int:
    target = Path(sys.argv[1] if len(sys.argv) > 1 else DEFAULT_TARGET)
    if not target.exists():
        print(f"目标文件不存在: {target}", file=sys.stderr)
        return 2

    src = target.read_text(encoding="utf-8")

    if "def _safe_int" in src:
        print(f"已打过补丁，跳过: {target}")
        return 0

    anchor = "class Cloud115Adapter"
    if anchor not in src:
        print("找不到锚点 class Cloud115Adapter，可能上游已改版，请人工核对", file=sys.stderr)
        return 3
    src = src.replace(anchor, HELPER.strip("\n") + "\n\n\n" + anchor, 1)

    for old, new in REPLACEMENTS:
        if old not in src:
            print(f"找不到待替换片段（上游可能已改）: {old}", file=sys.stderr)
            return 4
        src = src.replace(old, new)

    target.write_text(src, encoding="utf-8")
    print(f"已打补丁: {target}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
