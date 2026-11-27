#!/bin/bash
# Runs as root under a transient systemd service. No credentials are stored here.
set -Eeuo pipefail
export LC_ALL=C
export DEBIAN_FRONTEND=noninteractive
umask 022
task_dir=$1
service=$2
expected_hash=$3
expected_package=$4
expected_version=$5
case "$service" in webservice.service|nerva-web.service) ;; *) exit 64 ;; esac
[[ "$task_dir" =~ ^/var/lib/robot-web-installer/tasks/[a-f0-9]{32}$ ]] || exit 64
[[ "$expected_hash" =~ ^[a-f0-9]{64}$ ]] || exit 64
cd "$task_dir"
exec >> deploy.log 2>&1
chmod 644 deploy.log
stage=preflight
write_status() {
    printf '%s\n' "$1" > status.tmp
    chmod 644 status.tmp
    mv status.tmp status
}
on_error() {
    result=$?
    trap - ERR
    if [ "$stage" = service ]; then
        journalctl -u "$service" -n 60 --no-pager || true
    fi
    printf '\n任务失败：stage=%s exit=%s\n' "$stage" "$result"
    write_status "failed:$stage:$result"
    exit "$result"
}
trap on_error ERR
write_status "running:preflight"
exec 9>/run/lock/robot-web-installer.lock
if ! flock -n 9; then
    echo '另一个机器人部署任务正在执行'
    write_status 'failed:busy:73'
    exit 73
fi
echo '[10%] 检查机器人与安装包'
. /etc/os-release
if [ "$ID" != ubuntu ] || ! [[ "$VERSION_ID" =~ ^(20\.04|22\.04|24\.04)$ ]]; then
    echo '只支持 Ubuntu 20.04、22.04、24.04 机器人'
    false
fi
printf '%s  package.deb\n' "$expected_hash" | sha256sum --check --status
actual_package=$(dpkg-deb -f package.deb Package)
actual_version=$(dpkg-deb -f package.deb Version)
actual_arch=$(dpkg-deb -f package.deb Architecture)
test "$actual_package" = "$expected_package"
test "$actual_version" = "$expected_version"
test "$actual_arch" = all || test "$actual_arch" = "$(dpkg --print-architecture)"
audit=$(dpkg --audit)
if [ -n "$audit" ]; then
    echo "$audit"
    echo '包管理器有未完成任务，请维护后重试'
    false
fi
printf 'package=%s\nversion=%s\nservice=%s\n' "$actual_package" "$actual_version" "$service" > metadata
dpkg-query -W -f='${Version}\n' "$actual_package" > previous-version 2>/dev/null || true
systemctl is-enabled "$service" > previous-enabled 2>/dev/null || true
systemctl is-active "$service" > previous-active 2>/dev/null || true
# Back up dpkg-managed /etc conffiles only. Application data needs a product-specific policy.
dpkg-query -W -f='${Conffiles}\n' "$actual_package" 2>/dev/null \
    | awk '$1 ~ /^\/etc\// {print $1}' > conffiles.list || true
if [ -s conffiles.list ]; then
    tar --ignore-failed-read -czf conffiles.tar.gz -T conffiles.list
    chmod 600 conffiles.tar.gz
fi
echo '[20%] 模拟安装，检查依赖与包冲突'
write_status 'running:dependencies'
stage=dependencies
# A private empty source/index configuration permits local file acquisition only.
# --no-download breaks local-DEB acquisition on some APT versions, so do not use it.
mkdir -p empty-lists/partial
apt_options=(-o DPkg::Lock::Timeout=30 -o Dir::Etc::sourcelist=/dev/null
    -o Dir::Etc::sourceparts=- -o "Dir::State::lists=$task_dir/empty-lists"
    --no-remove --no-install-recommends)
apt-get "${apt_options[@]}" --simulate install "$task_dir/package.deb"
echo '[40%] 安装 DEB（不从互联网获取依赖）'
write_status 'running:install'
stage=install
apt-get "${apt_options[@]}" -o Dpkg::Options::=--force-confold \
    --yes --reinstall install "$task_dir/package.deb"
test "$(dpkg-query -W -f='${Status}' "$actual_package")" = 'install ok installed'
test "$(dpkg-query -W -f='${Version}' "$actual_package")" = "$expected_version"
echo '[80%] 设置开机自启并重启服务'
write_status 'running:service'
stage=service
systemctl daemon-reload
systemctl enable "$service"
systemctl restart "$service"
echo '[90%] 观察服务运行状态'
for attempt in $(seq 1 10); do
    sleep 1
    systemctl is-active --quiet "$service"
done
systemctl is-enabled --quiet "$service"
echo '[100%] 安装成功，服务已自启并持续运行 10 秒'
write_status 'succeeded'
# The uploaded package is no longer needed; retain status, metadata, logs and config backup.
rm -f package.deb || true
