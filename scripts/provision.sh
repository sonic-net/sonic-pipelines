#!/bin/bash

set -exo pipefail

ARCH=${1:-}

function ensure_data_mount()
{
  local source=$1 target=$2 filesystem=$3 options=$4 pass=$5 uuid=$6 fsroot=$7
  local entry actual_uuid actual_root contents
  entry=$(awk -v target="$target" '$1 !~ /^#/ && $2 == target {print $1, $3, $4, $5, $6}' /etc/fstab)
  if [ -n "$entry" ] && [ "$entry" != "$source $filesystem $options 0 $pass" ]; then
    echo "ERROR: Conflicting fstab entry for $target" >&2
    exit 1
  fi
  if [ -L "$target" ] || { [ -e "$target" ] && [ ! -d "$target" ]; }; then
    echo "ERROR: Mount target must be a real directory: $target" >&2
    exit 1
  fi
  if ! mountpoint -q "$target"; then
    if [ -d "$target" ]; then
      contents=$(find "$target" -mindepth 1 -maxdepth 1 -print -quit)
      if [ -n "$contents" ]; then
        echo "ERROR: Refusing to hide existing files at $target" >&2
        exit 1
      fi
    fi
    mkdir -p "$target"
  fi
  if [ -z "$entry" ]; then
    printf '\n%s %s %s %s 0 %s\n' "$source" "$target" "$filesystem" "$options" "$pass" >> /etc/fstab
  fi
  systemctl daemon-reload
  if ! mountpoint -q "$target"; then
    mount "$target"
  fi
  actual_uuid=$(findmnt -rn -M "$target" -o UUID)
  actual_root=$(findmnt -rn -M "$target" -o FSROOT)
  if [ "$actual_uuid" != "$uuid" ] || [ "$actual_root" != "$fsroot" ]; then
    echo "ERROR: Unexpected filesystem mounted at $target" >&2
    exit 1
  fi
}

function configure_data_disk()
{
  local disk=/dev/disk/azure/scsi1/lun0
  local disk_type disk_devices disk_id signatures label uuid mounts foreign_mounts
  local new_disk=0 active_agents
  local os_record=/var/lib/sonic-pipelines/data-disk-uuid
  if [ ! -b "$disk" ]; then
    echo "ERROR: Azure data disk is missing: $disk" >&2
    exit 1
  fi
  disk=$(readlink -f -- "$disk")
  disk_type=$(lsblk -dnro TYPE "$disk")
  disk_devices=$(lsblk -nrpo NAME "$disk")
  disk_id=$(lsblk -dnro MAJ:MIN "$disk")
  signatures=$(wipefs --no-act --noheadings --output TYPE "$disk")
  if [ "$disk_type" != "disk" ] || [ "$disk_devices" != "$disk" ] || [ -z "$disk_id" ]; then
    echo "ERROR: Select a whole data disk without partitions" >&2
    exit 1
  fi
  mounts=$(findmnt -rn -o MAJ:MIN,TARGET)
  foreign_mounts=$(awk -v disk="$disk_id" \
    '$1 == disk && $2 != "/data" && $2 != "/agent/_work" {print $2}' <<< "$mounts")
  if [ -n "$foreign_mounts" ]; then
    echo "ERROR: Selected disk is mounted outside the managed data paths" >&2
    exit 1
  fi
  if [ -z "$signatures" ]; then
    if [ -n "$(awk -v disk="$disk_id" '$1 == disk {print $2}' <<< "$mounts")" ]; then
      echo "ERROR: Refusing to format a mounted disk" >&2
      exit 1
    fi
    # The filesystem label identifies disks initialized by this script, even after reimage.
    mkfs.ext4 -L sonic-ci-data "$disk"
    new_disk=1
  elif [ "$signatures" = "ext4" ]; then
    label=$(lsblk -dnro LABEL "$disk")
    if [ "$label" != "sonic-ci-data" ]; then
      echo "ERROR: Existing filesystem was not initialized by this script" >&2
      exit 1
    fi
  else
    echo "ERROR: Refusing a data disk with unknown filesystem or partition signatures" >&2
    exit 1
  fi
  uuid=$(blkid -s UUID -o value "$disk")
  if [ -z "$uuid" ]; then
    echo "ERROR: Data filesystem has no UUID" >&2
    exit 1
  fi
  if [ -e "$os_record" ] && [ "$(cat "$os_record")" != "$uuid" ]; then
    echo "ERROR: This OS was provisioned with a different data disk" >&2
    exit 1
  fi
  ensure_data_mount "UUID=$uuid" /data ext4 defaults 2 "$uuid" /
  mkdir -p /data/agent-work
  ensure_data_mount /data/agent-work /agent/_work none \
    bind,x-systemd.requires-mounts-for=/data 0 "$uuid" /agent-work

  # The OS-side record survives retries, but not an OS reimage.
  if [ "$new_disk" -eq 0 ] && [ ! -e "$os_record" ]; then
    if systemctl is-active --quiet docker.service docker.socket containerd.service; then
      echo "ERROR: Stop Docker and containerd before resetting reimaged agent data" >&2
      exit 1
    fi
    active_agents=$(systemctl list-units --type=service --state=active --no-legend --plain 'vsts.agent.*')
    if [ -n "$active_agents" ]; then
      echo "ERROR: Refusing to reset data while an agent service is active" >&2
      exit 1
    fi
    mounts=$(findmnt -rn -o TARGET)
    if grep -Eq '^/data/|^/agent/_work/' <<< "$mounts"; then
      echo "ERROR: Unmount nested job/container mounts before resetting data" >&2
      exit 1
    fi
    echo "Resetting workspace, Docker and containerd data after OS reimage"
    umount /agent/_work
    rm -rf -- /data/agent-work /data/docker /data/containerd
    mkdir -p /data/agent-work
    mount /agent/_work
  fi
  mkdir -p /data/docker /data/containerd
  mkdir -p /etc/systemd/system/docker.service.d /etc/systemd/system/containerd.service.d
  printf '[Unit]\nRequiresMountsFor=/data\n' \
    > /etc/systemd/system/docker.service.d/data-mount.conf
  cat > /etc/systemd/system/containerd.service.d/data-root.conf <<'EOF'
[Unit]
RequiresMountsFor=/data
[Service]
ExecStart=
ExecStart=/usr/bin/containerd --root /data/containerd
EOF
  mkdir -p /var/lib/sonic-pipelines
  printf '%s\n' "$uuid" > "$os_record.tmp"
  mv -f -- "$os_record.tmp" "$os_record"
}

