# Build from scratch

For selecting both, either or neither environment and changing launch settings,
see [environment configuration](CONFIGURATION.md). Enable/disable policy is
saved through infractl rather than launch-config TOML switches.

This guide installs the two-option infrastructure with generic defaults.
The direct Debian guest is a demo; Incus is recommended for ordinary container
creation and management. Source scripts contain the role/package/unit settings.
Use dedicated persistent storage permitted by your host installation policy.
Keep credentials and runtime trees outside Git; do not publish live data.

## Prerequisites and topology

The tested outer platform is Debian 13 amd64, Tini PID1, Python3.13, root or
sudo -n, util-linux mount/nsenter/ip, and writable cgroup v2 child directories.
It permits mount/PID/user/network/cgroup namespaces, CAP_SYS_ADMIN/NET_ADMIN,
BPF/PERFMON and /dev/fuse. Kernel AppArmor and resource controller delegation
are absent. Kernel modules cannot be loaded. This recipe does not replace
platform PID1, change parent controllers, format disks or install ZFS/Btrfs.
The default bridge subnet 10.88.0.0/24 must not overlap existing routes.

Keep source in a writable project directory. The three rootfs and three runtime
paths described in README must be dedicated persistent directories outside Git. Provisioning
refuses unrelated nonempty destinations. Do not rerun bootstrap provisioning
against a partially built nonempty tree without investigating its contents.
Native APT internals remain in each filesystem, including /var/lib/dpkg.

## Provision native package roles

Read any host installation policy first. Record already installed outer
provisioning packages before introducing transient tools. Clone this repository
into a writable project directory; no live data belongs in that checkout.
Run the commands below from that checkout. The scripts locate source relative
to their own files. Installed paths under /srv/container-infrastructure need
root/sudo and preserved storage; these defaults are fixed across the installers
and helpers. If changing that base, update source/template paths consistently
before installation.

```sh
# Run from the repository checkout.
sudo apt-get update
sudo apt-get install --no-install-recommends debootstrap gpgv
# Skip this first provision step if the existing guest is already provisioned.
sudo scripts/provision.sh /srv/container-infrastructure/debian-rootfs
sudo scripts/install-controls.sh
sudo scripts/provision-infrastructure.sh host-tools /srv/container-infrastructure/infrastructure-rootfs
sudo scripts/provision-infrastructure.sh incus /srv/container-infrastructure/incus-rootfs
sudo scripts/install-infrastructure.py
```

The base uses debootstrap --force-check-sig against the Debian archive keyring,
then signed trixie/trixie-updates/trixie-security repositories. Each role uses
native apt-get, with policy-rc.d blocking package autostart during provisioning.
The host-tools role adds runit, openssh-server, nftables and Tailscale from the
official scoped signed repository. The Incus role adds incus-base, nftables,
dnsmasq-base and iptables (including their native dependency closure).
Debian's Incus package provides LXC, uidmap and rsync. Both trees have their
own minimal Debian base, locked operator UID1000, sudo, Python and systemd tools.
The tools tree is not itself booted as a systemd environment.

Provisioning sets one nonoverlapping root subuid/subgid range in the manager:
root:1000000:16777216. Do not append it over Debian's existing root range.
The explicit trusted manager configuration is installed on its first deployment:

```toml
rootfs = "/srv/container-infrastructure/incus-rootfs"
machine = "incus-manager"
state_dir = "/srv/container-infrastructure/incus-runtime/state"
private_users = "no"
allow_tun = false
allow_nesting = true
```

The installer preserves existing configuration and service down markers. It
installs a native Incus service override and the root-owned nesting/firewall
helpers. Fresh proc/sys mounts and owned cgroup directory traversal allow
unprivileged nested instances. /proc/sys/net and /sys/class/net are writable
only in the trusted manager namespace for its bridge administration. A helper
adds two scoped legacy FORWARD exceptions when this bridge is configured;
ExecStopPost removes those exact rules. The platform Docker policy remains.

## Optional access identity

Skip this section for an Incus-only installation without outer access services.
SSHD and Tailscale are initially disabled. Set up or migrate access only when
the operator requests it and supplies the required public key/authentication.

If migrating an existing Debian guest with native SSHD/Tailscale access, first
review [optional migration](MIGRATION.md), match its account and paths, and
preserve its identity with:

