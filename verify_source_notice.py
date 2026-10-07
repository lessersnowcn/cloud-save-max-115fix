#!/usr/bin/env python3
"""校验 resource_search.py 的「搜索源跳过原因回传」补丁是否真生效。

做法：从目标文件里 AST 抽出整个 fetch_task_suggestions 函数，把 DB、两个搜索源客户端、
      缓存、适配器注册表全部**桩掉**（不联网、不碰数据库），然后跑真代码。
      所以镜像构建期就能发现补丁没打上 / 打歪了 / 顺手把搜索逻辑改坏了。

覆盖场景（7）：
  1 正常：CloudSaver+PanSou 各 1 条 → 2 条，提示只有「已限定网盘类型」，且新 token 被写回
  2 密码为空（本次踩的坑）→ 提示「未配置密码」，PanSou 结果不丢
  3 凭据全空 → 提示点名「服务地址、用户名、密码」
  4 密码错（登录/搜索失败）→ 提示带上上游原因
  5 客户端抛异常 → 提示「异常」且不崩
  6 源未启用 → 不产生任何提示（用户主动禁用的，不该有噪音）
  7 上述任一场景都不得让 PanSou 正常结果受影响

用法：python3 verify_source_notice.py [目标文件路径]
退出码：0 = 全部场景通过；非 0 = 失败（构建或部署必须中止）
"""
from __future__ import annotations

import ast
import json
import pathlib
import sys
import threading
from typing import Any

DEFAULT_TARGET = "/app/backend/app/services/resource_search.py"

CS_OK_ITEM = {"shareurl": "https://115cdn.com/s/cs-item-1", "taskname": "cs-item-1"}
PS_OK_ITEM = {"shareurl": "https://115cdn.com/s/ps-item-1", "taskname": "ps-item-1"}


def load_function(src: str):
    tree = ast.parse(src)
    fns = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "fetch_task_suggestions"]
    if not fns:
        raise SystemExit("✗ 找不到 fetch_task_suggestions 函数")
    if "source_notes" not in src:
        raise SystemExit("✗ 源码里没有 source_notes —— 补丁没打上")
    mod = ast.Module(body=[fns[0]], type_ignores=[])
    ast.fix_missing_locations(mod)
    return compile(mod, "<patched-resource-search>", "exec"), fns[0].args


class _Col:
    def is_(self, _v):
        return self

    def __eq__(self, _o):
        return self

    def __ne__(self, _o):
        return self


class _DriveAccount:
    drive_type = _Col()
    enabled = _Col()
    runtime_status = _Col()


class _Select:
    def where(self, *_a, **_k):
        return self


class _Rows:
    def scalars(self):
        return self

    def all(self):
        return ["115"]


class _DB:
    def __init__(self):
        self.flushes = 0

    def execute(self, *_a, **_k):
        return _Rows()

    def flush(self):
        self.flushes += 1


def make_globals(cs_cfg: dict, ps_cfg: dict, *, cs_enabled: bool = True, ps_enabled: bool = True,
                 behaviour: str = "ok") -> dict:
    class Row:
        def __init__(self, cfg, enabled):
            self.config_json = json.dumps(cfg, ensure_ascii=False)
            self.enabled = enabled

    rows = {
        "cloudsaver": Row(cs_cfg, cs_enabled),
        "pansou": Row(ps_cfg, ps_enabled),
    }

    class FakeCloudSaver:
        def __init__(self, server):
            self.server = server

        def set_auth(self, username="", password="", token=""):
            self.auth = (username, password, token)

        def auto_login_search(self, _kw):
            if behaviour == "fail":
                return {"success": False, "message": "无效的 token"}
            if behaviour == "raise":
                raise RuntimeError("boom 连接被拒")
            return {"success": True, "data": [CS_OK_ITEM], "new_token": "fresh-token"}

        def clean_search_results(self, data, keyword=None):
            return [
                {"shareurl": x["shareurl"], "taskname": x["taskname"], "datetime": "2026-10-01 00:00:00"}
                for x in (data or [])
            ]

    class FakePanSou:
        def __init__(self, _server):
            pass

        def search(self, _kw, refresh=False, drive_type=None):
            return [{"shareurl": PS_OK_ITEM["shareurl"], "taskname": PS_OK_ITEM["taskname"],
                     "datetime": "2026-10-02 00:00:00"}]

    return {
        "Any": Any,
        "Session": object,
        "ThreadPoolExecutor": __import__("concurrent.futures", fromlist=["ThreadPoolExecutor"]).ThreadPoolExecutor,
        "as_completed": __import__("concurrent.futures", fromlist=["as_completed"]).as_completed,
        "ensure_default_sources": lambda _db: rows,
        "_loads": lambda s: json.loads(s or "{}"),
        "_dumps": lambda o: json.dumps(o, ensure_ascii=False),
        "_log_debug": lambda *_a, **_k: None,
        "_update_search_cache_ttl": lambda: None,
        "_search_cache": {},
        "_search_cache_lock": threading.Lock(),
        "CloudSaverClient": FakeCloudSaver,
        "PanSouClient": FakePanSou,
        "list_invalid_shareurls": lambda _db, shareurls=None: [],
        "select": lambda *_a, **_k: _Select(),
        "DriveAccount": _DriveAccount,
        "_normalize_drive_type": lambda x: (str(x).strip() or None) if x not in (None, "") else None,
        "AdapterRegistry": type("AR", (), {"detect_drive_type": staticmethod(lambda _url: "115")}),
    }


