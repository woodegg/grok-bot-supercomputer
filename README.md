# Persistent Linux environments for Grok Bot Computer

This project runs Debian and Incus on Grok Bot Computer using several
**box-owned ext4 image files** in `/workspace/infra-images`. Loop devices mount
these files as Linux filesystems, keeping system files, service identities and
application data inside them. The outer host keeps its own PID1; the Debian demo
and Incus manager run systemd for native package and service management.

**Use Incus for ordinary container creation and management.** The separate
systemd-nspawn Debian guest is a demonstration. Outer runit supervises the
Incus manager, Debian demo and optional SSHD/Tailscale services without replacing
platform PID1.

[Storage](#storage-and-recovery) · [Installation](#installation) ·
[Administration](#daily-administration) · [Incus](#create-and-manage-incus-containers) ·
[Remote access](#optional-remote-access) · [Swap](#optional-compressed-swap) ·
[Documentation](#documentation)

## What you can build

- Separate development environments with their own tools, accounts and packages.
- Databases, application servers and background workers managed by systemd.
- Reusable container templates, snapshots before upgrades, and clones for tests.
- Remote administration through optional SSH and Tailscale.
- A container desktop with additional desktop and display-service setup.

For example, use one container for development, another for a database, and a
clone to test dependency upgrades. Your agent can reuse these environments
across sessions instead of rebuilding its tools each time. Applications and
desktops are installed separately; this project supplies their infrastructure.

Containers share the host's CPU, RAM and kernel. Match running services and
build concurrency to available resources. Virtual machines, kernel AppArmor
protection and delegated CPU/memory/PID limits are outside the qualified setup.
The outer services and Incus manager are trusted administrators of the shared
kernel and network.

## Storage and recovery

### Why we use loop-mounted filesystems

The earlier layout stored rootfs and runtime trees directly under `/workspace`.
An observed platform recreation lost root-owned private files and deployment
state even though the source checkout and many box-owned files survived.
Choosing a workspace directory alone was insufficient to preserve a Linux
installation with root-owned configuration, accounts and service identities.

A loop-mounted ext4 image solves the ownership problem at two different levels:

- **Outside the image:** the platform sees one ordinary file owned by `box`.
- **Inside the image:** ext4 retains Linux numeric owners, permissions, symlinks
  and filesystem metadata. System files can remain root-owned and private.
- **After recreation:** preserved images can be mounted again at the same paths,
  so native packages and saved service policy remain usable without reinstalling.

We tested this with a cleanly unmounted 128 MiB image: after an actual recreation,
its whole-file checksum was identical and internal root-owned 0700/0600 paths
retained their permissions. The external box-owned control survived; the external
root-owned private control disappeared. This is evidence for the approach, not a
platform persistence guarantee. Full deployment recreation and consistency of
platform capture while images are being written remain unverified.

Loop devices provide the filesystem access; preservation depends on the platform
keeping the backing files. Images still need consistent backups, and mounts must
be recreated after host restart. Startup validates existing images and mounts
rather than creating or formatting storage.

### Several images, with stable mount paths

We use several smaller images to separate system, runtime and application data.
A full or damaged home image need not damage the immutable system base. Smaller
images also allow separate capacity planning and offline checks, although they
still share the risks of the host storage. All image files are owned by box,
mode 0600, in a private directory outside the checkout.

| Storage under `/workspace/infra-images` | Mounted use |
| --- | --- |
| Debian, tools and Incus system images | `/workspace/debian-rootfs`, `infrastructure-rootfs`, `incus-rootfs` |
| Separate runtime images | Each environment's `/workspace/*-runtime` configuration, saved policy and logs |
| Incus data image | Manager database, certificates and image metadata at manager `/var/lib/incus` |
| Incus pool image | Ordinary instance storage at manager `/srv/incus-pool` |
| Shared read-only base image | Common OS files for layered sandboxes |
| One delta image per layered sandbox | OverlayFS upper/work directories for root filesystem changes |
| One home image per layered sandbox | Persistent `/home`, independent of root replacement |

The first eight system/runtime/data/pool images have 6 GiB total capacity;
sandbox images are added separately. Budget their full capacity: the persistence
experiment's sparse image occupied its full capacity after restoration.
The source remains `/workspace/grok-bot-supercomputer`; rootfs/runtime directories
are mount destinations. Optional swap has its own separate setup.

### Shared bases and independent home data

For similar sandboxes, OverlayFS combines a shared read-only base with each
sandbox's writable delta. A write goes into that sandbox's delta; writes under
`/home` go into its separate home image. Multiple sandboxes share the base bytes
while keeping their changes and home data independent.

To replace an OS base, stop the sandbox, select a new base and a fresh delta,
and retain its home. Old bases/deltas can remain for rollback. Reapply intended
packages and configuration; old changes are not automatically compatible with a
new OS. See the [layered sandbox procedure](docs/IMAGE-STORAGE.md#replace-a-base-and-roll-back).
Ordinary Incus-managed instances remain available through the `dir` pool.

Native Incus snapshot/clone/export commands do not capture externally managed
layered roots and homes. The wrappers refuse those operations for registered
layered sandboxes; ordinary Incus instances retain native snapshot/clone support.

### Versioned backup and recovery

`backupctl enable` starts automatic quick backups every five minutes (keep 12)
and full checkpoints every six hours (keep three). Quick capture briefly pauses
running layered sandboxes and freezes their owned writable filesystems; full
checkpoints stop infrastructure and copy all cleanly unmounted images. Services
resume before compression, preserving saved disabled states. Full checkpoints
also cover ordinary Incus pool data; quick backups focus on the managed layers.

```sh
backupctl status
backupctl quick
backupctl checkpoint
```

Backups live outside Git under `/workspace/infra-images/backups`. Verify and
extract a completed generation into a new recovery candidate before restoring;
never overwrite mounted images. See [backup and recovery](docs/IMAGE-STORAGE.md#automatic-versioned-backups)
for interruption times, SQLite handling and independent storage configuration.
Local versions cannot protect against loss or rollback of the whole workspace.
Keep credentials, runtime data and backups out of Git; see [privacy](docs/PRIVACY.md).

To measure preservation, use the [survivability check](docs/IMAGE-STORAGE.md#measure-survival-before-and-after-recreation).
It records hashes, filesystem UUIDs and permissions before/after recreation.
Once prepared, every bootstrap records a live check of immutable bases and a
dedicated probe; writable image contents remain unverified until compared in a
deliberate stopped/unmounted test. The checks do not reset the host or register
the external schedule.

For a fresh image deployment, follow [IMAGE-STORAGE.md](docs/IMAGE-STORAGE.md)
alongside the native-package playbook. Preserve existing installation paths,
identities and service policy when reusing a deployment; replacing an existing
installation with images requires a separate migration plan.

## Installation

### Use a coding agent

Use a coding-agent session with file and terminal access to the Linux host where
you want the containers to run. The host needs the privileges and kernel
features listed in the [from-scratch playbook](docs/FROM-SCRATCH.md); an agent
cannot supply capabilities that its host does not permit.

Paste this task into **Codex, Claude Code, Antigravity or Grok Build**:

```text
Set up https://github.com/woodegg/grok-bot-supercomputer on my Grok Bot Computer.

1. Clone the repository into /workspace/grok-bot-supercomputer. Reuse an
   existing checkout rather than replacing it.
2. Read AGENTS.md, the host's installation policy if present, and
   docs/FROM-SCRATCH.md, docs/CONFIGURATION.md, docs/OPERATIONS.md and
   docs/IMAGE-STORAGE.md.
3. Check the playbook prerequisites, available disk space, namespace/cgroup
   permissions and bridge subnet conflicts before provisioning.
4. For a fresh installation, use docs/IMAGE-STORAGE.md to create separate
   box-owned ext4 system/runtime/data/pool images in /workspace/infra-images,
   outside the checkout. Mount them at the documented /workspace paths, then
   follow docs/FROM-SCRATCH.md with signed native packages, provisioning scripts,
   control installers and Incus initializer. Reuse existing deployments without
   replacing paths, images, identities or state. Keep private data out of Git.
5. On a fresh installation, enable Incus and disable the direct Debian demo.
   Preserve saved service policy and identities on an existing installation.
   Leave SSHD/Tailscale disabled unless I supply the access setup separately.
6. Run unit tests and the playbook's Incus-only instance/snapshot/clone checks
   on the fresh installation. Verify startup is idempotent and disabled states
   survive. The optional full integration suite enables all four services;
   do not run it without separate access/disruption authorization.
7. Give me the scheduling prompt from the README to send to the Grok Bot chat
   window, creating a task that runs bootstrap every five minutes. Scheduler
   registration remains pending until that chat task has actually been created.
8. Run a manual bootstrap check and verify the scheduled invocation in the
   private lifecycle events when available. Report service status, test results,
   schedule registration and remaining requirements. Keep private deployment
   records outside the checkout. Claim reboot/schedule verification only for
   observations actually made.
```

If you prefer to clone first, run this from a writable project directory and
open the resulting folder in your coding agent:

```sh
cd /workspace
git clone https://github.com/woodegg/grok-bot-supercomputer.git
cd grok-bot-supercomputer
```

Run your agent in the checkout on the target host. Installation and sign-in
for the agent are separate prerequisites. Private repositories require GitHub
authentication. For manual setup, follow [FROM-SCRATCH.md](docs/FROM-SCRATCH.md);
the playbook uses reviewed scripts and commands rather than an unattended installer.

### Schedule bootstrap through Grok Bot chat

After installation, send this prompt in the **Grok Bot chat window** to create
its recurring task. Use a five-minute interval:

```text
Create or update one task on my Grok Bot Computer that runs every five minutes:
/bin/sh /workspace/infra-images/startup.sh --trigger scheduled
Run the command and report failures. Preserve saved disabled service states.
Do not reinstall, initialize storage, or enable disabled services.
Confirm that the recurring task has been created or updated; avoid duplicates.
```

Image deployments use this box-owned entrypoint outside the mounted runtime.
Use the installed startup path reported by your agent if your existing deployment
has a different layout. The command needs root or a trusted operator with
working noninteractive sudo. Confirm task creation in chat, then check the
startup events after its first run:

```sh
export PATH="$HOME/.local/bin:$PATH"
infractl status
infractl events 20
```

A `scheduled` trigger in the events confirms that the bootstrap command was
invoked. Review its outcome to see whether it started infrastructure, found it
already running, or skipped deliberately disabled services. Task registration
and a successful bootstrap run are separate checks. Registration remains pending
until the chat task has actually been created. The backup worker is a separate
timer; its events do not prove that Grok scheduled bootstrap is running.

Runit recovers exited services immediately. The five-minute chat task checks for
a missing supervisor and starts it while honoring saved service policy.
Bootstrap uses the installed workspace data; it does not reinstall software,
reinitialize Incus or force disabled services on. The same check recovers the
environments after a host restart or refresh when their workspace data remain.

The Debian runtime's startup script forwards to the infrastructure startup
entry for compatibility. This schedule does not reactivate optional swap.

## Daily administration

Use these controls from the outer host or its configured SSH session. The
installer creates command symlinks in the installing user's `~/.local/bin` and
configures Bash PATH, preserving existing commands. For an already open terminal,
run `export PATH="$HOME/.local/bin:$PATH"`. The SSH operator's login PATH is
configured inside the tools filesystem. Command wrappers use noninteractive
sudo when needed. You can also use the installed command directory directly:

```sh
export PATH="$HOME/.local/bin:$PATH"
infractl status
sv status debian incus sshd tailscaled
infractl events 20
```

| Command | Effect |
| --- | --- |
| `infractl start` | Enable the supervisor and start services whose saved policy is enabled |
| `infractl stop` | Stop all four services and persistently disable the supervisor |
| `infractl restart` | Restart an enabled supervisor; retain each service's saved policy |
| `infractl enable NAME` | Persistently enable a service; start it if the supervisor is running |
| `infractl disable NAME` | Stop and persistently disable a service |
| `infractl service-restart NAME` | Restart an enabled service without changing its saved policy |
| `infractl events 20` | Show recent outer startup/lifecycle events |
| `sv status NAME` | Inspect the supervised process and logger |

`NAME` is `debian`, `incus`, `sshd` or `tailscaled`. Native `sv up/down/restart`
controls current process state; use `infractl enable/disable` for policy that
survives recurring startup and scanner restarts. Enable can return before a
guest or daemon is ready; wait for its command/API before using it.

Choose the desired environment combination by running both commands in its row:

| Environments | Debian command | Incus command |
| --- | --- | --- |
| Incus only (recommended) | `infractl disable debian` | `infractl enable incus` |
| Both | `infractl enable debian` | `infractl enable incus` |
| Debian demo only | `infractl enable debian` | `infractl disable incus` |
| Neither | `infractl disable debian` | `infractl disable incus` |

SSHD and Tailscale are independent of this selection. Disabling access services
interrupts connections through them.

For settings, use `sudoedit` on the applicable runtime's `etc/config.toml`, then
restart that environment with `infractl service-restart NAME`. Saved enable
policy lives in down/disabled markers, not TOML switches. See
[configuration](docs/CONFIGURATION.md) for the precise paths and profiles.

Logs are private under `infrastructure-runtime/state/events.jsonl`,
`state/logs/NAME/current`, and each guest runtime's `state/events.jsonl`.
Read a daemon's log, for example, with:

```sh
sudo tail -n 50 /workspace/infrastructure-runtime/state/logs/sshd/current
```

## Create and manage Incus containers

Use the [Incus project](https://linuxcontainers.org/incus/) and its
[official documentation](https://linuxcontainers.org/incus/docs/main/) as the
full manual. The [first-steps tutorial](https://linuxcontainers.org/incus/docs/main/tutorial/first_steps/)
explains instance lifecycle, configuration, commands and snapshots. This
repository supplies the persistent management environment and wrappers.

After the playbook initializes storage/network, enable the manager and check
readiness. Bootstrap does not repeat initialization:

```sh
infractl start
infractl enable incus
incus list
incus storage show local
incus network show incusbr0
incus profile show default
```

Create and use a container, then manage its native Debian packages and units:

```sh
incus launch images:debian/13 demo
incus list
incus info demo
incus exec demo -- /bin/bash
# Run from the outer command session after exiting the interactive shell:
incus exec demo -- apt-get update
incus exec demo -- apt-get install PACKAGE
incus exec demo -- systemctl status UNIT.service
incus stop demo
incus start demo
```

For an ordinary Incus-managed instance such as `demo`, create a checkpoint,
restore it with the container stopped, or clone it into
another instance. Restoration replaces changes made after the checkpoint:

```sh
incus snapshot create demo checkpoint
incus info demo
incus stop demo
incus snapshot restore demo checkpoint
incus start demo
incus copy demo/checkpoint demo-copy
incus start demo-copy
```

For backup/export/import, follow the
[Incus backup guide](https://linuxcontainers.org/incus/docs/main/howto/instances_backup/).
The wrapper runs the Incus client in the management guest: client-side file
paths for file transfer/export/import refer to that guest's filesystem. For
example, its `/root` is under the outer `incus-rootfs/root` directory. Store
private backups outside the source checkout and copy them to preserved storage.

`incus stop NAME` affects one instance. `infractl disable incus` stops the
entire manager and its instances; re-enabling restores previously running
instances while preserving deliberately stopped ones. Use
`infractl service-restart incus` for a manager restart and the manager's controller for
native package maintenance:

```sh
sudo /workspace/incus-runtime/bin/debianctl exec -- apt-get update
sudo /workspace/incus-runtime/bin/debianctl exec -- systemctl status incus
```

The default pool uses `dir`: snapshots and clones copy files and consume time
and storage accordingly. Default instances are unprivileged.

## Optional remote access

### Use the operator admin account

`operator` is the admin account in the Debian tools filesystem,
`infrastructure-rootfs`. Its account records and home are separate from the
outer host's account, the direct Debian demo and accounts inside Incus instances.
Inside an operator shell, `/home/operator` corresponds to
`/workspace/infrastructure-rootfs/home/operator` on the outer host. This tools
filesystem supplies SSHD and Tailscale without booting another full systemd guest.

For an image deployment, enter its login shell from the outer host terminal:

```sh
operator
```

The command verifies/remounts preserved images, uses noninteractive sudo and
enters a private mount namespace with the tools filesystem and host control
paths available. SSHD and Tailscale can stay disabled. Type `exit` to return to
the outer shell. `operator -c 'whoami'` runs a single command as operator.
`debianctl shell` enters the separate Debian demo. For older directory deployments,
the existing `infractl host-exec sshd -- /usr/sbin/runuser --login operator`
entry still requires the SSHD service to be running.

If you previously entered with plain `sudo chroot ... runuser`, exit that shell
and reenter with `operator`: a plain chroot does not expose the host control
paths, so `incus`/`infractl` may be unavailable even when their PATH is configured.

The provisioned operator has passwordless sudo. Inside its shell:

```sh
sudo -n whoami
# Prints root in the tools environment.
infractl status
incus list
```

The installed wrappers let operator administer the outer infrastructure and
Incus manager through sudo. Ordinary shell paths and native package commands
belong to the tools filesystem; `incus exec NAME -- COMMAND` runs inside the
named instance.

### SSH

The native host-tools role includes OpenSSH. Its default configuration listens
on IPv4 `0.0.0.0:22`, accepts public keys for `operator` and disables password
and root login.

Authorized keys use one file per login user: the file named `operator` below
contains all of that user's allowed public keys, one key per line. Keep each
private key on its client computer.

On a fresh installation, install **your public key** at the configured location:

```sh
sudo install -d -o root -g root -m 755 /workspace/infrastructure-rootfs/etc/ssh/authorized_keys
sudo install -o root -g root -m 644 /path/to/your-public-key.pub /workspace/infrastructure-rootfs/etc/ssh/authorized_keys/operator
```

Replace the public-key source path before running this. For an existing
installation, preserve its authorized keys and add the new key deliberately
instead of replacing that file. Preserve existing SSH host keys too.

To change the port, listen address or allowed accounts, edit the installed
configuration; account names must match the tools filesystem's accounts and
its authorized-key filenames:

```sh
sudoedit /workspace/infrastructure-rootfs/etc/ssh/sshd_config.d/container-infrastructure.conf
# Create any missing native host keys and validate the configuration:
sudo chroot /workspace/infrastructure-rootfs /usr/bin/ssh-keygen -A
sudo install -d -o root -g root -m 755 /workspace/infrastructure-rootfs/run/sshd
sudo chroot /workspace/infrastructure-rootfs /usr/sbin/sshd -t
infractl start
infractl enable sshd
# Once ready, verify through the service's mount namespace:
infractl host-exec sshd -- /usr/sbin/sshd -t
```

Ensure the chosen port is available and reachable through the host/platform's
network policy. Inspect the host fingerprint locally and compare it during
your first client connection:

```sh
sudo chroot /workspace/infrastructure-rootfs /usr/bin/ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub
# Run from your client, using your private key and the host's reachable address:
ssh -i /path/to/your-private-key -p 22 operator@HOST_ADDRESS
```

After changing an active SSH configuration, validate it and use
`infractl service-restart sshd`. Keep a recovery session available while
checking the new connection. Use `infractl disable sshd` to disable it
persistently. Existing guest access identity can instead be preserved through
[optional migration](docs/MIGRATION.md).

### Tailscale

The native host-tools role includes `tailscaled` and the `tailscale` CLI.
The outer service uses TUN `tailscale0`, native nftables, and a SOCKS5 listener
on `127.0.0.1:1055`. Its baseline accepts neither tailnet DNS nor subnet routes
and uses no exit node. The service wrapper checks saved preferences before
start and applies this baseline when the daemon API is ready.

Enable the daemon and authenticate with explicit baseline settings:

```sh
infractl start
infractl enable tailscaled
# Retry status until the daemon API is ready; NeedsLogin is expected initially.
tailscale status
tailscale login --accept-dns=false --accept-routes=false --exit-node=
# Open the displayed login URL and finish authorization, then verify:
tailscale status
tailscale ip -4
tailscale ping PEER_NAME_OR_IP
```

Replace the peer placeholder with an authorized peer. Daemon readiness and
`NeedsLogin` do not establish an online tailnet connection. A node may also
require approval in your tailnet's administration console.

Inspect or reapply the supported routing preferences with:

```sh
tailscale debug prefs
tailscale set --accept-dns=false --accept-routes=false --exit-node=
```

Keep these flags when logging in; the CLI's default DNS acceptance differs
from this project's baseline. Exit-node or subnet-route changes require a
separate network design and recovery procedure. See the
[official Tailscale CLI reference](https://tailscale.com/docs/reference/tailscale-cli).

Permanent state is in `infrastructure-rootfs/var/lib/tailscale`; the runtime
socket is `/run/tailscale/tailscaled.sock` inside the service's mount namespace.
Use the provided `tailscale` wrapper to reach that daemon. Launch settings live
in the deployed `infrastructure-runtime/libexec/host-service.py` and its source
counterpart; review, redeploy and restart deliberately for changes.

Use `infractl service-restart tailscaled` to restart the enabled daemon, or
`infractl disable tailscaled` to stop it persistently. These retain its private
identity. `tailscale logout` explicitly logs out the current account.

With SSHD enabled, a client on the permitted tailnet can use regular OpenSSH:

```sh
ssh -i /path/to/your-private-key operator@TAILSCALE_IP
```

The SSHD account, public keys and host-key checks still apply. Tailnet access
policy must permit the connection. Keep login URLs, auth keys, node state and
real network inventories out of Git.

## Explore the Debian demo

The direct systemd-nspawn guest is independent of Incus instances. Enable it
and wait for boot:

```sh
infractl start
infractl enable debian
debianctl exec -- systemctl is-system-running
debianctl shell
```

`debianctl shell` enters the demo's `operator` account; `debianctl exec -- COMMAND`
runs as guest root. Guest paths and package commands belong to `debian-rootfs`.
Use `infractl service-restart debian` to restart it and `infractl disable debian`
to stop it persistently. Its services share the outer network namespace, so
choose available ports. See [direct-container notes](docs/SETUP-RECORD.md) and
[operations](docs/OPERATIONS.md) for configuration and maintenance.

## Optional compressed swap

For a host with approximately 16 GiB RAM, the tested optional setup uses a
4 GiB swap backing file through a loop device, with zswap caching pages in
compressed RAM. Direct swapfile activation on the tested OverlayFS mount failed.
Swap provides headroom during memory pressure, with CPU/RAM costs for compression
and slower I/O when pages reach disk; it does not add physical RAM.

Follow the [swap playbook](docs/FROM-SCRATCH.md#optional-outer-host-swap) for
sizing, creation, verification, shutdown and reactivation. Activation is opt-in
and kernel-wide. Automatic reactivation after host recreation is not configured.

## Documentation

| Guide | Use it for |
| --- | --- |
| [From scratch](docs/FROM-SCRATCH.md) | Prerequisites, provisioning, swap and qualification commands |
| [Configuration](docs/CONFIGURATION.md) | Saved service policy, paths and launch profiles |
| [Operations](docs/OPERATIONS.md) | Maintenance, troubleshooting and logs |
| [Architecture](docs/ARCHITECTURE.md) | Supervision, namespaces and trust boundaries |
| [Build record](docs/BUILD-RECORD.md) | Verification evidence and untested behavior |
| [Migration](docs/MIGRATION.md) | Preserving existing guest access identities |
| [Privacy](docs/PRIVACY.md) | Separating public source from private deployment data |

## Contributors

**Jun Zhang** — project contributor. [GitHub: woodegg](https://github.com/woodegg)
· [woodegg@hotmail.com](mailto:woodegg@hotmail.com).

See [contributor information](CONTRIBUTORS.md). Contributions should preserve
service policy and keep private deployment information out of source and history.