```sh
sudo /srv/container-infrastructure/debian-runtime/bin/debianctl start
sudo /srv/container-infrastructure/infrastructure-runtime/bin/infractl start
sudo scripts/migrate-access.py
```

The migration copies the existing ED25519 host key, public authorized keys and
Tailscale state to the tools tree, disables guest access units and enables the
outer pair. It validates actual API/readiness and rolls back guest access on
failure. --retry is only for inspecting/retrying a previously rolled-back
migration. A sentinel prevents stale state from being recopied on later runs.
Root-private migration backup remains until verification/intentional cleanup.

For a truly fresh installation without existing guest access services, do not
run that migration. Native openssh-server generates fresh host keys. Install
your public key file at infrastructure-rootfs/etc/ssh/authorized_keys/operator with
root owner, directory0755 and file0644; private host keys0600. Verify its
content before enabling access. The installed SSH config listens on IPv4
0.0.0.0:22, permits operator public keys and disables password/root login. If another
service owns port22 or SOCKS1055, resolve that ownership before enabling these.

```sh
sudo install -d -m 755 /srv/container-infrastructure/infrastructure-rootfs/etc/ssh/authorized_keys
sudo install -m 644 /path/to/your-public-key.pub /srv/container-infrastructure/infrastructure-rootfs/etc/ssh/authorized_keys/operator
sudo /srv/container-infrastructure/infrastructure-runtime/bin/infractl start
sudo /srv/container-infrastructure/infrastructure-runtime/bin/infractl enable sshd
sudo /srv/container-infrastructure/infrastructure-runtime/bin/infractl enable tailscaled
```

Tailscale starts logged out when no prior identity exists. The baseline helper
rejects unsafe saved route/exit/DNS preferences and applies the approved TUN
settings. Authentication is an explicit operator action and cannot be inferred
from daemon readiness. Keep the baseline flags when logging in; the CLI otherwise
defaults to accepting tailnet DNS:

```sh
/srv/container-infrastructure/infrastructure-runtime/bin/tailscale login --accept-dns=false --accept-routes=false --exit-node=
```

