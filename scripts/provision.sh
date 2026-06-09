#!/bin/bash

set -ex
echo "start"
ARCH=$1
DEFAULT_ARCH=$(dpkg --print-architecture)
[ -z "$ARCH" ] && [ -f /etc/docker-arch ] && ARCH=$(cat /etc/docker-arch)
[ -z "$ARCH" ] && ARCH=$DEFAULT_ARCH

start_health_stub() {
  cat >/usr/local/bin/health-stub.py <<'PYEOF'
import http.server, socketserver
class H(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path.startswith('/health'):
            self.send_response(200); self.end_headers(); self.wfile.write(b'OK\n')
        else:
            self.send_response(404); self.end_headers()
    def log_message(self, *a, **k): pass
socketserver.TCPServer.allow_reuse_address = True
with socketserver.TCPServer(('127.0.0.1', 8080), H) as s:
    s.serve_forever()
PYEOF
  cat >/etc/systemd/system/health-stub.service <<'UEOF'
[Unit]
Description=Provisioning health stub on :8080/health
After=network.target
[Service]
ExecStart=/usr/bin/python3 /usr/local/bin/health-stub.py
Restart=always
RestartSec=2
[Install]
WantedBy=multi-user.target
UEOF
  systemctl daemon-reload
  systemctl enable --now health-stub.service
  for i in 1 2 3 4 5 6 7 8 9 10; do
    curl -fsS http://127.0.0.1:8080/health && break
    sleep 1
  done
}
start_health_stub
 
echo "Waiting for cloud-init to finish..."
cloud-init status --wait || true

wait_apt() {
  local i=0
  while fuser /var/lib/dpkg/lock-frontend \
              /var/lib/dpkg/lock \
              /var/lib/apt/lists/lock >/dev/null 2>&1; do
    if [ $((i % 15)) -eq 0 ]; then
      echo "Waiting for apt/dpkg locks... ${i}s elapsed"
    fi
    sleep 5
    i=$((i+5))
    [ $i -ge 600 ] && { echo "apt locked >10min, giving up"; return 1; }
  done
}

wait_apt
apt-get update
wait_apt
NEEDRESTART_MODE=l DEBIAN_FRONTEND=noninteractive apt-get -o Dpkg::Options::="--force-confdef" -o Dpkg::Options::="--force-confold" -y upgrade
wait_apt
apt-get install -y ca-certificates curl gnupg lsb-release

wait_apt
apt-get update
wait_apt
apt-get install -y acl || sleep 3000

# install git lfs
curl -s https://packagecloud.io/install/repositories/github/git-lfs/script.deb.sh | bash
wait_apt
apt-get update
wait_apt
apt-get install -y git-lfs

if [ "$ARCH" == "armhf" ] && [ "$ARCH" != "$DEFAULT_ARCH" ]; then
  dpkg --add-architecture armhf
fi
 
mkdir -p /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg | gpg --dearmor -o /etc/apt/keyrings/docker.gpg --batch --yes
echo "deb [arch=$ARCH signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/ubuntu $(lsb_release -cs) stable" > /etc/apt/sources.list.d/docker.list

apt-get update
wait_apt
apt-get install -y docker-ce:$ARCH docker-ce-cli:$ARCH containerd.io:$ARCH docker-compose-plugin:$ARCH

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
wait_apt
apt-get install -y build-essential nfs-common python3-pip python3-setuptools python3-pip python-is-python3
pip3 install jinja2 j2cli markupsafe
