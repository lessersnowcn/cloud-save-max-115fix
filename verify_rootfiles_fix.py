#!/usr/bin/env python3
"""补丁4 验证脚本：用桩适配器驱动真实代码，检查「分享根目录散放文件」是否被正确归组。

- 默认验证镜像里已打补丁的文件：`import app.services.share_preview_batch`
- 也可用环境变量指定文件本体（本地预演用）：
      SPB_MODULE_FILE=/tmp/share_preview_batch.patched.py python3 verify_rootfiles_fix.py

任一场景失败 → 退出码 1（构建期会让镜像构建直接失败）。
"""

from __future__ import annotations

import importlib.util
import os
import sys

BACKEND = "/app/backend"
if BACKEND not in sys.path:
    sys.path.insert(0, BACKEND)
os.chdir(BACKEND)

TARGET_FILE = os.environ.get("SPB_MODULE_FILE", "").strip()
if TARGET_FILE:
    spec = importlib.util.spec_from_file_location("spb_probe", TARGET_FILE)
    spb = importlib.util.module_from_spec(spec)
    sys.modules["spb_probe"] = spb
    spec.loader.exec_module(spb)  # type: ignore[union-attr]
    print(f"[verify] 验证文件本体: {TARGET_FILE}")
else:
    import app.services.share_preview_batch as spb  # type: ignore
    print("[verify] 验证镜像内 app.services.share_preview_batch")

import app.extensions.runtime.account_manager as am  # noqa: E402
from app.extensions.runtime.magic_rename import MagicRename  # noqa: E402
from app.services.drama_share_consecutive import check_consecutive_episodes  # noqa: E402

REAL_E01 = "无可替代.S01E01.第1集.2160p.WEB-DL HQ.H265.DTS 5.1.mkv"
REAL_E02 = "无可替代.S01E02.第2集.2160p.WEB-DL HQ.H265.DTS 5.1.mkv"
PAT = r".*\.(mp4|mkv|mov|m4v|avi|mpeg|ts|zip)$"
REP = "{TASKNAME}-S01E{E0}.{EXT}"


def V(name: str, fid: str = "f", size: int = 5_000_000_000) -> dict:
    return {
        "fid": fid, "name": name, "file_name": name,
        "is_dir": False, "dir": False, "size": size, "updated_at": 1790593583000,
    }


def D(name: str, fid: str) -> dict:
    return {
        "fid": fid, "name": name, "file_name": name,
        "is_dir": True, "dir": True, "size": 0, "updated_at": 1790593583000,
    }


class FakeAdapter:
    is_active = True

    def __init__(self, root, subs=None):
        self._root = list(root or [])
        self._subs = dict(subs or {})

    # pdir_fid 返回 0（整数）→ 触发「无 fid 的情况」分支，也就是补丁4 修的那条
    def extract_url(self, url):
        return ("swstbfd3zrk", "t58d", 0, None)

    def get_stoken(self, pwd_id, passcode=""):
        return {"data": {"stoken": f"{pwd_id}:{passcode}"}}

    def get_detail(self, pwd_id, stoken, fid):
        key = str(fid or "").strip()
        if key in ("", "0"):
            return {"data": {"list": list(self._root)}}
        return {"data": {"list": list(self._subs.get(key, []))}}


class FakeManager:
    CURRENT = None

    def __init__(self, db, no_login=True):
        pass

    def get_adapter_for_task(self, payload, allow_inactive=True):
        return FakeManager.CURRENT


am.DatabaseAccountManager = FakeManager
try:
    spb.DatabaseAccountManager = FakeManager  # 防御：若模块顶层直接绑定该符号
except Exception:
    pass

SHAREURL = "https://115cdn.com/s/swstbfd3zrk?password=t58d"
mr = MagicRename(magic_regex=None)
mr.set_taskname("无可替代")
_p2, _r2 = mr.magic_regex_conv(PAT, REP)
mr._resolved_pattern, mr._resolved_replace = _p2, _r2

RESULTS: list[tuple[str, bool]] = []


def check(desc: str, cond, extra=""):
    ok = bool(cond)
    RESULTS.append((desc, ok))
    print(("PASS  " if ok else "FAIL  ") + desc + (f"   [{extra}]" if extra else ""))