Finish authorization at the displayed URL and check `tailscale status` and a
permitted peer connection. SOCKS binds127.0.0.1:1055. See the README's
[Tailscale setup guide](../README.md#configure-and-enable-tailscale).

## Enable Incus and explicitly initialize storage/network

```sh
# Recommended fresh-install selection: Incus only.
sudo /srv/container-infrastructure/infrastructure-runtime/bin/infractl disable debian
sudo /srv/container-infrastructure/infrastructure-runtime/bin/infractl enable incus
sudo /srv/container-infrastructure/infrastructure-runtime/bin/infractl start
# Retry status until the guest/native API is ready:
sudo /srv/container-infrastructure/infrastructure-runtime/bin/incus list
sudo scripts/initialize-incus.py
# To explore the direct Debian demo, use infractl enable debian instead.
```

The explicit initialization script creates dir pool local and the default root
disk. It checks for a conflicting subnet, then creates incusbr0 with
10.88.0.1/24, IPv4 NAT, IPv6 disabled, DHCP/DNS and default eth0 profile device.
It refuses incompatible existing pool/network/devices instead of overwriting.
Repetition retains the database, identities, instances and snapshots.

An independent detached outer process records the physical default route,
preexisting nftables tables and the exact missing legacy rules, then monitors
the configured HTTPS probe hosts on TCP443, DNS and the Debian endpoint. Failed
qualification or no commit within180seconds removes only the newly created
bridge/owned rules and disables the manager. Its private spec and log remain
in infrastructure-runtime/state for inspection. No global firewall flush,
route/DNS replacement or Docker-chain edits are performed. Bootstrap never
calls this initializer. The default probe host is deb.debian.org. Add private
management endpoints only through the local environment, never in Git:

```sh
sudo env INFRA_PROBE_HOSTS="deb.debian.org,management.example.invalid" python3 scripts/initialize-incus.py
```

Replace the illustrative management hostname with a reachable local endpoint;
every configured host must accept TCP443. The detached guard saves the same
host list in its private specification. A new subnet/interface requires deliberate
configuration changes to both the initializer and firewall helper.

## Deploy source updates and schedule

```sh
sudo scripts/install-controls.sh
sudo scripts/install-infrastructure.py
# Inspect changes before restarting affected services:
sudo /srv/container-infrastructure/infrastructure-runtime/bin/infractl service-restart incus
```

The old Debian startup alias and Tailscale command forward to the outer owner.
Their dispatch checks the public executable, so the operator can use them
despite root-private configuration. Optional ~/.local/bin symlinks for
infractl, sv and incus point directly into infrastructure-runtime/bin; do not
replace an unrelated existing command. SSH login and sudo PATH are configured
inside the tools filesystem.

Register exactly this in the platform's five-minute external schedule:

```sh
/bin/sh /srv/container-infrastructure/infrastructure-runtime/startup.sh --trigger scheduled
```

Both, either or neither environment can be enabled with infractl. Their
persistent down/disabled markers survive updates/restart. Access services are
independent. Repeated triggers ensure one supervisor and do not force disabled
services on. The old Debian startup path is a compatibility forwarder.

## Qualification and cleanup

For the recommended Incus-only setup, first run unit tests and create disposable
instances to check snapshots and clones. These checks do not enable the Debian
demo or outer access services. Choose unused instance names; wait until the
instance's `systemctl is-system-running` reports `running` before snapshotting.

```sh
export PATH=/srv/container-infrastructure/infrastructure-runtime/bin:$PATH
python3 -m unittest discover -s tests -p 'test_*.py'
CHECK_INSTANCE="setup-check-$(date +%s)-$$"
incus launch images:debian/13 "$CHECK_INSTANCE"
incus exec "$CHECK_INSTANCE" -- systemctl is-system-running
incus exec "$CHECK_INSTANCE" -- sh -c 'echo original > /root/setup-check'
incus snapshot create "$CHECK_INSTANCE" baseline
incus exec "$CHECK_INSTANCE" -- sh -c 'echo modified > /root/setup-check'
incus stop "$CHECK_INSTANCE"
incus snapshot restore "$CHECK_INSTANCE" baseline
incus start "$CHECK_INSTANCE"
incus exec "$CHECK_INSTANCE" -- cat /root/setup-check
incus copy "$CHECK_INSTANCE/baseline" "$CHECK_INSTANCE-copy"
incus start "$CHECK_INSTANCE-copy"
incus exec "$CHECK_INSTANCE-copy" -- cat /root/setup-check
# Both cat commands should print original. Remove only these test instances.
incus delete --force "$CHECK_INSTANCE-copy" "$CHECK_INSTANCE"
infractl --trigger qualification startup
infractl status
```

Check that repeated startup leaves one supervisor and keeps the demo/access
services disabled. Register the external schedule separately as described above.

The optional full integration suite temporarily enables **all four services**,
including SSHD/Tailscale, briefly restarts guests/access and intentionally kills
the owned scanner. Use it only when that access setup and disruption are
authorized, on a fresh disposable installation or during maintenance. It restores
the original per-service enable policy and removes its test objects; it never
reboots platform PID1. Prepare its qualification seed explicitly:

```sh
export PATH=/srv/container-infrastructure/infrastructure-runtime/bin:$PATH
incus launch images:debian/13 infra-qualification
incus exec infra-qualification -- useradd -m snapshot-user
incus snapshot create infra-qualification baseline
sudo python3 tests/infrastructure-integration.py
incus delete --force infra-qualification
infractl status
```

Also verify SSH using a temporary authorized key, remove that exact test key,
compare the original host fingerprint, check platform DNS/HTTPS/routes and
confirm the Tailscale baseline. An online peer test requires authentication;
perform it separately after login. Review native package versions with
`debianctl exec dpkg-query` or the corresponding manager command.

Remove only provisioning packages introduced for this build, after inspecting
apt-get -s purge output. Preserve packages that existed before provisioning;
do not assume the host has the same package set as a previous qualification. Native
role APT caches are cleaned; permanent package databases/rootfs are retained.
Remove disposable probes, temporary private keys and source download files.
Keep a useful native Incus base image cache for future containers if desired.

For backup/restore and daily management see OPERATIONS.md. A real platform
reboot/recreation and the external schedule registration have not been verified
here. Preserve all rootfs/runtime trees with numeric owners and metadata;
verify that the chosen storage survives your platform's recreation policy.
A directory on an ephemeral root overlay does not provide that guarantee.
