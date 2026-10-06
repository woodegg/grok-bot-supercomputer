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

Publication installers use /workspace, the generic operator
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
Clone instructions use a writable project directory. The scripts discover their
source directory independently of root-owned installed-data paths. An authenticated
clone into a writable directory was checked without provisioning software.

A real platform reboot/recreation and external schedule registration were not
verified by the original integration tests. Rootfs/runtime survival depends on
preserved storage and metadata. The shared-kernel trusted manager requires
substantial privileges; kernel AppArmor and resource controller delegation
must be evaluated separately on each target host.

For reproducible setup and tests see FROM-SCRATCH.md; tests there affect owned
services and should run on a disposable installation or during maintenance.

A confidentiality review of the current uncommitted tracked changes and the
untracked command-setup helper found no credentials, key material, private
operator records or deployment-specific endpoints. Review combined source
inspection with credential-pattern checks. Generic paths, account names and
access instructions were retained. This review did not cover Git history,
ignored files or deployed state, and ran no installers or service tests.

## Optional swap verification

A fully allocated, root-private 4 GiB file outside the checkout was tested on
the outer OverlayFS platform. Direct `swapon` failed with `Invalid argument`.
Attaching the same formatted file to a free loop device and enabling swap on
that device succeeded. An isolated process filled 32 MiB of anonymous memory,
requested `MADV_PAGEOUT`, observed 32 MiB of swap usage, and verified the complete
contents after reading them back. The swap was left active as requested.
No services were restarted, parent cgroup controllers changed, or automatic
startup registered. The playbook documents manual creation and recovery.

This establishes activation and a small page-out/read-back operation, not
sustained memory-pressure performance, per-instance Incus swap behavior,
swapoff under pressure, or survival across platform recreation. Preservation
of the backing file and safe reattachment remain host responsibilities.

Zswap was subsequently enabled through its existing runtime interface without
loading modules or changing its `lzo`/`zbud`/20-percent defaults. An isolated
32 MiB anonymous-memory test used nonuniform repeated page contents, requested
`MADV_PAGEOUT`, and verified every page after read-back. Cgroup statistics
reported 33,554,432 bytes of logical zswapped data and 2,457,600 bytes of zswap
memory during the test. Compression was left enabled. This synthetic test
does not establish workload compression ratios or sustained performance;
automatic reactivation and host recreation remain unverified.

The README now explains the project's purpose on Grok Bot Computer and practical
uses of its existing container, service, snapshot, recovery, access and swap
features. Documentation was checked against the playbook and current qualification
limits, with `git diff --check` passing. No installation or service changes were
made for this documentation update.

The README was subsequently reorganized around purpose, storage/recovery,
installation, administration, Incus, optional access and swap. Repeated
explanations were consolidated and the outdated instruction to adapt defaults
already set to `/workspace` was removed. Local file links and the syntax of
all 17 shell examples passed checks, along with `git diff --check`. Examples
were parsed without execution; no deployment or service state changed.

## Initial release qualification

For v0.1.0, all 16 unit tests passed, Python source parsed successfully, and
shell scripts passed `bash -n`. Release preparation made no installation or
service changes. Disruptive integration tests were not rerun against active
work; earlier integration evidence and its limits remain as documented above.

## Box-owned image qualification

A cleanly unmounted 128 MiB ext4 image owned by box survived an observed platform
recreation with an identical whole-image SHA256, internal root-owned directory
0700 and file0600. An external box-owned control retained its content; an external
root-owned mode0600 control disappeared. Kernel boot and PID1 observations changed.
The sparse image's allocated size grew from approximately 4.5 MiB to its full
capacity after restoration. These observations do not establish platform rules.

A fresh native Debian/Incus installation was subsequently built inside eight
box-owned system/runtime/data/pool images, totaling 6 GiB capacity. Incus was
enabled; the demo and both access services remained disabled. Native Debian
security updates and Tailscale packages used signature-verified repositories.
The host's failing Debian APT HTTPS transport required the official HTTP mirror;
signature/package verification remained enabled. Incus version was 6.0.4.

Two unprivileged Alpine sandboxes shared one read-only base, with distinct ext4
delta/home images. Writes, home data and package installation remained isolated.
Both upper/work directories resided on each delta filesystem. Root ID mapping
was verified as 0 -> 1000000 with range16777216; OverlayFS used userxattr.
BusyBox guests required their halt/reboot signals rather than systemd defaults;
graceful stop/restart passed after that correction.

A second base marker and fresh delta demonstrated root replacement with retained
home, and rollback restored the original upper and installed package. Both bases
used the same Alpine release; this was not a distribution upgrade qualification.
The normal Debian instance/snapshot/restore/clone checks passed separately.
The wrapper refused misleading native snapshot/clone operations for layered roots.

All 16 existing unit tests and six real disposable ext4 mount tests passed.
The latter checked idempotent writes, missing image preflight, UUID mismatch,
failure rollback, changed base checksums and read-only enforcement in a private mount namespace.
The full four-service integration suite was not run. Whole-image shutdown and
unmount completed; all 15 ext4 filesystem checks were clean. Remount/bootstrap
retained supervisor disable, and an explicit start restored both running Alpine
instances with root/home contents intact. Repeated startup retained one supervisor
and preserved disabled services. Private deployment details remain in an image,
outside the checkout.

Full deployment platform recreation, live platform capture consistency and the
updated external scheduled task remain unverified. The prior lost sandbox was
not recovered. See [IMAGE-STORAGE.md](IMAGE-STORAGE.md) for current procedures.

## Versioned image backup qualification

Automatic quick backups were enabled at 300 seconds, with six-hour full
checkpoints and retention of 12 quick/three full generations. A detached worker
produced an observed automatic quick generation; repeated bootstrap retained
one worker. This is separate from the external Grok scheduled bootstrap task.

Quick captures took approximately 2–4 seconds; full stopped-image captures took
13–14 seconds and resumed services before compression. Independent recovery
candidates extracted successfully. All 15 images in a full candidate passed
offline ext4 checks, UUID/base checks and executable helper checks. A disposable
SQLite WAL fixture retained its committed row in a standalone verified backup.
Injected copy failure thawed filesystems and resumed paused instances. An
interrupted maintenance journal recovery also passed a disposable ext4 check.

The final test run covers 30 tests, including eight real disposable filesystem
checks in a private mount namespace. Incus and both layered sandboxes remain
running; Debian demo, SSHD and Tailscale remain disabled. No full four-service
integration suite or full deployment platform reset was performed. External
backup storage remains unconfigured; local generations are not offsite backups.
