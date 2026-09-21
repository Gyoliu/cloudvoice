#!/usr/bin/env bash

# 在目标 Linux 服务器上创建不可变版本目录，切换 current 软链接并执行健康检查。
set -Eeuo pipefail

SERVICE_NAME="googlecloudvoice"
HEALTHCHECK_URL="http://127.0.0.1:8000/"
HEALTHCHECK_ATTEMPTS=20
HEALTHCHECK_INTERVAL_SECONDS=1

DEPLOY_ROOT="${DEPLOY_ROOT:-}"
RELEASE_SHA="${RELEASE_SHA:-}"
ARCHIVE_PATH="${ARCHIVE_PATH:-}"
staging_dir=""
release_in_progress=""

cleanup() {
  # staging_dir 只可能来自 DEPLOY_ROOT/releases 下的 mktemp。
  if [[ -n "$staging_dir" && -d "$staging_dir" ]]; then
    rm -rf -- "$staging_dir"
  fi

  # 失败时只清理由本次部署创建、且尚未标记完成的确定版本目录。
  if [[ -n "$release_in_progress" && -d "$release_in_progress" ]]; then
    rm -rf -- "$release_in_progress"
  fi

  # 仅允许删除本 Workflow 上传到 /tmp 的确定格式归档。
  if [[ "$ARCHIVE_PATH" =~ ^/tmp/googlecloudvoice-[0-9a-f]{40}\.tar\.gz$ ]]; then
    rm -f -- "$ARCHIVE_PATH"
  fi
}
trap cleanup EXIT

fail() {
  echo "Deployment error: $*" >&2
  exit 1
}

switch_current() {
  local target="$1"
  local next_link="$DEPLOY_ROOT/.current-${RELEASE_SHA}-$$"

  ln -s "$target" "$next_link"
  # GNU mv 的 -T 可保证替换的是软链接本身，而不是链接指向的目录。
  mv -Tf "$next_link" "$DEPLOY_ROOT/current"
}

[[ "$DEPLOY_ROOT" =~ ^/[A-Za-z0-9._-]+(/[A-Za-z0-9._-]+)+$ ]] \
  || fail "DEPLOY_ROOT must be a safe absolute path with at least two components"
[[ "$RELEASE_SHA" =~ ^[0-9a-f]{40}$ ]] \
  || fail "RELEASE_SHA must be a full lowercase Git commit SHA"
[[ "$ARCHIVE_PATH" == "/tmp/googlecloudvoice-${RELEASE_SHA}.tar.gz" ]] \
  || fail "ARCHIVE_PATH does not match RELEASE_SHA"
[[ -f "$ARCHIVE_PATH" ]] || fail "release archive does not exist"
command -v python3 >/dev/null || fail "python3 is not installed"
command -v curl >/dev/null || fail "curl is not installed"
command -v sudo >/dev/null || fail "sudo is not installed"
command -v systemctl >/dev/null || fail "systemctl is not installed"

releases_dir="$DEPLOY_ROOT/releases"
release_dir="$releases_dir/$RELEASE_SHA"
mkdir -p "$releases_dir"

previous_release=""
if [[ -L "$DEPLOY_ROOT/current" ]]; then
  previous_release="$(readlink -f "$DEPLOY_ROOT/current" || true)"
fi

if [[ -d "$release_dir" && ! -f "$release_dir/.release-ready" ]]; then
  incomplete_dir="$releases_dir/.incomplete-${RELEASE_SHA}-$(date +%s)-$$"
  echo "Quarantining incomplete release as $incomplete_dir." >&2
  mv "$release_dir" "$incomplete_dir"
fi

if [[ ! -d "$release_dir" ]]; then
  staging_dir="$(mktemp -d "$releases_dir/.${RELEASE_SHA}.XXXXXX")"
  tar -xzf "$ARCHIVE_PATH" -C "$staging_dir"

  # venv 启动脚本包含绝对路径，所以源码先进入最终目录，再创建虚拟环境。
  mv "$staging_dir" "$release_dir"
  staging_dir=""
  release_in_progress="$release_dir"
  python3 -m venv "$release_dir/.venv"
  "$release_dir/.venv/bin/python" -m pip install \
    --require-hashes \
    -r "$release_dir/backend/requirements.lock"

  # 只有依赖完整安装后才允许后续任务复用该版本。
  touch "$release_dir/.release-ready"
  release_in_progress=""
else
  echo "Release $RELEASE_SHA already exists; reusing it."
fi

previous_pid="$(systemctl show --property=MainPID --value "$SERVICE_NAME" 2>/dev/null || true)"
switch_current "$release_dir"
sudo systemctl restart "$SERVICE_NAME"

for ((attempt = 1; attempt <= HEALTHCHECK_ATTEMPTS; attempt++)); do
  if systemctl is-active --quiet "$SERVICE_NAME" \
    && curl --fail --silent --show-error --max-time 2 "$HEALTHCHECK_URL" >/dev/null; then
    current_pid="$(systemctl show --property=MainPID --value "$SERVICE_NAME")"
    active_since="$(systemctl show --property=ActiveEnterTimestamp --value "$SERVICE_NAME")"
    echo "Release $RELEASE_SHA is healthy and active."
    echo "Service restart verified: previous_pid=${previous_pid:-unknown}, current_pid=$current_pid"
    echo "Service active since: $active_since; health endpoint: $HEALTHCHECK_URL"
    exit 0
  fi
  sleep "$HEALTHCHECK_INTERVAL_SECONDS"
done

echo "Health check failed; rolling back." >&2
if [[ -n "$previous_release" && -d "$previous_release" ]]; then
  switch_current "$previous_release"
  sudo systemctl restart "$SERVICE_NAME"
  echo "Rolled back to $previous_release." >&2
else
  # 首次发布失败时没有可回退版本，移除无效入口并停止服务。
  rm -f -- "$DEPLOY_ROOT/current"
  sudo systemctl stop "$SERVICE_NAME" || true
  echo "No previous release was available; service was stopped." >&2
fi

exit 1
