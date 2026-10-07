# cloud-save-max-115fix

给 [cloud-save-max](https://github.com/wuanqicll-del/cloud-save-max) 打的**一个 bug 修复**镜像：115 网盘账号探测时容量解析崩溃。

> 非官方构建。上游项目版权归其作者所有，本仓库只包含「一个补丁脚本 + 一个 4 行 Dockerfile」，不含上游源码。

## 修的是什么

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

## 怎么用

### 1. 让它构建（GitHub Actions，免费）

1. 在 GitHub 建一个仓库，名字建议 `cloud-save-max-115fix`（**public**，见下方说明）
2. 把本目录内容整个推上去：`git init && git add -A && git commit -m init && git push`
3. Actions 会自动跑，构建 `ghcr.io/<你的用户名>/cloud-save-max-115fix:26.9.6-115fix` 并推送
4. 首次推送后去 GitHub → 你的头像 → Packages 里能看到这个包

### 2. NAS 上切过去

把现有 `docker-compose.yml` 的 `image:` 行改成你的镜像，然后重新部署（极空间 Docker 界面的 Compose 里改，别只改宿主机文件）：

```yaml
    image: ghcr.io/<你的用户名小写>/cloud-save-max-115fix:26.9.6-115fix
```

拉取 + 重建后，去页面「账号管理 → 115 账号 → 探测」，状态应恢复正常并显示容量。

### 3. 上游升级

Actions 页面点 **Run workflow**，把 `base_tag` 填成新版本（如 `26.10.1`）即可；
新镜像 tag 就是 `26.10.1-115fix`。如果上游已经自己修了这个 bug，构建会**失败**（找不到锚点），
那就说明补丁可以退休了，直接用官方镜像。

## 为什么不直接用官方镜像 + 本地改

- 指向自己的镜像名后，`docker compose pull` / 界面「更新镜像」**不会再悄悄把补丁冲掉**
- 换机器、重装，一条 pull 就能复制出同样的环境
- 补丁随上游升级只需改一个 `base_tag`，不用再手工进容器改文件

## 边界与注意

- **镜像里不含你的任何账号信息或数据**：只有官方 base 层 + 一个改过的 `.py`。你的 115 cookie、SQLite 都在 NAS 的 `./cloud-save-max/data` 卷里
- **建议 public 仓库**：GHCR 包会继承仓库可见性。仓库 public → 包 public → NAS 直接拉，不用登录；private → NAS 拉取要 `docker login`（那就要 shell 了，等于绕回原点）
- 补丁只覆盖 `size_used_raw` / `size_total_raw` 两处。**同类隐患未修**：`cloud189_adapter.py` 里 `int(cloud_capacity.get("usedSize", 0))` 有一样的味道
- 另有一个上游行为要注意：`PATCH /accounts/{id}/status` 在探测不是 active 时会把账号**自动置为 enabled=False**，别反复点启用开关
