# Box-owned image storage

Use this layout when the platform preserves files owned by `box` but loses
root-private directory contents. All deployment files live inside ext4 images
under `/workspace/infra-images`, outside this checkout. Each image is owned by
box, mode 0600. Internal Linux owners, modes and identities remain intact.
The source checkout and public image-storage helpers remain ordinary box files.

The small, cleanly unmounted persistence experiment survived one actual platform
recreation with an identical whole-image SHA256 and internal root 0700/0600
permissions. Its external box control survived; its external root 0600 control
did not. This observation does not establish a platform contract, maximum file
size or the consistency of copying images while they are being written.
Restoration expanded the sparse experiment to its full size: budget the complete
capacity of every image, even when local `du` is initially smaller.

## Layout

| Image | Capacity | Mounted payload |
| --- | --- | --- |
| debian-system.ext4 | 768 MiB | `/workspace/debian-rootfs` |
| tools-system.ext4 | 1 GiB | `/workspace/infrastructure-rootfs` |
| incus-system.ext4 | 1.5 GiB | `/workspace/incus-rootfs` |
| debian-runtime.ext4 | 64 MiB | `/workspace/debian-runtime` |
| infrastructure-runtime.ext4 | 128 MiB | `/workspace/infrastructure-runtime` |
| incus-runtime.ext4 | 64 MiB | `/workspace/incus-runtime` |
| incus-data.ext4 | 512 MiB | manager `/var/lib/incus` |
| incus-pool.ext4 | 2 GiB | manager `/srv/incus-pool` |
| base-NAME.ext4 | explicit size | manager `/var/lib/incus/layers/bases/NAME`, read-only |
| delta-NAME.ext4 | explicit size | manager `/var/lib/incus/layers/deltas/NAME` |
| home-NAME.ext4 | explicit size | manager `/var/lib/incus/layers/homes/NAME` |

The first eight images total 6 GiB. Sandbox images are added explicitly.
Debian demo is installed but disabled; Incus alone is enabled on a fresh build.
SSHD and Tailscale remain disabled until separately configured. Lost private
host/Tailscale identities cannot be reconstructed from a surviving public key.

The manifest records file names, ext4 UUIDs, immutable base checksums, mount destinations, read-only
policy and the completion flag. The helper verifies all images before mounting,
serializes concurrent bootstrap calls, refuses unrelated mounts, and rolls back
only mounts added by a failed call. Startup never creates or formats images.
Ext4 payload subdirectories avoid exposing lost+found as a rootfs or pool.
The pool source is outside `/var/lib/incus`, as required by the Incus dir driver.

## Fresh installation

Read AGENTS.md, host installation policy, FROM-SCRATCH.md, CONFIGURATION.md and
OPERATIONS.md. Perform the namespace/cgroup, disk and subnet checks first.
Do not apply these creation steps over any existing deployment. Inspect and
preserve partial restoration remnants before choosing a genuinely fresh build.
All six standard rootfs/runtime paths must be absent before creation.

Install signed native outer provisioning tools, then run from this checkout:

```sh
sudo apt-get update
sudo apt-get install --no-install-recommends debootstrap gpgv e2fsprogs
sudo python3 scripts/create-image-storage.py create
# Do not mount data directories into an empty rootfs before debootstrap.
python3 /workspace/infra-images/image-storage.py --systems-only mount
sudo scripts/provision.sh /workspace/debian-rootfs
sudo scripts/provision-infrastructure.sh host-tools /workspace/infrastructure-rootfs
sudo scripts/provision-infrastructure.sh incus /workspace/incus-rootfs
python3 /workspace/infra-images/image-storage.py mount
sudo scripts/install-controls.sh
sudo scripts/install-infrastructure.py
# Keep formatter tools inside a persistent system for future small images.
sudo chroot /workspace/infrastructure-rootfs apt-get install --no-install-recommends e2fsprogs
sudo python3 scripts/create-image-storage.py entrypoints
```

Provisioning normally uses HTTPS. Where the host's APT HTTPS transport fails,
`sudo env DEBIAN_APT_SCHEME=http scripts/provision.sh ...` selects the official
Debian HTTP transport while retaining APT signature/package hash verification.
The same environment variable applies to provision-infrastructure.sh. Do not
disable signature or TLS verification. APT update must succeed for all configured
repositories; Error-Mode=any rejects missing security indexes.

Before marking completion, verify the role markers, native package state,
configs and persistent image mounts. Mark only a provisioned installation:

