#!/bin/bash
set -euo pipefail
export PATH=/usr/sbin:/usr/bin:/sbin:/bin
export DEBIAN_FRONTEND=noninteractive
if [ "$(id -u)" != 0 ]; then exec sudo -n /bin/bash "$0" "$@"; fi
ROOTFS=${1:-/srv/container-infrastructure/debian-rootfs}
case "$ROOTFS" in /*) ;; *) echo "Rootfs must be an absolute path" >&2; exit 2;; esac
PROJECT=$(cd -- "$(dirname -- "$0")/.." && pwd)
case "$ROOTFS" in "$PROJECT"|"$PROJECT"/*) echo "Keep rootfs outside the source project" >&2; exit 2;; esac
case "$ROOTFS" in /|/usr|/etc|/home|/srv/container-infrastructure) echo 'Choose a dedicated rootfs directory' >&2; exit 2;; esac
[ ! -L "$ROOTFS" ] || { echo 'Rootfs must not be a symlink' >&2; exit 2; }
if [ -f "$ROOTFS/etc/debian-container-provisioned" ]; then echo "Already provisioned: $ROOTFS"; exit 0; fi
if [ -e "$ROOTFS" ] && [ -n "$(ls -A "$ROOTFS")" ]; then echo 'Rootfs is nonempty and not provisioned; inspect it before retrying' >&2; exit 2; fi
command -v debootstrap >/dev/null || { echo 'Host prerequisite: apt-get install --no-install-recommends debootstrap gpgv' >&2; exit 1; }
mkdir -p "$ROOTFS"
debootstrap --arch=amd64 --variant=minbase --force-check-sig --include=systemd-sysv,systemd-container,dbus,libpam-systemd,sudo,ca-certificates,iproute2,curl,python3,procps,gpgv trixie "$ROOTFS" https://deb.debian.org/debian
cat > "$ROOTFS/usr/sbin/policy-rc.d" <<'EOF'
#!/bin/sh
exit 101
EOF
chmod 755 "$ROOTFS/usr/sbin/policy-rc.d"
cat > "$ROOTFS/etc/apt/sources.list" <<'EOF'
deb https://deb.debian.org/debian trixie main
deb https://deb.debian.org/debian trixie-updates main
deb https://deb.debian.org/debian-security trixie-security main
EOF
cp /etc/resolv.conf "$ROOTFS/etc/resolv.conf"
chroot "$ROOTFS" /usr/bin/apt-get update
chroot "$ROOTFS" /usr/bin/apt-get -y --no-install-recommends upgrade
# Account is local to the guest. Do not import host password hashes or keys.
chroot "$ROOTFS" /usr/sbin/useradd --create-home --uid 1000 --shell /bin/bash --groups sudo operator
printf 'operator ALL=(ALL:ALL) NOPASSWD: ALL\n' > "$ROOTFS/etc/sudoers.d/operator"
chmod 440 "$ROOTFS/etc/sudoers.d/operator"
printf 'debian-local\n' > "$ROOTFS/etc/hostname"
printf '127.0.0.1 localhost\n127.0.1.1 debian-local\n::1 localhost ip6-localhost ip6-loopback\n' > "$ROOTFS/etc/hosts"
mkdir -p "$ROOTFS/etc/systemd/journald.conf.d" "$ROOTFS/var/log/journal"
cat > "$ROOTFS/etc/systemd/journald.conf.d/persistent.conf" <<'EOF'
[Journal]
Storage=persistent
SystemMaxUse=128M
EOF
# Shared host networking is managed by the outer container. Preserve its routes and DNS.
for unit in systemd-networkd.service systemd-networkd.socket systemd-resolved.service systemd-udevd.service systemd-udevd-control.socket systemd-udevd-kernel.socket console-getty.service; do
 chroot "$ROOTFS" /usr/bin/systemctl --root=/ mask "$unit"
done
chroot "$ROOTFS" /usr/bin/systemctl --root=/ set-default multi-user.target
# Debootstrap's autostart guard is unnecessary once the guest is genuinely booted.
python3 - "$ROOTFS" <<'PY'
from pathlib import Path
import sys
root=Path(sys.argv[1]);(root/'usr/sbin/policy-rc.d').unlink()
(root/'etc/machine-id').write_text('')
(root/'etc/debian-container-provisioned').write_text('Debian trixie amd64 with nested systemd; provisioned by debian-container\n')
PY
chroot "$ROOTFS" /usr/bin/apt-get clean
printf 'Provisioned %s\n' "$ROOTFS"