configure_data_disk

export DEBIAN_FRONTEND=noninteractive
export NEEDRESTART_MODE=a
export NEEDRESTART_SUSPEND=1

apt-get -y purge --auto-remove needrestart || true
apt-mark hold needrestart || true
 
mkdir -p /etc/needrestart/conf.d
cat > /etc/needrestart/conf.d/99-no-prompt.conf <<'EOF'
$nrconf{restart} = 'a';
$nrconf{kernelhints} = 0;
$nrconf{ucodehints} = 0;
EOF

DEFAULT_ARCH=$(dpkg --print-architecture)
[ -z "$ARCH" ] && [ -f /etc/docker-arch ] && ARCH=$(cat /etc/docker-arch)
[ -z "$ARCH" ] && ARCH=$DEFAULT_ARCH

apt-get update
apt-get -y purge needrestart || true
NEEDRESTART_MODE=l DEBIAN_FRONTEND=noninteractive apt-get -o Dpkg::Options::="--force-confdef" -o Dpkg::Options::="--force-confold" -y dist-upgrade
apt-get install -y ca-certificates curl gnupg lsb-release

apt-get update
apt-get install -y acl

# install git lfs
curl -s https://packagecloud.io/install/repositories/github/git-lfs/script.deb.sh | bash
apt-get update
apt-get install -y git-lfs

if [ "$ARCH" == "armhf" ] && [ "$ARCH" != "$DEFAULT_ARCH" ]; then
  dpkg --add-architecture armhf
fi
 
mkdir -p /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg | gpg --dearmor -o /etc/apt/keyrings/docker.gpg --batch --yes
echo "deb [arch=$ARCH signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/ubuntu $(lsb_release -cs) stable" > /etc/apt/sources.list.d/docker.list

apt-get update
apt-get install -y docker-ce:$ARCH docker-ce-cli:$ARCH containerd.io:$ARCH docker-compose-plugin:$ARCH

systemctl daemon-reload
systemctl stop docker.service docker.socket
systemctl restart containerd.service

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
  echo "ExecStart=/usr/bin/setarch linux32 -B /usr/bin/containerd --root /data/containerd" >> /etc/systemd/system/containerd.service.d/override.conf

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
  sed -i '/^ExecStart=/ { s| --data-root[= ][^ ]*||g; s|$| --data-root /data/docker|; }' /lib/systemd/system/docker.service
  systemctl daemon-reload
  systemctl start docker
fi
usermod -a -G docker azureuser || true
cat /etc/passwd /etc/group || true

# Install build tools (and waiting docker ready)
apt-get install -y build-essential nfs-common python3-setuptools python-is-python3

# Install uv (system-wide) and use it to install Python packages without pip
curl -LsSf https://astral.sh/uv/install.sh | env UV_INSTALL_DIR=/usr/local/bin sh
uv pip install --system jinja2 j2cli markupsafe

# Validate dependencies using the agent account and its sudo environment.
if ! sudo -u azureuser -- bash -e <<'EOF'
for tool in docker git-lfs make python3; do
  if ! command -v "$tool" >/dev/null; then
    echo "ERROR: Missing agent dependency: $tool" >&2
    exit 1
  fi
done

sudo -n uv --version
EOF
then
  echo "ERROR: Agent dependency validation failed" >&2
  exit 1
fi

if [ -f /var/run/reboot-required ]; then
   echo "Kernel/libs upgraded — rebooting"
   systemctl reboot
 fi
