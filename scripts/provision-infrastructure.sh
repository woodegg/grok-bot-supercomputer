#!/bin/bash
# Native package files stay in each persistent Debian filesystem.
set -euo pipefail
export PATH=/usr/sbin:/usr/bin:/sbin:/bin DEBIAN_FRONTEND=noninteractive
if [ "$(id -u)" != 0 ]; then exec sudo -n /bin/bash "$0" "$@"; fi
PROJECT=$(cd -- "$(dirname -- "$0")/.." && pwd)
ROLE=${1:?Usage: provision-infrastructure.sh host-tools|incus ROOTFS}
ROOTFS=${2:?Specify a dedicated rootfs}
case "$ROLE" in host-tools|incus) ;; *) echo 'Unknown role' >&2; exit 2;; esac
"$PROJECT/scripts/provision.sh" "$ROOTFS"
if [ -f "$ROOTFS/etc/container-infrastructure-$ROLE" ]; then
 echo "Already configured: $ROLE $ROOTFS"; exit 0
fi
printf '#!/bin/sh\nexit 101\n' > "$ROOTFS/usr/sbin/policy-rc.d"
chmod 755 "$ROOTFS/usr/sbin/policy-rc.d"
if [ "$ROLE" = host-tools ]; then
 chroot "$ROOTFS" apt-get -y --no-install-recommends install runit openssh-server nftables
 chroot "$ROOTFS" curl -fsSL https://pkgs.tailscale.com/stable/debian/trixie.noarmor.gpg -o /usr/share/keyrings/tailscale-archive-keyring.gpg
 chroot "$ROOTFS" curl -fsSL https://pkgs.tailscale.com/stable/debian/trixie.tailscale-keyring.list -o /etc/apt/sources.list.d/tailscale.list
 chroot "$ROOTFS" apt-get update
 chroot "$ROOTFS" apt-get -y --no-install-recommends install tailscale
 cp "$PROJECT/templates/sshd-container.conf" "$ROOTFS/etc/ssh/sshd_config.d/container-infrastructure.conf"
 install -D -m 755 "$PROJECT/scripts/tailscale-baseline.py" "$ROOTFS/usr/local/libexec/tailscale-baseline"
 # This tree supplies outer daemons; none of its systemd units boot themselves.
else
 chroot "$ROOTFS" apt-get -y --no-install-recommends install incus-base nftables dnsmasq-base iptables
 # Explicit non-overlapping root ranges for Incus unprivileged instances.
 python3 - "$ROOTFS" <<'PY'
from pathlib import Path
import sys
for name in ('subuid','subgid'):
 p=Path(sys.argv[1])/'etc'/name
 lines=[line for line in p.read_text().splitlines() if not line.startswith('root:')]
 p.write_text('\n'.join(lines+['root:1000000:16777216'])+'\n')
PY
 printf 'incus-manager\n' > "$ROOTFS/etc/hostname"
 chroot "$ROOTFS" systemctl --root=/ enable incus.service
fi
rm "$ROOTFS/usr/sbin/policy-rc.d"
printf 'Native Debian APT role: %s\n' "$ROLE" > "$ROOTFS/etc/container-infrastructure-$ROLE"
chroot "$ROOTFS" apt-get clean
echo "Configured $ROLE at $ROOTFS"