def run(root, subs=None):
    FakeManager.CURRENT = FakeAdapter(root, subs)
    return spb.fetch_share_file_list_grouped(None, SHAREURL, account_name="115")


def flat_names(groups) -> list[str]:
    """groups 是 (files, fid, ts, dir_name) 四元组列表，这里把各组文件摊平取名"""
    out: list[str] = []
    for grp in groups or []:
        files = grp[0] if isinstance(grp, (tuple, list)) and grp else []
        for f in files or []:
            if isinstance(f, dict):
                out.append(str(f.get("file_name") or f.get("name") or ""))
    return out


# ---- 场景1：分享根下散放 1 集（这就是线上踩到的形态）----
g1 = run([V(REAL_E01)])
names1 = flat_names(g1)
check("场景1 根下散放 1 集 → 能拿到文件（修前返回空列表）",
      len(g1) == 1 and REAL_E01 in names1, f"groups={len(g1)} files={names1}")

# ---- 场景1b：端到端 —— 连贯集数过滤应当放行 E01 ----
if g1:
    res1 = check_consecutive_episodes(g1[0][0], current_episode=0, mr=mr)
    check("场景1b 连贯集数过滤 → (True, [1])，不再『无连贯集数，取消转存』",
          res1[0] is True and list(res1[1]) == [1], res1)
else:
    check("场景1b 连贯集数过滤 → (True, [1])，不再『无连贯集数，取消转存』",
          False, "拿不到文件组，无法放行（这正是线上现象）")

# ---- 场景2：根文件 + 子目录并存 → 子目录组必须仍排第一（保住旧调用方语义）----
g2 = run([V(REAL_E01), D("第2季打包", "sub1")], {"sub1": [V(REAL_E02, fid="f2")]})
check("场景2 根文件与子目录并存 → 两组，且子目录组（非空 fid）仍在最前",
      len(g2) == 2 and str(g2[0][1]) == "sub1" and g2[1][1] == "",
      f"fids={[x[1] for x in g2]} counts={[len(x[0]) for x in g2]}")

# ---- 场景3：只有子目录（回归：原有行为不变）----
g3 = run([D("打包", "sub1")], {"sub1": [V(REAL_E01, fid="f3")]})
check("场景3 只有子目录 → 行为不变（1 组 1 文件）",
      len(g3) == 1 and len(g3[0][0]) == 1, f"groups={len(g3)} counts={[len(x[0]) for x in g3]}")

# ---- 场景4：根下杂质不崩、不产生空组 ----
g4 = run(["junk", {"name": "", "is_dir": False}, {"name": "movie.nfo", "is_dir": False, "size": 10}])
check("场景4 非 dict / 空名项被跳过且不崩（.nfo 交给上层 _is_video_file 过滤，不在此处做类型判断）",
      isinstance(g4, list) and len(g4) <= 1, f"groups={len(g4)}")

# ---- 场景5：根下多集连贯 ----
g5 = run([V(REAL_E01), V(REAL_E02, fid="f2")])
if g5:
    res5 = check_consecutive_episodes(g5[0][0], current_episode=0, mr=mr)
    check("场景5 根下 E01+E02 → (True, [1, 2])",
          res5[0] is True and list(res5[1]) == [1, 2], res5)
else:
    check("场景5 根下 E01+E02 → (True, [1, 2])", False, "拿不到文件组")

# ---- 场景6：根下只有后续集（E14）→ 应判为不连贯但能拿到文件（可回退到缺口策略）----
g6 = run([V("无可替代.S01E14.第14集.2160p.WEB-DL.H265.DTS.mkv")])
names6 = flat_names(g6)
if g6:
    res6 = check_consecutive_episodes(g6[0][0], current_episode=0, mr=mr)
    check("场景6 根下只有 E14 → 文件可见且判定为（不连贯, max_ep=14）",
          len(names6) == 1 and res6[0] is False and res6[2] == 14, res6)
else:
    check("场景6 根下只有 E14 → 文件可见且判定为（不连贯, max_ep=14）",
          False, f"拿不到文件组 names={names6}")

bad = [d for d, ok in RESULTS if not ok]
print(f"\n[verify] 小结: {len(RESULTS) - len(bad)}/{len(RESULTS)} 通过")
if bad:
    print("[verify] 失败项:")
    for d in bad:
        print("   -", d)
    sys.exit(1)
print("[verify] 补丁4 验证全部通过")
