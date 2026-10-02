# Persistent Debian and Incus infrastructure

Boot two independently selectable systemd environments under a platform whose
PID1 is Tini. Outer runit supervises Debian, Incus, SSHD and Tailscale. Each
systemd guest manages its native units; Incus manages its own instances.

The directly launched Debian environment is a **demo** showing how to run a
container directly with systemd-nspawn. For regular use, **use Incus to create
and run containers**: its CLI makes creating, starting, stopping, cloning and
snapshotting multiple containers more convenient. You can disable the Debian
demo and run only the Incus environment; both options are independent.

This repository provides reusable source with generic example paths and an
`operator` account. It contains no deployed root filesystems, credentials or
runtime state. See [publication and privacy](docs/PRIVACY.md).

| Persistent installation | Contents |
| --- | --- |
| `/srv/container-infrastructure/debian-rootfs` | Demo native Debian OS, packages, accounts, homes and journals |
| `/srv/container-infrastructure/debian-runtime` | Debian launch configuration, controls and lifecycle state |
| `/srv/container-infrastructure/incus-rootfs` | Native Debian Incus manager OS, APT database, Incus database, certificates, images, instances and snapshots |
| `/srv/container-infrastructure/incus-runtime` | Incus manager launch configuration, controls and lifecycle state |
| `/srv/container-infrastructure/infrastructure-rootfs` | Native APT runit/OpenSSH/Tailscale tools and outer access identities; not a booted guest |
| `/srv/container-infrastructure/infrastructure-runtime` | Outer supervision, four service definitions, controls and private logs |
| `/srv/container-infrastructure/source` | Reusable source and documentation; no live installation or secrets |

Installations are native packages inside their dedicated persistent Debian
filesystems. Bootstrap starts existing installations; it never rebuilds them,
initializes Incus again, or overrides a deliberate disable.

```sh
export PATH=/srv/container-infrastructure/infrastructure-runtime/bin:$PATH
infractl status
infractl enable debian
infractl enable incus
incus list
sv status debian incus sshd tailscaled
# Persistently disable either environment independently:
infractl disable debian
infractl disable incus
```

For all four enable/disable combinations, restart behavior and configuration
paths, see [environment configuration](docs/CONFIGURATION.md). SSHD and
Tailscale remain independent of the two environments.

Register this exact command in the platform's external five-minute schedule:

```sh
/bin/sh /srv/container-infrastructure/infrastructure-runtime/startup.sh --trigger scheduled
```

The old `/srv/container-infrastructure/debian-runtime/startup.sh` forwards to the new supervisor
when installed, preserving an existing trigger. Do not register a second
independent supervisor. Runit restarts exited services immediately; the external
trigger recovers a missing scanner within the schedule interval.

Read [from-scratch setup](docs/FROM-SCRATCH.md), [operations](docs/OPERATIONS.md),
[architecture](docs/ARCHITECTURE.md), and [build evidence](docs/BUILD-RECORD.md).
The [direct-container notes](docs/SETUP-RECORD.md) explain the demo approach.

The default configuration uses `dir` storage: snapshots and clones copy files. It has no
resource controller delegation or kernel AppArmor support. The outer services
and management guest are trusted administrators of the shared kernel/network;
unprivileged Incus instances receive separate namespaces and mapped UIDs.
Platform recreation must preserve the rootfs/runtime trees with their metadata.
Local service restart tests cannot prove the platform's preservation contract
or external schedule registration.