def run_case(code, *, cs_cfg, ps_cfg, cs_enabled=True, behaviour="ok", keyword="庆余年"):
    g = make_globals(cs_cfg, ps_cfg, cs_enabled=cs_enabled, behaviour=behaviour)
    exec(code, g)
    db = _DB()
    items, changed, msg = g["fetch_task_suggestions"](db, keyword, 0, drive_type="115")
    return items, changed, msg, g


def main() -> int:
    target = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else DEFAULT_TARGET)
    if not target.exists():
        print(f"✗ 目标文件不存在：{target}")
        return 2
    src = target.read_text(encoding="utf-8")
    code, _args = load_function(src)

    base_cs = {"server": "http://cloudsaver.local:8008", "username": "demo", "password": "pw", "token": ""}
    base_ps = {"server": "http://pansou.local:4040"}

    failures: list[str] = []

    def check(name: str, cond: bool, detail: str):
        print(f"  {'✓' if cond else '✗'} {name} — {detail}")
        if not cond:
            failures.append(name)

    print("场景 1：两源都正常")
    items, changed, msg, _g = run_case(code, cs_cfg=base_cs, ps_cfg=base_ps)
    check("1a 结果数=2（cs+ps 都在）", len(items) == 2, f"实际 {len(items)} 条：{[i.get('taskname') for i in items]}")
    check("1b 提示里没有 ⚠", "⚠" not in str(msg or ""), f"msg={msg!r}")
    check("1c 提示仍是原样「已限定网盘类型：115」", str(msg).strip() == "已限定网盘类型：115", f"msg={msg!r}")
    check("1d 自动登录拿到的新 token 已写回", changed is True, f"changed={changed}")

    print("场景 2：CloudSaver 未配置密码（本次踩的坑）")
    items, changed, msg, _g = run_case(code, cs_cfg={k: v for k, v in base_cs.items() if k != "password"}, ps_cfg=base_ps)
    check("2a 提示点名未配置密码", "CloudSaver 未配置密码" in str(msg or ""), f"msg={msg!r}")
    check("2b PanSou 结果没被吞", any(i.get("taskname") == PS_OK_ITEM["taskname"] for i in items), f"{len(items)} 条")
    check("2c 提示排在限定网盘类型之前", str(msg or "").startswith("⚠ CloudSaver"),
          f"msg={msg!r}")

    print("场景 3：凭据全空")
    items, _c, msg, _g = run_case(code, cs_cfg={"enabled": True}, ps_cfg=base_ps)
    check("3a 三点名齐全", "未配置服务地址、用户名、密码" in str(msg or ""), f"msg={msg!r}")

    print("场景 4：密码错 → 登录/搜索失败")
    items, _c, msg, _g = run_case(code, cs_cfg=base_cs, ps_cfg=base_ps, behaviour="fail")
    check("4a 带上上游原因", "CloudSaver 不可用：无效的 token" in str(msg or ""), f"msg={msg!r}")
    check("4b PanSou 结果没被吞", len(items) == 1, f"{len(items)} 条")

    print("场景 5：客户端抛异常")
    items, _c, msg, _g = run_case(code, cs_cfg=base_cs, ps_cfg=base_ps, behaviour="raise")
    check("5a 提示异常且不崩", "CloudSaver 异常：boom 连接被拒" in str(msg or ""), f"msg={msg!r}")
    check("5b 仍有 PanSou 结果", len(items) == 1, f"{len(items)} 条")

    print("场景 6：源被用户主动禁用")
    items, _c, msg, _g = run_case(code, cs_cfg=base_cs, ps_cfg=base_ps, cs_enabled=False)
    check("6a 不产生任何 CloudSaver 噪音", "CloudSaver" not in str(msg or ""), f"msg={msg!r}")
    check("6b 只剩 PanSou 结果", len(items) == 1, f"{len(items)} 条")

    print("场景 7：桩函数没被绕过（真跑了两个源）")
    _i, _c, _m, g = run_case(code, cs_cfg=base_cs, ps_cfg=base_ps)
    check("7a 缓存被写入", len(g["_search_cache"]) == 1, f"cache={list(g['_search_cache'].keys())}")

    print()
    if failures:
        print("✗ 校验未通过：" + "、".join(failures))
        return 1
    print("✓ 全部 7 个场景通过：搜索源跳过原因会随 message 回传，且搜索/自动登录逻辑未被破坏")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
