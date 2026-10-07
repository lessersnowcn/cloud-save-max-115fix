#!/usr/bin/env python3
"""让 cloud-save-max 的搜索源「被静默跳过」时，把原因回传到界面提示里，不再无声无结果。

背景（实测，非推测）：
  搜索源 cloudsaver 在凭据不完整时被整条跳过：

      if not (server and username and password):
          return ([], None)          # ← 不报错、不记日志、返回的 message 里也不提

  实例：库里的 cloudsaver 只有 username，password 为空（密码被误填进了 token 字段）
  → cloudsaver 一条结果都不出，界面只显示「已限定网盘类型：115」，
    用户完全看不出少了一个源，以为"搜索是正常的"（其实结果全来自 pansou）。
  同症状的还有两种：密码错导致自动登录失败；服务地址/用户名缺失。

改法（只增不减，不动任何搜索与过滤逻辑）：
  1. cs_search() 的 4 处静默返回改成带原因的 3 元组 ([], None, 原因)；
     「未启用」仍不提示（那是用户的主动选择，不该报噪音）。
  2. 调用侧把原因收集进 source_notes，最后拼到 message 最前面。
     界面是原样显示 message 的，所以不需要改前端、不需要重新构建前端。
  3. 自动登录/写回 token 的既有逻辑一行未改（token 依旧是自动维护的）。

幂等：重复执行安全（检测到 source_notes 就跳过）。
用法：python3 patch_source_skip_notice.py [目标文件路径]
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path

DEFAULT_TARGET = "/app/backend/app/services/resource_search.py"

# (说明, 原文, 新文)
EDITS: list[tuple[str, str, str]] = [
    (
        "cs_search: 未启用分支改成 3 元组（仍不产生提示）",
        '''            if not rows["cloudsaver"].enabled:
                return ([], None)
            server = str(cfg_cs.get("server") or "").strip()
''',
        '''            if not rows["cloudsaver"].enabled:
                return ([], None, "")
            server = str(cfg_cs.get("server") or "").strip()
''',
    ),
    (
        "cs_search: 凭据不全 → 说明缺哪几项",
        '''            if not (server and username and password):
                return ([], None)
''',
        '''            if not (server and username and password):
                missing = "、".join(
                    name for name, value in (("服务地址", server), ("用户名", username), ("密码", password)) if not value
                )
                return ([], None, f"⚠ CloudSaver 未配置{missing}，已跳过（token 由密码自动登录维护，无需手填）")
''',
    ),
    (
        "cs_search: 登录/搜索失败 → 带上上游给的原因",
        '''            if not search.get("success"):
                return ([], None)
''',
        '''            if not search.get("success"):
                return ([], None, f"⚠ CloudSaver 不可用：{search.get('message') or '未知原因'}，已跳过")
''',
    ),
    (
        "cs_search: 正常返回补第三项 + 异常也回传",
        '''            return (results, new_token)
        except Exception:
            return ([], None)
''',
        '''            return (results, new_token, "")
        except Exception as exc:
            return ([], None, f"⚠ CloudSaver 异常：{str(exc)[:80]}，已跳过")
''',
    ),
    (
        "调用侧：准备收集各源提示",
        '''    search_results: list[dict[str, Any]] = []
    new_token: str | None = None
    with ThreadPoolExecutor(max_workers=3) as executor:
''',
        '''    search_results: list[dict[str, Any]] = []
    new_token: str | None = None
    source_notes: list[str] = []
    with ThreadPoolExecutor(max_workers=3) as executor:
''',
    ),
    (
        "调用侧：把提示从结果元组里取出来（兼容 2 元组）",
        '''            if isinstance(r, tuple):
                items, tok = r
''',
        '''            if isinstance(r, tuple):
                items, tok = r[0], r[1]
                if len(r) > 2 and r[2]:
                    source_notes.append(str(r[2]))
''',
    ),
    (
        "message 组装：提示排在最前面",
        '''    message = None
    msg_parts: list[str] = []
    if dt_filter:
''',
        '''    message = None
    msg_parts: list[str] = []
    # 被跳过的搜索源（如 CloudSaver 未配置密码）也一并回传，避免"静默少源"被误判成搜索正常
    for note in source_notes:
        if note and note not in msg_parts:
            msg_parts.append(note)
    if dt_filter:
''',
    ),
]

MARKER = "source_notes"


def main() -> int:
    target = Path(sys.argv[1] if len(sys.argv) > 1 else DEFAULT_TARGET)
    if not target.exists():
        print(f"✗ 目标文件不存在：{target}")
        return 2
    src = target.read_text(encoding="utf-8")

    if MARKER in src:
        print(f"· 已打过补丁（发现 {MARKER}），跳过：{target}")
        return 0

    for idx, (label, old, new) in enumerate(EDITS, 1):
        cnt = src.count(old)
        if cnt != 1:
            print(f"✗ 第 {idx} 处锚点匹配 {cnt} 次（应为 1）：{label}")
            print("  锚点首行：" + old.splitlines()[0])
            print("  上游可能已改这块代码 —— 补丁需要重新对位，构建中止。")
            return 3
        src = src.replace(old, new, 1)
        print(f"✓ {idx}/{len(EDITS)} {label}")

    # 打完后：cs_search 内不应再有裸露的 2 元组静默返回
    left = src.count("return ([], None)\n")
    if left:
        print(f"✗ 补丁后仍有 {left} 处 `return ([], None)`（应为 0）")
        return 4

    try:
        ast.parse(src)
    except SyntaxError as exc:
        print(f"✗ 补丁后语法错误：{exc}")
        return 5

    target.write_text(src, encoding="utf-8")
    print(f"✓ 已写入：{target}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
