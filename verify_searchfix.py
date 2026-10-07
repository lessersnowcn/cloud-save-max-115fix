#!/usr/bin/env python3
"""校验 drama_share_autoupdate.py 的「两轮搜索合并」补丁是否真生效。

做法：从目标文件里 AST 抽出 _search_candidates 里那个 `for keyword in names:` 循环，
      用桩函数喂进真实场景数据，直接跑真代码（不是看字符串）。
      所以镜像构建期就能发现补丁没打上 / 打歪了。

用法：python3 verify_searchfix.py [目标文件路径]
退出码：0 = 全部场景通过；非 0 = 失败（构建或部署必须中止）
"""
from __future__ import annotations

import ast
import pathlib
import sys

DEFAULT_TARGET = "/app/backend/app/services/drama_share_autoupdate.py"


def build_harness(src: str):
    tree = ast.parse(src)
    fn = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "_search_candidates"]
    if not fn:
        raise SystemExit("✗ 找不到 _search_candidates 函数")
    loops = [n for n in fn[0].body if isinstance(n, ast.For) and getattr(n.target, "id", None) == "keyword"]
    if not loops:
        raise SystemExit("✗ _search_candidates 里找不到关键词循环")
    if "for deep in (1, 0):" not in src:
        raise SystemExit("✗ 没看到两轮搜索结构 for deep in (1, 0):")
    if "deep=1," in src.replace(" ", ""):
        # 原硬编码调用应已被替换（形如 deep=1, 后面紧跟 drive_type）
        raise SystemExit("✗ 仍存在硬编码 deep=1 的调用，补丁未生效")
    if "fetched_deep0_added" not in src:
        raise SystemExit("✗ 缺少 fetched_deep0_added 日志字段")

    mod = ast.Module(body=[
        ast.Assign(targets=[ast.Name(id="all_items", ctx=ast.Store())], value=ast.List(elts=[], ctx=ast.Load())),
        ast.Assign(targets=[ast.Name(id="db_changed", ctx=ast.Store())], value=ast.Constant(False)),
        ast.Assign(targets=[ast.Name(id="deep1_total", ctx=ast.Store())], value=ast.Constant(0)),
        ast.Assign(targets=[ast.Name(id="deep0_added", ctx=ast.Store())], value=ast.Constant(0)),
        loops[0],
    ], type_ignores=[])
    ast.fix_missing_locations(mod)
    return compile(mod, "<patched-loop>", "exec")


def run(harness, names, dataset):
    calls: list[tuple[str, int]] = []

    def fetch_task_suggestions(db, *, keyword, deep, drive_type, search_filter, search_exclude,
                               search_date_from, search_filter_mode, search_exclude_mode):
        calls.append((keyword, deep))
        return dataset.get((keyword, deep), []), False, "stub"

    g = {
        "names": names, "db": None, "fetch_task_suggestions": fetch_task_suggestions,
        "drive_type": "115", "search_filter": "", "search_exclude": "",
        "search_date_from": "", "search_filter_mode": "", "search_exclude_mode": "",
    }
    exec(harness, g)
    return g, calls


def urls(items):
    return [i["shareurl"] for i in items]


def main() -> int:
    target = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else DEFAULT_TARGET)
    if not target.exists():
        print(f"✗ 目标文件不存在: {target}", file=sys.stderr)
        return 2
    src = target.read_text(encoding="utf-8")
    harness = build_harness(src)
    failures: list[str] = []

    # A) 真实本剧场景：deep=1 返回 0 条（实测 3/3），缓存 19 条（含 S01E01–E14）
    ds = {("无可替代", 1): [], ("无可替代", 0): [{"shareurl": f"https://115cdn.com/s/u{i:02d}"} for i in range(19)]}
    g, calls = run(harness, ["无可替代"], ds)
    ok = len(g["all_items"]) == 19 and g["deep1_total"] == 0 and g["deep0_added"] == 19 and calls == [("无可替代", 1), ("无可替代", 0)]
    print(f"  A) deep=1 空 → 缓存兜底：候选 {len(g['all_items'])} 条, deep1={g['deep1_total']}, deep0_added={g['deep0_added']}  {'OK' if ok else 'FAIL'}")
    if not ok:
        failures.append("A")

    # B) 半空场景：deep=1 回 2 条，缓存与其重叠 1 条 → 合并去重后 3 条，深度结果在前
    ds = {("剧X", 1): [{"shareurl": "u1"}, {"shareurl": "u2"}], ("剧X", 0): [{"shareurl": "u2"}, {"shareurl": "u3"}]}
    g, _ = run(harness, ["剧X"], ds)
    ok = urls(g["all_items"]) == ["u1", "u2", "u3"] and g["deep1_total"] == 2 and g["deep0_added"] == 1
    print(f"  B) 半空 + 重叠去重：{urls(g['all_items'])}  {'OK' if ok else 'FAIL'}")
    if not ok:
        failures.append("B")

    # C) deep=1 只回 1 条无关结果 → 仍要补缓存（"仅空回退"方案会漏掉这个场景）
    ds = {("无可替代", 1): [{"shareurl": "junk"}], ("无可替代", 0): [{"shareurl": f"e{i}"} for i in range(14)]}
    g, _ = run(harness, ["无可替代"], ds)
    ok = len(g["all_items"]) == 15
    print(f"  C) deep=1 半空(1 条无关) + 缓存 14 条：候选 {len(g['all_items'])} 条  {'OK' if ok else 'FAIL'}")
    if not ok:
        failures.append("C")

    # D) 脏数据：items=None / 非 dict / 空 shareurl 都不能炸
    ds = {("剧Y", 1): None, ("剧Y", 0): [None, {"shareurl": ""}, {"shareurl": "ok"}]}
    g, _ = run(harness, ["剧Y"], ds)
    ok = urls(g["all_items"]) == ["", "ok"]
    print(f"  D) 脏数据容错：{urls(g['all_items'])}  {'OK' if ok else 'FAIL'}")
    if not ok:
        failures.append("D")

    # E) 多关键词（剧名+原名）同一链接 → 循环产出后由原有全局去重收敛（此处只验不重复追加）
    ds = {(k, d): [{"shareurl": "same"}] for k in ("无可替代", "Nowhere") for d in (1, 0)}
    g, _ = run(harness, ["无可替代", "Nowhere"], ds)
    ok = len(g["all_items"]) == 2 and "seen_urls: set[str] = set()" in src
    print(f"  E) 跨关键词同一链接：{len(g['all_items'])} 条 + 原有全局去重仍在  {'OK' if ok else 'FAIL'}")
    if not ok:
        failures.append("E")

    if failures:
        print(f"✗ 校验失败：{', '.join(failures)}", file=sys.stderr)
        return 1
    print("✓ 补丁校验通过（5/5 场景）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
