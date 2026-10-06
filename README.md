# Persistent Linux environments for Grok Bot Computer

This project provides an agent playbook for running Debian and Incus on Grok
Bot Computer while retaining installed software and application data in
`/workspace`. The outer host uses Tini; the added environments run systemd for
native Linux package and service management.

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

Software installed only in the outer root filesystem can disappear when that
filesystem is replaced. Keep the source checkout and deployed data separate:

| Directory under `/workspace` | Contents |
| --- | --- |
| `grok-bot-supercomputer` | Source and documentation |
| `incus-rootfs` | Manager OS, database, certificates, images, instances and snapshots |
| `incus-runtime` | Manager launch configuration, controls and lifecycle state |
| `debian-rootfs` | Demo OS, packages, accounts, homes and journals |
| `debian-runtime` | Demo launch configuration, controls and lifecycle state |
| `infrastructure-rootfs` | Runit/OpenSSH/Tailscale tools, operator account and access identities |
| `infrastructure-runtime` | Supervision, service policies, commands and private logs |
| `swap-runtime` (optional) | Swap backing file and loop-device record |

Installed trees require root/sudo. Packages use native APT paths and databases
inside their respective filesystems. Keep configuration, credentials, logs and
backups outside Git; see [privacy](docs/PRIVACY.md).

Recovery after host recreation depends on the platform preserving or restoring
**complete rootfs/runtime trees with numeric ownership and metadata**, plus the
required host capabilities. Keeping the source checkout alone is insufficient.
Export backups to independently preserved storage; local snapshots do not
protect against losing the host's storage. Platform recreation and external
scheduler registration remain unverified by the local integration tests.

### Box-owned ext4 images

When the platform preserves box-owned files but drops root-private files, use
[the image storage playbook](docs/IMAGE-STORAGE.md). It keeps Debian systems,
runtime policy, Incus data/pool and sandbox data in several box-owned ext4 images.
An actual small-image recreation test retained identical contents and internal
root permissions. Full deployment recreation and live capture remain unverified.

The shared-base option uses read-only base images plus a separate delta and home
image per sandbox. Its custom roots require stopped image backups; native Incus
snapshot/clone/export commands do not capture them. Ordinary Incus instances
continue to support native snapshots and clones.

Image installations also support versioned quick backups and stopped full
checkpoints: `backupctl enable` and `backupctl status`. See the
[backup and recovery procedure](docs/IMAGE-STORAGE.md#automatic-versioned-backups)
for capture interruptions, retention and independent storage requirements.

For an image installation, use the permanent five-minute scheduling command:

```sh
/bin/sh /workspace/infra-images/startup.sh --trigger scheduled
```

Send this prompt to the Grok Bot chat window to create or update the existing
task, avoiding duplicate tasks:

```text
Create or update one task that runs every five minutes on my Grok Bot Computer:
/bin/sh /workspace/infra-images/startup.sh --trigger scheduled
Run the command and report failures. Preserve saved disabled service states.
Do not reinstall, initialize storage, or enable disabled services.
```

Registration remains pending until that chat task is created or updated.

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
   docs/FROM-SCRATCH.md, docs/CONFIGURATION.md and docs/OPERATIONS.md.
3. Check the playbook prerequisites, available disk space, namespace/cgroup
   permissions and bridge subnet conflicts before provisioning.
4. Use the documented /workspace defaults for a fresh installation, keeping
   rootfs/runtime trees outside the source checkout. Follow
   docs/FROM-SCRATCH.md using its signed native packages, provisioning scripts,
   control installers and Incus initializer. Preserve existing installation paths
   and state when reusing a deployment. Keep credentials and runtime data out of Git.
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
Create a scheduled task on my Grok Bot Computer that runs every 5 minutes.
On each run, execute this command:

/bin/sh /workspace/infrastructure-runtime/startup.sh --trigger scheduled

Confirm that the recurring task has been created.
```

Use the installed startup path reported by your agent if your existing deployment
has a different layout. The command needs root or a trusted operator with
working noninteractive sudo. Confirm task creation in chat, then check the
startup events after its first run:

```sh
export PATH=/workspace/infrastructure-runtime/bin:$PATH
infractl status
infractl events 20
```

A `scheduled` trigger in the events confirms that the bootstrap command was
invoked. Review its outcome to see whether it started infrastructure, found it
already running, or skipped deliberately disabled services. Task registration
and a successful bootstrap run are separate checks.

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
export PATH=/workspace/infrastructure-runtime/bin:$PATH
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

Create a checkpoint, restore it with the container stopped, or clone it into
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

From the outer host terminal, enter its login shell while SSHD is running:

```sh
infractl host-exec sshd -- /usr/sbin/runuser --login operator
```

This command needs outer root or working noninteractive sudo. It does not enable
SSHD; see the access setup below before enabling that service. Type `exit` to
return to the outer shell. `debianctl shell` enters the separate Debian demo.

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
