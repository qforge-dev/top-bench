#!/usr/bin/env bash

set -Eeuo pipefail

deploy_root=${1:?usage: deploy.sh DEPLOY_ROOT ARCHIVE_PATH}
archive_path=${2:?usage: deploy.sh DEPLOY_ROOT ARCHIVE_PATH}

if [[ "$deploy_root" != /home/ubuntu/* || "$archive_path" != /tmp/top-arena-*.tar.gz ]]; then
  echo "Refusing unexpected deployment paths" >&2
  exit 2
fi

stage_dir=$(mktemp -d /tmp/top-arena-stage.XXXXXX)

cleanup() {
  if [[ "$stage_dir" == /tmp/top-arena-stage.* ]]; then
    rm -rf -- "$stage_dir"
  fi
  rm -f -- "$archive_path"
}
trap cleanup EXIT

tar -xzf "$archive_path" -C "$stage_dir"

required_files=(
  "$stage_dir/pyproject.toml"
  "$stage_dir/uv.lock"
  "$stage_dir/apps/leaderboard/pyproject.toml"
  "$stage_dir/packages/top-arena/pyproject.toml"
  "$stage_dir/infra/alembic.ini"
  "$stage_dir/infra/caddy/top-arena.caddy"
  "$stage_dir/infra/systemd/top-arena.service"
)
for required_file in "${required_files[@]}"; do
  if [[ ! -f "$required_file" ]]; then
    echo "Deployment archive is missing $required_file" >&2
    exit 2
  fi
done

install -d \
  "$deploy_root/apps/leaderboard" \
  "$deploy_root/packages/top-arena" \
  "$deploy_root/infra"
rsync -a --delete "$stage_dir/apps/leaderboard/" "$deploy_root/apps/leaderboard/"
rsync -a --delete "$stage_dir/packages/top-arena/" "$deploy_root/packages/top-arena/"
rsync -a --delete "$stage_dir/infra/" "$deploy_root/infra/"
install -m 0644 "$stage_dir/pyproject.toml" "$deploy_root/pyproject.toml"
install -m 0644 "$stage_dir/uv.lock" "$deploy_root/uv.lock"

cd "$deploy_root"
/home/ubuntu/.local/bin/uv sync \
  --locked \
  --no-dev \
  --package top-arena-leaderboard \
  --python 3.14

set -a
# The production file is installed by the one-time host setup and is root-owned.
# shellcheck disable=SC1091
source /etc/top-arena/top-arena.env
set +a
"$deploy_root/.venv/bin/alembic" -c infra/alembic.ini upgrade head

sudo install -o root -g root -m 0644 \
  "$deploy_root/infra/systemd/top-arena.service" \
  /etc/systemd/system/top-arena.service

caddy_destination=/etc/caddy/conf.d/top-arena.caddy
caddy_backup="$stage_dir/top-arena.caddy.previous"
had_caddy_config=false
if [[ -f "$caddy_destination" ]]; then
  cp "$caddy_destination" "$caddy_backup"
  had_caddy_config=true
fi
sudo install -D -o root -g root -m 0644 \
  "$deploy_root/infra/caddy/top-arena.caddy" \
  "$caddy_destination"

sudo systemctl daemon-reload
if ! sudo caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile; then
  if [[ "$had_caddy_config" == true ]]; then
    sudo install -o root -g root -m 0644 "$caddy_backup" "$caddy_destination"
  else
    sudo unlink "$caddy_destination"
  fi
  echo "Restored the previous Caddy configuration after validation failed" >&2
  exit 1
fi
if ! sudo systemctl restart caddy; then
  if [[ "$had_caddy_config" == true ]]; then
    sudo install -o root -g root -m 0644 "$caddy_backup" "$caddy_destination"
  else
    sudo unlink "$caddy_destination"
  fi
  sudo systemctl restart caddy
  echo "Restored the previous Caddy configuration after restart failed" >&2
  exit 1
fi
sudo systemctl restart top-arena

for _ in {1..20}; do
  if curl --fail --silent --show-error http://127.0.0.1:8910/health >/dev/null; then
    echo "Top Arena deployment is healthy"
    exit 0
  fi
  sleep 1
done

sudo systemctl status top-arena --no-pager || true
sudo journalctl -u top-arena -n 100 --no-pager || true
echo "Top Arena did not become healthy after deployment" >&2
exit 1