```sh
python3 - <<'PY'
import json
from pathlib import Path
p = Path('/workspace/infra-images/manifest.json')
data = json.loads(p.read_text())
data['build_complete'] = True
p.write_text(json.dumps(data, indent=2) + '\n')
PY
infractl disable debian
infractl enable incus
infractl start
sudo python3 scripts/initialize-incus.py
```

Run the unit and Incus-only instance/snapshot/clone qualification from
FROM-SCRATCH.md. The optional full suite enables access services and requires
separate access/disruption authorization. Do not run it as part of this recipe.

## Startup and normal use

Use the box-owned permanent entrypoint after recreation:

```sh
/bin/sh /workspace/infra-images/startup.sh --trigger manual
infractl status
incus list
incus exec alpine-a -- /bin/sh
python3 /workspace/infra-images/image-storage.py status
```

The user's ~/.local/bin commands point at permanent image-aware wrappers.
The helper mounts systems, runtime, database/pool and sandbox images before
executing the native controls. Existing supervisor/service disables are honored.
`infractl start` deliberately clears the supervisor disable; bootstrap does not.
The installed infrastructure-runtime/startup.sh is a box-owned compatibility
forwarder, but the permanent external entrypoint is preferred for scheduling.

Register or update the Grok Bot chat task separately, every five minutes:

```sh
/bin/sh /workspace/infra-images/startup.sh --trigger scheduled
```

Registration is pending until the chat task is actually created or updated.
Only real scheduled events in `infractl events` establish observed execution;
running the same command manually with a scheduled label proves no schedule.

## Shared-base sandboxes

This is a custom LXC OverlayFS layout managed through Incus, not an Incus storage
driver. Base images are read-only; each instance has a separate writable upper
and work in one ext4 delta image, plus an independent home image. Common lower
files are shared. Debian managers and Incus database remain ordinary writable
ext4 payloads. The tested unprivileged mapping is 0 -> 1000000, range 16777216.
Do not change idmap isolation/ranges on these instances without adapting owners.

Image preparation currently requires stopping the whole Incus manager. This
interrupts all its instances: perform these commands during maintenance.
Use trusted Incus root.tar.xz archives downloaded over HTTPS and verified against
their published stream SHA256; these are rootfs archives, not metadata archives.

```sh
infractl disable incus
layerctl base-import alpine-v1 /path/to/verified-root.tar.xz --size-mib 64
layerctl delta-create my-sandbox --size-mib 128
layerctl home-create my-sandbox --size-mib 128
infractl enable incus
# Wait for incus list to succeed before registration.
layerctl register my-sandbox --base alpine-v1 --delta my-sandbox --home my-sandbox
incus start my-sandbox
incus exec my-sandbox -- /bin/sh
layerctl status
```

BusyBox/Alpine bases get the correct LXC halt/reboot signals; their PID1 does not
use the generic systemd halt signal. The verified build leaves alpine-a and
alpine-b sharing alpine-v1 and separate delta/home images. These are fresh
example sandboxes; the previously lost grok-bot-sandbox is not recovered.

Native Incus snapshots, clones, moves, exports and publish do not capture the
externally managed roots/homes and can accidentally share writable paths. The
image-aware wrapper refuses those commands for registered layered instances.
Native commands inside the manager bypass that guard: do not use them for these
operations. Regular Incus-managed instances retain native snapshot/clone support.
Back up layered instances using a consistent stopped set of their image files,
the Incus database/runtime and manifest. Home is persistent data, not a backup.

## Replace a base and roll back

Stop the target instance before manager maintenance so it remains stopped when
the manager returns. Prepare a new immutable base and a fresh delta; keep home.
Old bases and deltas remain available for rollback. Never casually reuse an old
upper over a new base: copied files and whiteouts can hide new system files.

```sh
incus stop my-sandbox
infractl disable incus
layerctl base-import alpine-v2 /path/to/new-verified-root.tar.xz --size-mib 64
layerctl delta-create my-sandbox-v2 --size-mib 128
infractl enable incus
layerctl switch my-sandbox --base alpine-v2 --delta my-sandbox-v2
incus start my-sandbox
# Verify application behavior and reapply intentional system configuration.
# Home remains the existing home image.
```

Rollback after stopping the instance:

```sh
incus stop my-sandbox
layerctl switch my-sandbox --base alpine-v1 --delta my-sandbox
incus start my-sandbox
```

