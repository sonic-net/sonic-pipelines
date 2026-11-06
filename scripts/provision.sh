#!/bin/bash

set -exo pipefail

ARCH=$1
DEFAULT_ARCH=$(dpkg --print-architecture)
[ -z "$ARCH" ] && [ -f /etc/docker-arch ] && ARCH=$(cat /etc/docker-arch)
[ -z "$ARCH" ] && ARCH=$DEFAULT_ARCH  

# Avoid slow Azure Ubuntu mirror downloads on amd64 hosts.
if [ "$DEFAULT_ARCH" == "amd64" ]; then
  for source in /etc/apt/sources.list \
                /etc/apt/sources.list.d/*.list \
                /etc/apt/sources.list.d/*.sources; do
    if [ -f "$source" ]; then
      sed -i \
        's|://azure\.archive\.ubuntu\.com/ubuntu|://archive.ubuntu.com/ubuntu|g' \
        "$source"
    fi
  done
fi

dpkg --configure -a
apt-get update
DEBIAN_FRONTEND=noninteractive NEEDRESTART_MODE=a \
  apt-get upgrade -y --with-new-pkgs \
  -o Dpkg::Options::="--force-confdef" \
  -o Dpkg::Options::="--force-confold"
apt-get install -y ca-certificates curl gnupg lsb-release

# Install the Bazel builder profile
apt-get install -y apparmor
if [ "$(cat /sys/module/apparmor/parameters/enabled)" != Y ]; then
  echo "ERROR: AppArmor must be enabled to install the Bazel builder profile." >&2
  exit 1
fi
if [ ! -f /proc/sys/kernel/apparmor_restrict_unprivileged_userns ]; then
  echo "ERROR: Agent kernel does not expose AppArmor user namespace mediation." >&2
  exit 1
fi
mkdir -p /etc/apparmor.d
cat > /etc/apparmor.d/sonic-bazel-builder <<'EOF'
abi <abi/4.0>,
include <tunables/global>

# Explicitly selected by Docker, not attached to arbitrary executable paths.
# Preserve the builder's existing unconfined access, with userns permission.
profile sonic-bazel-builder flags=(default_allow) {
    userns,
}
EOF
if ! apparmor_parser -r -T /etc/apparmor.d/sonic-bazel-builder; then
  echo "ERROR: Cannot load the sonic-bazel-builder AppArmor profile." >&2
  exit 1
fi
if ! grep -q '^sonic-bazel-builder ' /sys/kernel/security/apparmor/profiles; then
  echo "ERROR: sonic-bazel-builder is missing from the loaded AppArmor profiles." >&2
  exit 1
fi
echo "Installed sonic-bazel-builder; select it with --security-opt apparmor=sonic-bazel-builder."

# install git lfs
curl -s https://packagecloud.io/install/repositories/github/git-lfs/script.deb.sh | bash
apt-get install -y git-lfs acl
apt-get install -y libnftables1 nftables

if [ "$ARCH" == "armhf" ] && [ "$ARCH" != "$DEFAULT_ARCH" ]; then
  dpkg --add-architecture armhf
fi
 
mkdir -p /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg | gpg --dearmor -o /etc/apt/keyrings/docker.gpg --batch --yes
echo "deb [arch=$ARCH signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/ubuntu $(lsb_release -cs) stable" > /etc/apt/sources.list.d/docker.list

apt-get update
#apt-get install -y docker-ce:$ARCH docker-ce-cli:$ARCH containerd.io:$ARCH docker-compose-plugin:$ARCH
# docker-ce 29 docker root don't work.
# containerd 2.2.1 can't download, pin to 2.2.0
apt-get install -y --allow-downgrades docker-ce=5:28.5.2-1~ubuntu.24.04~noble docker-ce-cli=5:28.5.2-1~ubuntu.24.04~noble containerd.io=2.2.0-2~ubuntu.24.04~noble docker-compose-plugin:$ARCH

if [ "$ARCH" == "amd64" ]; then
  # On amd64, reduce the number of random bits for ASLR to 28 from 32. This was
  # a change done in recent kernels to increase security, but this breaks older
  # versions of ASAN that assumed that ASLR is only randomizing library load
  # addresses within a certain range. This can be removed when we are using
  # newer versions of ASAN that are bundled with GCC (Trixie and newer? Unsure
  # on this at this time).
  #
  # See also https://stackoverflow.com/a/78302537 and https://stackoverflow.com/a/77895910
  echo "vm.mmap_rnd_bits = 28" > /etc/sysctl.d/99-sonic-mmap-rnd-bits.conf
  sysctl -p /etc/sysctl.d/99-sonic-mmap-rnd-bits.conf
fi

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
usermod -a -G docker azureuser 2>&1 >> /var/log/agent-provision.log || true
cat /etc/passwd /etc/group >> /var/log/agent-provision.log || true

# Install build tools (and waiting docker ready)
curl -sSL -O https://packages.microsoft.com/config/$(source /etc/os-release && echo "$ID/$VERSION_ID")/packages-microsoft-prod.deb
dpkg -i packages-microsoft-prod.deb
rm packages-microsoft-prod.deb
apt-get update
echo "intnfs.file.core.windows.net:/intnfs/nfs /nfs aznfs noauto,x-systemd.automount,_netdev,vers=4.1,sec=sys,nconnect=4 0 0" >> /etc/fstab
apt-get install -y build-essential aznfs nfs-common python3-setuptools python-is-python3
# Some job need pwsh, Job Validation/Release Task Validation
if [ "$ARCH" == "amd64" ]; then
  apt-get install -y powershell
else
  mkdir -p /opt/microsoft/powershell/7
  wget https://github.com/PowerShell/PowerShell/releases/download/v7.5.10/powershell-7.5.10-linux-arm64.tar.gz
  tar -xzf powershell-7.5.10-linux-arm64.tar.gz -C /opt/microsoft/powershell/7
  ln -s /opt/microsoft/powershell/7/pwsh /usr/bin/pwsh
  chmod +x /opt/microsoft/powershell/7/pwsh
  pwsh -v
fi
curl -LsSf https://astral.sh/uv/0.13.0/install.sh | env UV_UNMANAGED_INSTALL=/usr/local/bin sh
UV_TOOL_DIR=/opt/uv/tools UV_TOOL_BIN_DIR=/usr/local/bin uv tool install jinjanator
mkdir -p /nfs

# Validate dependencies using the agent account and its sudo environment.
if ! sudo -u azureuser -- bash -e <<'EOF'
for tool in docker git-lfs make python3 pwsh jinjanate; do
  if ! command -v "$tool" >/dev/null; then
    echo "ERROR: Missing agent dependency: $tool" >&2
    exit 1
  fi
done

sudo -n uv --version
jinjanate --version
EOF
then
  echo "ERROR: Agent dependency validation failed" >&2
  exit 1
fi
