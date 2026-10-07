# cloud-save-max-115fix

给 [cloud-save-max](https://github.com/wuanqicll-del/cloud-save-max) 打的**三个 bug 修复**镜像（115 容量解析崩溃 / 自动换链搜不到候选 / 搜索源被静默跳过）。

> 非官方构建。上游项目版权归其作者所有，本仓库只包含「三个补丁脚本 + 若干验证脚本 + 一个 Dockerfile」，不含上游源码。
> 本目录是**增量更新包**（覆盖到你已有的仓库上）：新增 `patch_source_skip_notice.py`、`verify_source_notice.py`，改了 `Dockerfile`、`.github/workflows/build.yml`、`README.md`。

## 修的是什么

### 修复 1：115 账号探测时容量解析崩溃

**现象**：115 账号卡片状态显示 `error`，`last_error` 是

```
invalid literal for int() with base 10: '1205409640142.8'
```

**根因**：115 的 `base_info` 接口返回的 `size_used_raw` / `size_total_raw` 是**带小数的字符串**，
而 `backend/app/extensions/adapters/cloud115_adapter.py` 里 `get_account_config()` 直接 `int()`
→ `ValueError` → 上层 `drive_accounts.py` 的探测流程把它当成**账号故障**（写 `runtime_status='error'`）。
账号本身没事（同一个接口取 `user_name` 是成功的，cookie 有效）。

**改法**：把那两行 `int(...)` 换成 `_safe_int(...)`，并注入一个转不了的返回 `None` 的小函数。
只动 1 个文件、2 行 + 1 个函数，幂等（打过就跳过），找不到锚点就报错退出。

### 修复 2：自动换链「搜不到候选」

**现象**：追剧任务的自动换链不动了 —— 已跟到 E14，但不会往后换新分享。换链日志里 `reason=no_candidates`。

**根因**：自动换链的候选搜索写死 `deep=1`（PanSou `refresh=true`，实时抓取），
而实时抓取对**部分关键词会返回空**，同一关键词的缓存搜索反而有结果：

| 关键词 | deep=1（实时） | deep=0（缓存） |
|---|---|---|
| 无可替代 | **0 条**（3/3 复现） | 19 条（含 S01E01–E14） |
| 庆余年 | 正常 | 正常 |

即「实时抓取取不到」是个**半空**场景，不是 PanSou 整体故障。

**改法**：同一关键词搜两轮 —— deep=1（实时，最新优先）→ deep=0（缓存兜底），
按 shareurl 去重，深度结果排在前；原有候选校验/排序/连贯性判断全部不动。
文件：`backend/app/services/drama_share_autoupdate.py` 的 `_search_candidates()`。

> 为什么不是「仅当 deep=1 为空才回退」：deep=1 有时返回**少量无关结果**（本剧实测回了 1 条不相关的「秘密」），
> 那种"空则回退"根本不会触发，换链照样卡死。合并两轮能覆盖这种半空场景。

**这次改动不会让任何剧变差**：deep=1 的结果一条不丢，只是后面补一轮缓存搜索。
仍然换不上链的情况是别的原因：①源头没资源 ②连贯性规则（必须从当前集+1 起连续）③分享者被过滤 ④命中过滤词。

**构建期校验**：`verify_searchfix.py` 用桩函数喂真实场景数据、直接跑打补丁后的那段循环，
5 个场景（空兜底 / 半空去重 / 脏数据 / 跨关键词 / 原有全局去重仍在）不过就构建失败。

### 修复 3：搜索源被「静默跳过」，界面看不出来（2026-10-07 追加）

**现象**：某个搜索源（这里是 CloudSaver）明明配了、也启用了，但**一条结果都搜不出来**，
界面也没有任何报错 —— 只显示「已限定网盘类型：115」，用户完全看不出少了一个源。

**根因**：`backend/app/services/resource_search.py` 的 `cs_search()` 里，凭据不全时直接静默返回：

```python
if not (server and username and password):
    return ([], None)      # ← 不报错、不记日志、message 里也不提
```

真实现场：库里的 cloudsaver **只有 username，password 为空**（CloudSaver 的密码被误填进了 `token` 字段
——初期初始化向导里只有「Token」输入框、没有「密码」框，很容易填错）。于是：

- 用 token 去搜索 → CloudSaver 返回 `401 无效的 token`
- 自动重登逻辑因为 `password` 为空 → 重登也失败
- 结果：CloudSaver 一条都不出，所有结果其实**全来自 PanSou**（实测同一关键词：修复前 2 条 / 修复后 7 条）

**改法**：只在被跳过的三处返回里补上原因，并把原因拼到返回给界面的 `message` 最前面，例如：

```
⚠ CloudSaver 未配置密码，已跳过（token 由密码自动登录维护，无需手填）; 已限定网盘类型：115
⚠ CloudSaver 不可用：无效的 token，已跳过; 已限定网盘类型：115
```

- 「源被主动禁用」**不产生提示**（那是用户的主动选择，不该有噪音）
- 搜索、过滤、排序、自动登录写回 token 的逻辑**一行未改**；界面原样显示 `message`，所以**不用重构建前端**

**构建期校验**：`verify_source_notice.py` 用桩函数（DB/两个源客户端/缓存全桩掉，不联网、不碰数据库）
跑真代码的 7 个场景：两源正常 / 未配密码 / 凭据全空 / 密码错 / 客户端异常 / 源被禁用 / 缓存仍写入。

> **顺带说明维护方式**：这个源的 `Token` 是**自动维护**的（页面上的 Token 框是灰的、占位符写着「自动登录后会写入 token」），
> 你要维护的是**密码**：**系统设置 → 资源搜索 → 搜索引擎选 CloudSaver → 密码（留空表示不修改）→ 保存**。
> 密码对，token 过期后会自动重登并写回，不需要手填。

## 怎么用

### 1. 让它构建（GitHub Actions，免费）

1. 在你已有的仓库（`cloud-save-max-115fix`）里，把本目录内容按相对路径覆盖上去
2. commit + push 到 `main`
3. Actions 会自动跑，构建并推送：
   - `ghcr.io/<你的用户名小写>/cloud-save-max-115fix:26.9.6-115fix.3`
   - `ghcr.io/<你的用户名小写>/cloud-save-max-115fix:latest-115fix`
4. 构建日志里会看到三行自检输出：`115补丁语法自检通过` / `搜索补丁语法自检通过` / `搜索源提示补丁语法自检通过`，
   加上 `全部 7 个场景通过`。**任何一步不过，构建就是红的**，不会推一个带病的镜像

### 2. NAS 上切过去

把现有 `docker-compose.yml` 的 `image:` 行改成你的镜像，然后重新部署（极空间 Docker 界面的 Compose 里改，别只改宿主机文件）：

```yaml
    image: ghcr.io/<你的用户名小写>/cloud-save-max-115fix:26.9.6-115fix.3
```

> 搜索结果有 **300 秒缓存**（`preview_cache_ttl_seconds`），切过去后最多 5 分钟就是新行为；急着重启一下容器即可。

### 3. 上游升级

Actions 页面点 **Run workflow**，把 `base_tag` 填成新版本（如 `26.10.1`）即可；
新镜像 tag 就是 `26.10.1-115fix.3`（后缀可在同一次运行里改）。如果上游已经自己修了某个 bug，
构建会**失败**（找不到锚点），那就说明对应补丁可以退休了。

## 镜像 tag 怎么选

| tag | 内容 |
|---|---|
| `26.9.6-115fix` | 只有 115 容量修复（v1，已在 ghcr） |
| `26.9.6-115fix.3` | 115 容量修复 + 自动换链搜索兜底 + 搜索源跳过原因回传（当前推荐） |
| `latest-115fix` | 始终指向最近一次构建（移动别名） |

回滚就是把 compose 的 image 行改回 `26.9.6-115fix`。

## 为什么不直接用官方镜像 + 本地改

- 指向自己的镜像名后，`docker compose pull` / 界面「更新镜像」**不会再悄悄把补丁冲掉**
- 换机器、重装，一条 pull 就能复制出同样的环境
- 补丁随上游升级只需改一个 `base_tag`，不用再手工进容器改文件

## 边界与注意

- **镜像里不含你的任何账号信息或数据**：只有官方 base 层 + 三个改过的 `.py`。你的 115 cookie、SQLite 都在 NAS 的 `./cloud-save-max/data` 卷里
- **建议 public 仓库**：GHCR 包会继承仓库可见性。仓库 public → 包 public → NAS 直接拉，不用登录；private → NAS 拉取要 `docker login`（那就要 shell 了，等于绕回原点）
- 补丁只覆盖 `size_used_raw` / `size_total_raw` 两处。**同类隐患未修**：`cloud189_adapter.py` 里 `int(cloud_capacity.get("usedSize", 0))` 有一样的味道
- 修复 3 只覆盖 CloudSaver 这一条源；PanSou 仍是静默跳过（它是 `refresh` 走的实时抓取，失败多为上游网络抖动，怕刷屏）
- 另有一个上游行为要注意：`PATCH /accounts/{id}/status` 在探测不是 active 时会把账号**自动置为 enabled=False**，别反复点启用开关