The demonstrated v1/v2 switch uses the same Alpine release with different base
markers; it proves filesystem replacement/rollback, not a distribution upgrade
or application/home-data compatibility across versions.

## Clean backup and recovery

### Record bootstrap arrival before privileged work

The permanent `startup.sh` uses `startup-entry.py` to write an entry record
before launching the image-aware controls, sudo, storage-lock acquisition or
image validation. Its private, box-owned log is outside mounted images:

```sh
tail -n 12 /workspace/infra-images/startup-entry/events.jsonl
```

Each invocation has a run ID and records `entry_received`, `control_spawned`
(with child PID) and `entry_finished` (with exit code and total elapsed time).
Timestamps are UTC; trigger, PID, parent PID and invoking UID are included.
Raw command arguments, environment variables and credentials are not logged.
The directory is 0700, files 0600, with three rotated logs at 1 MiB each.
Concurrent entry writes are serialized. The wrapper forwards termination
signals and preserves the control command's exit status. Logging failures are
printed to stderr without preventing attempted infrastructure recovery.

Compare arrival records with the private lifecycle events: no arrival record
means the command did not reach this logger, or entry logging itself failed;
arrival/spawn without lifecycle start means a pre-lifecycle wait/failure remains
possible. Arrival without finish can also mean the wrapper was killed or the
environment was suspended. It does not by itself prove a deadlock. This closes
the previous observability gap but does not register or guarantee a schedule.
Direct `infractl startup` bypasses this entry logger; scheduled tasks should use
the permanent `startup.sh`. Deploy the helper with the entrypoint; it is not
intended to be run from the source checkout, where runtime logs do not belong.

### Measure survival before and after recreation

`survivability.py` records SHA256, ext4 UUID, image size, numeric ownership,
permissions and allocated space. Every file record also includes modification
time (mtime), metadata-change time (ctime), access time (atime), numeric inode,
device, hard-link count and filesystem block size. Times have raw nanoseconds
and readable UTC dates; size/allocation values are bytes. Creation time is
explicitly unavailable through the stat API used here: ctime is not creation
time. Records include the observation time and permissions in octal.
Evidence stays private and box-owned under
`/workspace/infra-images/survivability`, outside Git. The preparation action
creates a NEW disposable 16 MiB ext4 probe with an internal root-owned 0700
directory/0600 file, plus external box-owned/root-owned control files. It does
not alter deployment image contents and refuses to replace an existing probe.

```sh
python3 /workspace/infra-images/survivability.py prepare
python3 /workspace/infra-images/survivability.py capture bootstrap-live --mode live
```

After this setup, each infrastructure `start`, `startup` or `restart` records a
live observation, including its trigger, in `survivability/observations.jsonl`.
The log rotates at 1 MiB. Repeated startup compares to the original baseline;
it never silently replaces it or formats a missing probe. An inspection problem
is reported as `needs_attention` in lifecycle events without blocking startup.
Automatic observations acquire the storage lock without waiting. If a backup
holds it (including a checkpoint restoring services while still holding its
lock), the observation records `deferred` and startup continues. The next
bootstrap retries inspection; a deferred observation is not a successful hash
check. Manual capture/verification still waits for exclusive access.
Each observation stores its full current snapshot, including these file sizes
and dates, plus before/after values for changed fields. Timestamp/inode/allocation
changes are informational, separate from byte/permission mismatches. Reading
hashes can itself affect atime; metadata is sampled before reading each file.
Live observations are not an atomic snapshot of all mutable files. Older
baselines lack newly added fields: their before values remain null rather than
being invented. Save a new separately named baseline for complete metadata
comparison; existing evidence is never silently rewritten.

Live checks hash immutable bases only when their loop attachments are read-only,
plus the unmounted probe. Mutable deployment image contents are explicitly
**UNVERIFIED**: legitimate writes would change their whole-file hashes. This
lightweight check runs when bootstrap is invoked; it does not register a Grok
task or prove a five-minute schedule. Look for actual `scheduled` invocations
and observation timestamps separately from the backup timer.

For strict whole-deployment byte comparison, arrange a deliberate maintenance
window and leave images detached across the test. Pause the external bootstrap
task first: even a disabled supervisor's bootstrap can remount storage. Restore
that task deliberately after verification. Keep the previously saved service
policy; `infractl start` below intentionally enables the supervisor and is only
appropriate if it was enabled before the test:

