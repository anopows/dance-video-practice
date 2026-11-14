#!/usr/bin/env bash
set -euo pipefail
cd /opt/choreo
apt-get update -qq
DEBIAN_FRONTEND=noninteractive apt-get install -y -qq bubblewrap python3-venv ffmpeg rubberband-cli fonts-dejavu-core git curl caddy age rclone
id choreo >/dev/null 2>&1 || useradd --system --home-dir /var/lib/choreo --shell /usr/sbin/nologin choreo
install -d -m 0755 /opt/choreo/bin /var/lib/choreo/models
install -d -o choreo -g choreo -m 0700 /var/lib/choreo/jobs
chown choreo:choreo /var/lib/choreo
install -m 0755 /usr/bin/bwrap /opt/choreo/bin/bwrap
install -m 0644 deploy/choreo-bwrap.apparmor /etc/apparmor.d/choreo-bwrap
apparmor_parser -r /etc/apparmor.d/choreo-bwrap
if [ ! -x /opt/choreo/bin/uv ]; then
  curl -LsSf https://astral.sh/uv/0.9.8/install.sh | env UV_INSTALL_DIR=/opt/choreo/bin UV_NO_MODIFY_PATH=1 sh
fi
UV_CACHE_DIR=/var/cache/choreo-uv /opt/choreo/bin/uv sync --frozen --extra worker --python /usr/bin/python3
install -m 0644 deploy/choreo-api.service deploy/choreo-worker.service deploy/choreo-backup.service deploy/choreo-backup.timer deploy/choreo-health.service deploy/choreo-health.timer /etc/systemd/system/
systemctl daemon-reload
sudo -u choreo /opt/choreo/bin/bwrap --unshare-all --ro-bind /usr /usr --ro-bind /lib /lib --ro-bind /lib64 /lib64 --proc /proc --dev /dev /usr/bin/true
echo 'Runtime and sandbox installed. Configure models and /etc/choreo.env before enabling services.'
