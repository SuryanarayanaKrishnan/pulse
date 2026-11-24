#!/usr/bin/env bash
# One-time VM setup. Run on the Oracle VM (Ubuntu 22.04/24.04 aarch64) as the default user:
#   curl -fsSL https://raw.githubusercontent.com/<owner>/pulse/main/deploy/oci/provision.sh | bash   (private repo: scp it instead)
set -euo pipefail
REPO_URL="${REPO_URL:-https://github.com/suryanarayanakrishnan/pulse.git}"

sudo apt-get update -y
sudo apt-get install -y ca-certificates curl git
command -v docker >/dev/null || curl -fsSL https://get.docker.com | sudo sh
sudo usermod -aG docker "$USER"

# Oracle's Ubuntu images ship iptables rules that block everything but SSH, on top of the VCN security list.
sudo iptables -C INPUT -p tcp --dport 80 -j ACCEPT 2>/dev/null || sudo iptables -I INPUT 5 -p tcp --dport 80 -j ACCEPT
sudo iptables -C INPUT -p tcp --dport 443 -j ACCEPT 2>/dev/null || sudo iptables -I INPUT 5 -p tcp --dport 443 -j ACCEPT
sudo apt-get install -y iptables-persistent && sudo netfilter-persistent save

# 2 GB swap so image builds never OOM
if ! swapon --show | grep -q swapfile; then
  sudo fallocate -l 2G /swapfile && sudo chmod 600 /swapfile && sudo mkswap /swapfile && sudo swapon /swapfile
  echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab >/dev/null
fi

sudo mkdir -p /opt/pulse && sudo chown "$USER":"$USER" /opt/pulse
[ -d /opt/pulse/.git ] || git clone "$REPO_URL" /opt/pulse
echo
echo "Next: create /opt/pulse/.env from deploy/oci/env.example (nano, chmod 600), then run deploy/oci/deploy.sh"
echo "(log out and back in first so the docker group applies)"