```sh
/workspace/infra-images/shutdown.sh
python3 /workspace/infra-images/survivability.py capture before-reset --mode offline
# Perform platform recreation separately. Do not start/remount services yet.
python3 /workspace/infra-images/survivability.py verify before-reset --mode offline
# Review the report before deliberately re-enabling the supervisor:
infractl start
```

Offline capture/verification refuses any attached deployment image. It does not
stop services automatically, replay journals, run filesystem repair or reset the
host. Keep the baseline unchanged and also copy it to independent storage if
available. Verification writes a new report, never overwrites active images or
the baseline. Use the same mode before/after. Live manual verification uses
`verify bootstrap-live --mode live`.

Exit codes: 0 = compared content/metadata match; 1 = mismatch or inspection
error; 2 = metadata/immutable checks match but mutable content is unverified.
Loss of the external root-owned control is reported separately and does not
fail preserved-image checks. Sparse allocation growth is informational if file
size/content match. Changed kernel boot/PID1/root observations are evidence of
environment changes, not proof of a particular reset mechanism. A matching
checksum proves retained bytes, not application-level recovery or future
platform guarantees. After intentional upgrades, save a new baseline with a
new label rather than rewriting old evidence; changing the bootstrap baseline
requires deliberate archival/replacement outside Git.

### Automatic versioned backups

The image installer deploys `backupctl`. Automatic backups save configuration
only, every 300 seconds with 12 retained completed generations:

```sh
backupctl enable
backupctl status
backupctl config
```

Archives are private and box-owned under `/workspace/infra-images/backups`.
Capture includes the three environment `/etc` trees, runtime configuration,
service launch scripts and disabled markers, image manifest and layer definitions.
When Incus is enabled, it also exports server, project, profile, network, storage
pool and instance definitions through the running API. Pool definitions contain
settings, not pool contents. Container application configuration inside their
filesystems is not captured. SSH keys present in `/etc` are private configuration;
operator home files and Tailscale runtime identity/state are not captured.

There are no image copies, database dumps, application data backups, filesystem
freezes, container pauses or service restarts. `quick` and `checkpoint` remain
configuration-only aliases for existing callers. Old image backup generations
are not created or rotated by the new worker. The current deployment's old
image generations were removed on explicit operator request.

Only completed, checksummed generations are published. Verify and extract to
a new directory before reviewing configuration for selective restoration:

```sh
backupctl verify config-GENERATION
backupctl extract config-GENERATION /workspace/configuration-candidate
```

Extraction does not apply configuration or recreate volumes. Backups cannot
restore rootfs, home files, databases or missing images. Files may change during
capture; this is a live configuration export, not a transactional snapshot.
Policy and logs remain outside Git in `backup-policy.json` and
`backup-events.jsonl`. Legacy interrupted-maintenance recovery remains available
solely to safely thaw/resume an interrupted old backup; new captures do not
create such maintenance records.

`backupctl disable` stops future automatic work without changing service policy.
To update the worker, disable, wait for exit, deploy reviewed helpers, then
restore its prior enabled setting. An optional independently mounted destination
can be configured with `backupctl external /path/to/independent-volume`.
It must exist outside `/workspace` on a different persistent filesystem.
This deployment has no external destination. Local configuration versions do not
survive loss of the whole workspace. Five minutes is a worker target; the Grok
bootstrap task is a separate external schedule.

```sh
/workspace/infra-images/shutdown.sh
python3 /workspace/infra-images/image-storage.py status
# All images must report unmounted. Copy the complete infra-images directory
# to separate persistent storage, preserving box ownership and file contents.
```

Shutdown stops owned services, persists supervisor disable, syncs and unmounts
all images in reverse dependency order. It refuses busy/unrelated mounts rather
than lazily detaching or force-killing platform processes. Check exit status;
do not copy a partially unmounted set. `infractl start` remounts and deliberately
reenables the supervisor after backup. Recurring bootstrap retains the disable.

On restoration, use the preserved helpers/manifest; never run create or mkfs.
If ext4 reports corruption, keep a backup of the affected image and perform
offline filesystem diagnosis/repair with services stopped. Ordinary bootstrap
does not repair or format filesystems. Inspect `df -h` inside each mounted image
and provision extra capacity deliberately. Resizing requires offline maintenance
and updating recorded capacity; no automatic expansion is implemented.

Smaller images isolate capacity and filesystem damage, but correlated host loss
and cross-image database consistency still require complete external backups.
Live platform capture, automatic scheduler execution after recreation and a
full-sized deployment recreation must be verified separately.
