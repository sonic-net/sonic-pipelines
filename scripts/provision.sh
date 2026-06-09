#!/bin/bash

set -ex

ARCH=$1
DEFAULT_ARCH=$(dpkg --print-architecture)
[ -z "$ARCH" ] && [ -f /etc/docker-arch ] && ARCH=$(cat /etc/docker-arch)
[ -z "$ARCH" ] && ARCH=$DEFAULT_ARCH  

wait_for_cloud_init() {
  if command -v cloud-init >/dev/null 2>&1; then
    cloud-init status --wait || true
  fi
}

wait_for_apt_lock() {
  local waited=0
  local timeout=600

  while true; do
    if command -v fuser >/dev/null 2>&1; then
      if ! fuser /var/lib/dpkg/lock-frontend /var/lib/dpkg/lock /var/lib/apt/lists/lock >/dev/null 2>&1; then
        return 0
      fi
    else
      if ! pgrep -x apt >/dev/null 2>&1 && ! pgrep -x apt-get >/dev/null 2>&1 && ! pgrep -x dpkg >/dev/null 2>&1 && ! pgrep -f unattended-upgrades >/dev/null 2>&1; then
        return 0
      fi
    fi

    sleep 5
    waited=$((waited + 5))
    if [ "$waited" -ge "$timeout" ]; then
      echo "apt/dpkg lock held for more than ${timeout}s, giving up." >&2
      return 1
    fi
  done
}

wait_apt_ready() {
  wait_for_cloud_init
  wait_for_apt_lock
}

apt_update_retry() {
  local attempt
  for attempt in 1 2 3; do
    wait_apt_ready
    if apt-get update; then
      return 0
    fi
    echo "apt-get update failed on attempt $attempt, retrying..."
  done
  return 1
}

apt_install_retry() {
  local attempt
  for attempt in 1 2 3; do
    wait_apt_ready
    if apt-get install -y "$@"; then
      return 0
    fi
    echo "apt-get install failed on attempt $attempt for packages: $*"
    apt_update_retry || true
  done
  return 1
}

dump_pkg_diagnostics() {
  local pkg="$1"
  echo "===== apt diagnostics for package: $pkg ====="
  echo "ARCH(default/current): $DEFAULT_ARCH/$ARCH"
  lsb_release -a 2>/dev/null || true
  dpkg --print-architecture || true
  dpkg --print-foreign-architectures || true
  apt-cache policy "$pkg" || true
  apt-cache madison "$pkg" || true
  grep -R "^deb " /etc/apt/sources.list /etc/apt/sources.list.d/*.list 2>/dev/null || true
  grep -R "^Types\\|^URIs\\|^Suites\\|^Components" /etc/apt/sources.list.d/*.sources 2>/dev/null || true
  echo "===== end apt diagnostics =====" >&2
}

apt-get update
NEEDRESTART_MODE=l DEBIAN_FRONTEND=noninteractive apt-get -o Dpkg::Options::="--force-confdef" -o Dpkg::Options::="--force-confold" -y upgrade
apt-get install -y ca-certificates curl gnupg lsb-release
if ! apt_install_retry acl; then
  dump_pkg_diagnostics acl
  echo "acl install failed during provisioning; continuing without acl."
fi

# install git lfs
curl -s https://packagecloud.io/install/repositories/github/git-lfs/script.deb.sh | bash
apt_update_retry
apt_install_retry git-lfs

if [ "$ARCH" == "armhf" ] && [ "$ARCH" != "$DEFAULT_ARCH" ]; then
  dpkg --add-architecture armhf
fi
 
mkdir -p /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg | gpg --dearmor -o /etc/apt/keyrings/docker.gpg --batch --yes
echo "deb [arch=$ARCH signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/ubuntu $(lsb_release -cs) stable" > /etc/apt/sources.list.d/docker.list

apt_update_retry
apt_install_retry docker-ce:$ARCH docker-ce-cli:$ARCH containerd.io:$ARCH docker-compose-plugin:$ARCH

# Customize for armhf
if [ "$ARCH" == "armhf" ] && [ "$ARCH" != "$DEFAULT_ARCH" ]; then
  # Configure docker service
  mkdir -p /etc/systemd/system/docker.service.d
  echo "[Service]" > /etc/systemd/system/docker.service.d/override.conf
  echo "ExecStart=" >> /etc/systemd/system/docker.service.d/override.conf
  echo "ExecStart=/usr/bin/setarch linux32 -B /usr/bin/dockerd -H unix:// --storage-driver overlay2 --data-root /data/docker --ipv6 --fixed-cidr-v6=2603:10a0:100:830::0/64 --experimental" >> /etc/systemd/system/docker.service.d/override.conf

  # Configure container service
  mkdir -p /etc/systemd/system/containerd.service.d
  echo "[Service]" > /etc/systemd/system/containerd.service.d/override.conf
  echo "ExecStart=" >> /etc/systemd/system/containerd.service.d/override.conf
  echo "ExecStart=/usr/bin/setarch linux32 -B /usr/bin/containerd" >> /etc/systemd/system/containerd.service.d/override.conf

  # reload docker container service
  systemctl daemon-reload
  service docker restart
  service containerd restart

  # Verify docker armhf is ready
  machine=$(docker run --rm publicmirror.azurecr.io/debian:bookworm uname -m)
  if [ "$machine" != "armv7l" ] && [ "$machine" != "armv8l" ]; then
    echo "The machine=$machine is not correct, provision failed" 1>&2
    exit 1
  fi
else
  systemctl stop docker
  sed -i 's/^ExecStart=.*$/& --data-root \/data\/docker/' /lib/systemd/system/docker.service
  systemctl daemon-reload
  systemctl start docker
fi
usermod -a -G docker azureuser || true
cat /etc/passwd /etc/group || true

# Install build tools (and waiting docker ready)
apt-get install -y build-essential nfs-common python3-pip python3-setuptools python3-pip python-is-python3
pip3 install jinja2 j2cli markupsafe
