# cloud-save-max 官方镜像 + 115 容量解析修复（非官方构建，来源与改动见 README）
ARG BASE_IMAGE=wuanqicll/cloud-save-max:26.9.6
FROM ${BASE_IMAGE}
ARG BASE_IMAGE

LABEL org.opencontainers.image.title="cloud-save-max-115fix"       org.opencontainers.image.description="cloud-save-max + 115 容量解析崩溃修复(_safe_int)，基于官方 ${BASE_IMAGE}"       org.opencontainers.image.source="https://github.com/wuanqicll-del/cloud-save-max"       org.opencontainers.image.base.name="${BASE_IMAGE}"

COPY patch_115_capacity.py /tmp/patch_115_capacity.py

# 打补丁 + 语法自检 + 结果核对；任何一步不过，构建直接失败（宁可构建失败，不可带病上线）
RUN python3 /tmp/patch_115_capacity.py /app/backend/app/extensions/adapters/cloud115_adapter.py  && python3 -c "import ast,pathlib; ast.parse(pathlib.Path('/app/backend/app/extensions/adapters/cloud115_adapter.py').read_text(encoding='utf-8')); print('语法自检通过')"  && grep -n "_safe_int" /app/backend/app/extensions/adapters/cloud115_adapter.py  && rm -f /tmp/patch_115_capacity.py
