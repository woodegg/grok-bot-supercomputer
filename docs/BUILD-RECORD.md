# Qualification record

This is a sanitized technical record, not a transcript of a particular host.
Account names, paths and network addresses in this repository are generic
examples. Private installation records, process identifiers, host fingerprints,
management endpoints and original Git history are excluded.

## Architecture qualified

The original qualification exercised two independently selectable nspawn
systemd environments: a direct Debian demo and an Incus manager. Outer runit
supervised their lifetime wrappers plus independent SSHD and Tailscale daemons.
Tini remained platform PID1. Native signed Debian packages supplied runit,
OpenSSH, Incus/LXC and their dependencies; Tailscale used its official signed
repository. Software and state stayed in separate native filesystems/runtimes.

The manager used directory storage and unprivileged instances. ZFS/Btrfs and
resource-controller delegation were unavailable in that environment; no kernel
modules, parent cgroup controllers or platform disk layouts were changed.
Directory snapshots and clones copy files rather than providing copy-on-write.

## Integration observations

The pre-publication installation passed checks covering:

- Independent enable/disable of Debian and Incus, including both and neither.
- Supervisor recovery after an intentionally killed scanner, without duplicate
  owners; persistent disabled markers survived scheduled startup checks.
- Incus manager recovery, native API readiness, and restoration of previously
  running instances while deliberately stopped instances stayed stopped.
- Unprivileged Debian instance creation, networking, native snapshot restoration
  and cloning, including persistence of files and a disposable account.
- Outer SSH login and sudo control access using a temporary authorized key;
  existing access identities were preserved during migration.
- Tailscale daemon readiness with the conservative routing baseline. Readiness
  did not prove authenticated tailnet connectivity.
- DNS, public HTTPS connectivity, unchanged outer default route, and removal of
  only owned bridge forwarding rules on service stop.
- Structured startup/lifecycle events, concurrent writers, bounded rotation and
  secret-safe logging without argument/environment dumps.

Disposable test objects were removed after qualification. Deployment identities,
addresses, exact package versions and timestamps are intentionally omitted.

## Publication changes and limits

Publication source uses /srv/container-infrastructure, the generic operator
account and an example bridge subnet. Network probes accept locally configured
hosts instead of embedding a private management endpoint. Obsolete one-time
home-prefix migration scripts were removed; optional migration from existing
native guest access remains documented in MIGRATION.md.

The anonymized source receives syntax, unit, and isolated deployment checks.
These are not a new from-scratch deployment qualification. The existing live
installation continues using its deployed copies and private settings.

Publication checks passed: 16 unit tests; Python and shell syntax; isolated
installer generation with root ownership, private permissions, configuration
and disabled-state preservation; and mocked access-chroot mount construction.
The source scan found no original deployment identifiers or credential patterns,
and local documentation links resolved. The isolated checks started no daemons.

The README also covers agent-driven installation and scheduler registration,
administration, optional SSH/Tailscale access, the Debian demo and Incus usage.
Documented control syntax matches the source interfaces; shell examples and
documentation links are checked without applying configuration to live services.
Tailscale login examples explicitly retain the routing baseline.

A real platform reboot/recreation and external schedule registration were not
verified by the original integration tests. Rootfs/runtime survival depends on
preserved storage and metadata. The shared-kernel trusted manager requires
substantial privileges; kernel AppArmor and resource controller delegation
must be evaluated separately on each target host.

For reproducible setup and tests see FROM-SCRATCH.md; tests there affect owned
services and should run on a disposable installation or during maintenance.
