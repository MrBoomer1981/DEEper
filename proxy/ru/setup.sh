#!/bin/sh
# Sets up DEEper's address in Russia on a fresh Ubuntu or Debian server: Caddy with HTTPS that passes every
# request on to the app (see proxy/CLAUDE.md). As root, from a copy of this folder on the server:
#
#   sh setup.sh <the server's IP address or domain> "<the app's Railway address> <the Worker's address>"
#
# Run it again to change the addresses or to install a changed Caddyfile. The addresses go into
# /etc/caddy/deeper.env, not into this public repository.
set -eu
cd "$(dirname "$0")"
SITE=${1:?the server IP address or domain}
UPSTREAMS=${2:?the app addresses, Railway first}
export DEBIAN_FRONTEND=noninteractive

# Caddy from its own repository: the distributions' packages are too old for IP address certificates (2.10.2+).
if ! command -v caddy >/dev/null; then
	apt-get update -q
	apt-get install -yq debian-keyring debian-archive-keyring apt-transport-https curl gpg
	curl -1sLf https://dl.cloudsmith.io/public/caddy/stable/gpg.key |
		gpg --dearmor --yes -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
	curl -1sLf https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt > /etc/apt/sources.list.d/caddy-stable.list
	apt-get update -q
	apt-get install -yq caddy
fi

# Security updates, Caddy's too, install themselves.
apt-get install -yq unattended-upgrades
printf 'APT::Periodic::Update-Package-Lists "1";\nAPT::Periodic::Unattended-Upgrade "1";\n' > /etc/apt/apt.conf.d/20auto-upgrades
origin=$(apt-cache policy | grep -A 1 'cloudsmith.io/public/caddy' | sed -n 's/.*[ ,]o=\([^,]*\).*/\1/p' | head -n 1)
[ -n "$origin" ] && printf 'Unattended-Upgrade::Origins-Pattern:: "origin=%s";\n' "$origin" > /etc/apt/apt.conf.d/51deeper-caddy

printf 'SITE="%s"\nUPSTREAMS="%s"\n' "$SITE" "$UPSTREAMS" > /etc/caddy/deeper.env
mkdir -p /etc/systemd/system/caddy.service.d
printf '[Service]\nEnvironmentFile=/etc/caddy/deeper.env\n' > /etc/systemd/system/caddy.service.d/deeper.conf
install -m 644 Caddyfile /etc/caddy/Caddyfile
(set -a; . /etc/caddy/deeper.env; caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile >/dev/null 2>&1) ||
	{ echo "The Caddyfile does not validate: caddy validate --config /etc/caddy/Caddyfile" >&2; exit 1; }

if command -v ufw >/dev/null && ufw status | grep -q 'Status: active'; then
	ufw allow 80/tcp && ufw allow 443/tcp && ufw allow 443/udp
fi

# Sign-in by key only, once a key is there: password guessing on root never stops on a public address.
if [ -s /root/.ssh/authorized_keys ]; then
	printf 'PasswordAuthentication no\nKbdInteractiveAuthentication no\n' > /etc/ssh/sshd_config.d/10-deeper-keys-only.conf
	systemctl reload ssh 2>/dev/null || systemctl reload sshd 2>/dev/null || true  # socket-activated sshd reads it anyway
fi

systemctl daemon-reload
systemctl enable -q caddy
systemctl restart caddy

# The first certificate takes a few seconds to a minute.
for _ in $(seq 30); do
	if curl -fsS -o /dev/null --max-time 5 --connect-to ::127.0.0.1: "https://$SITE/static/manifest.webmanifest" 2>/dev/null; then
		echo "Ready: https://$SITE"
		exit 0
	fi
	sleep 3
done
echo "No answer over HTTPS yet; see journalctl -u caddy" >&2
exit 1
