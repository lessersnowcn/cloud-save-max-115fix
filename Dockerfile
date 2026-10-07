# cloud-save-max 官方镜像 + 四个补丁（非官方构建，来源与改动见 README）
#   补丁1 115 容量解析崩溃：账号卡片 error 的根因（int() 碰到带小数的字符串）
#   补丁2 自动换链搜索兜底：深度搜索(deep=1)返回空/半空时，补一轮缓存搜索(deep=0)，两轮合并去重
#   补丁3 搜索源被静默跳过时回传原因：如 CloudSaver 未配置密码，界面不再"无声无结果"
#   补丁4 分享根目录散放文件被忽略：无 fid 分支只遍历子目录 → 剧集被判"无连贯集数"而永不转存
ARG BASE_IMAGE=wuanqicll/cloud-save-max:26.9.6
FROM ${BASE_IMAGE}
ARG BASE_IMAGE

LABEL org.opencontainers.image.title="cloud-save-max-115fix" \
      org.opencontainers.image.description="cloud-save-max + 115 容量解析修复 + 自动换链搜索兜底 + 搜索源静默跳过原因回传 + 分享根目录散放文件归组修复，基于官方 ${BASE_IMAGE}" \
      org.opencontainers.image.source="https://github.com/wuanqicll-del/cloud-save-max" \
      org.opencontainers.image.base.name="${BASE_IMAGE}"

COPY patch_115_capacity.py /tmp/patch_115_capacity.py
COPY patch_drama_deep_search.py /tmp/patch_drama_deep_search.py
COPY patch_source_skip_notice.py /tmp/patch_source_skip_notice.py
COPY patch_sharepreview_rootfiles.py /tmp/patch_sharepreview_rootfiles.py
COPY verify_searchfix.py /tmp/verify_searchfix.py
COPY verify_source_notice.py /tmp/verify_source_notice.py
COPY verify_rootfiles_fix.py /tmp/verify_rootfiles_fix.py

# 补丁 1：115 容量解析崩溃 + 语法自检 + 结果核对；任何一步不过，构建直接失败（宁可构建失败，不可带病上线）
RUN python3 /tmp/patch_115_capacity.py /app/backend/app/extensions/adapters/cloud115_adapter.py \
 && python3 -c "import ast,pathlib; ast.parse(pathlib.Path('/app/backend/app/extensions/adapters/cloud115_adapter.py').read_text(encoding='utf-8')); print('115补丁语法自检通过')" \
 && grep -n "_safe_int" /app/backend/app/extensions/adapters/cloud115_adapter.py

# 补丁 2：自动换链搜索兜底 + 语法自检 + 用桩函数跑真代码的 5 场景校验 + 结构核对
RUN python3 /tmp/patch_drama_deep_search.py /app/backend/app/services/drama_share_autoupdate.py \
 && python3 -c "import ast,pathlib; ast.parse(pathlib.Path('/app/backend/app/services/drama_share_autoupdate.py').read_text(encoding='utf-8')); print('搜索补丁语法自检通过')" \
 && python3 /tmp/verify_searchfix.py \
 && grep -n "for deep in (1, 0):" /app/backend/app/services/drama_share_autoupdate.py

# 补丁 3：搜索源被静默跳过时回传原因 + 语法自检 + 桩函数跑真代码的 7 场景校验 + 残留检查
RUN python3 /tmp/patch_source_skip_notice.py /app/backend/app/services/resource_search.py \
 && python3 -c "import ast,pathlib; ast.parse(pathlib.Path('/app/backend/app/services/resource_search.py').read_text(encoding='utf-8')); print('搜索源提示补丁语法自检通过')" \
 && python3 /tmp/verify_source_notice.py \
 && grep -n "source_notes" /app/backend/app/services/resource_search.py

# 补丁 4：分享根目录散放文件归组 + 语法自检 + 桩适配器跑真代码的 6 场景（含修前必失败的 5 项）+ 标记核对
RUN python3 /tmp/patch_sharepreview_rootfiles.py /app/backend/app/services/share_preview_batch.py \
 && python3 -c "import ast,pathlib; ast.parse(pathlib.Path('/app/backend/app/services/share_preview_batch.py').read_text(encoding='utf-8')); print('根目录文件归组补丁语法自检通过')" \
 && python3 /tmp/verify_rootfiles_fix.py \
 && grep -n "_root_level_files_fix" /app/backend/app/services/share_preview_batch.py

# 清掉构建期的补丁/验证脚本，不进最终镜像
RUN rm -f /tmp/patch_115_capacity.py /tmp/patch_drama_deep_search.py /tmp/patch_source_skip_notice.py \
          /tmp/patch_sharepreview_rootfiles.py /tmp/verify_searchfix.py /tmp/verify_source_notice.py \
          /tmp/verify_rootfiles_fix.py
