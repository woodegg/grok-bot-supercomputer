# Persistent Debian and Incus infrastructure

This project provides an **agent playbook to create persistent Debian and Incus
environments on Grok Bot Computer**. It stores the installed filesystems,
packages, configuration, accounts and runtime data in `/workspace`. When that
workspace and its complete installation data are retained, the environments can
recover after reboots, refreshes and platform upgrades through scheduled
bootstrap. Keeping only the source repository or an empty workspace is not enough.

Two independently selectable systemd environments run under the platform's
Tini PID1. Outer runit supervises Debian, Incus, SSHD and Tailscale. Each systemd
guest manages its native units; Incus manages its own instances.

The directly launched Debian environment is a **demo** showing how to run a
container directly with systemd-nspawn. For regular use, **use Incus to create
and run containers**: its CLI makes creating, starting, stopping, cloning and
snapshotting multiple containers more convenient. You can disable the Debian
demo and run only the Incus environment; both options are independent.

This repository provides reusable source with generic example paths and an
`operator` account. It contains no deployed root filesystems, credentials or
runtime state. See [publication and privacy](docs/PRIVACY.md).

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
4. Before a fresh installation, adapt the playbook, scripts and templates from
   their generic /srv/container-infrastructure base to /workspace consistently.
   Store all rootfs/runtime trees there, outside the source checkout. Then follow
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

For [Codex CLI](https://developers.openai.com/codex/cli/), run `codex` in that
directory and paste the task above. In the other agents, use a session connected
to the same directory and target host. Ask each agent to read `AGENTS.md`
explicitly. Agent installation and sign-in are separate prerequisites.
While the repository is private, GitHub authentication is also required to clone
it; an authenticated `gh repo clone` can be used instead of `git clone`.

Use a `/workspace` subfolder for the source checkout. Installed rootfs/runtime
trees also belong in `/workspace`, separately from Git, and require root/sudo.
The reusable scripts and full playbook currently have generic
`/srv/container-infrastructure` defaults: the installation agent must adapt
that base consistently to `/workspace` before a fresh Grok Bot installation.
The commands in this README show the resulting workspace layout.

The playbook is a sequence of reviewed scripts and commands, rather than a
single unattended installer. For regular use, the resulting configuration should
run Incus; enable the direct Debian guest when you want to explore the demo.

### Schedule bootstrap through Grok Bot chat

After installation, send this prompt in the **Grok Bot chat window** to create
its recurring task. Use **every five minutes**, the smallest available interval:

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

The canonical entry is `infrastructure-runtime/startup.sh`; the Debian runtime's
startup script is a compatibility forwarder. Retain the complete rootfs/runtime
trees, numeric owners and metadata in `/workspace` across platform changes.

## Installation layout

For Grok Bot Computer, installed data lives under `/workspace` after the agent
adapts the generic playbook defaults. Keep installed filesystems/runtimes outside
the `/workspace/grok-bot-supercomputer` source checkout.

| Persistent installation | Contents |
| --- | --- |
| `debian-rootfs` | Direct Debian demo OS, packages, accounts, homes and journals |
| `debian-runtime` | Demo launch configuration, controls and lifecycle state |
| `incus-rootfs` | Incus manager OS, native packages, database, certificates, images, instances and snapshots |
| `incus-runtime` | Manager launch configuration, controls and lifecycle state |
| `infrastructure-rootfs` | Native runit/OpenSSH/Tailscale tools and outer access identities |
| `infrastructure-runtime` | Outer supervision, four services, commands and private logs |
| Your project checkout | Reusable source and documentation; no live installation or secrets |

Packages use native APT paths and package databases inside their selected
filesystem. Configuration and private state remain root-owned outside Git.

## Administration commands

Use these controls from the outer host or its configured SSH session. Command
wrappers use noninteractive sudo when needed; the operator needs the appropriate
sudo rights. Add the command directory to your shell's PATH:

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

SSHD and Tailscale are independent of this selection. Disabling Incus shuts
down its manager and instances; enabling restores previously running instances
while deliberately stopped instances stay stopped. Disabling access services
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

## Configure and enable SSHD

The native host-tools role includes OpenSSH. Its default configuration listens
on IPv4 `0.0.0.0:22`, accepts public keys for the `operator` account and disables
password and root login. That account belongs to `infrastructure-rootfs` and
has its own home and sudo policy.

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

## Configure and enable Tailscale

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

## Debian demo usage manual

The direct Debian system demonstrates native systemd under systemd-nspawn.
It is independent of containers created by Incus. Enable it and wait for boot:

```sh
infractl start
infractl enable debian
debianctl status
debianctl exec -- systemctl is-system-running
# Operator login shell; type exit to return to the outer host:
debianctl shell
# Root shell when needed:
debianctl exec -- /bin/bash
```

`debianctl exec` runs the guest command as root; `debianctl shell` defaults to
its `operator` account. Commands and paths within those sessions belong to the
guest filesystem, with its own accounts, homes and package database.

Install packages and manage their native units inside the guest. Replace
`PACKAGE` and `UNIT.service` with your selected package and service:

```sh
debianctl exec -- apt-get update
debianctl exec -- apt-get install PACKAGE
debianctl exec -- systemctl enable --now UNIT.service
debianctl exec -- systemctl status UNIT.service
debianctl exec -- journalctl -u UNIT.service -n 50 --no-pager
# Create an application account and enter its shell:
debianctl exec -- useradd --create-home --shell /bin/bash appuser
debianctl shell appuser
```

Restart or stop the whole demo through its supervisor owner:

```sh
infractl service-restart debian
infractl disable debian
# Explicitly re-enable later:
infractl enable debian
```

Packages, account data, files and persistent journals remain in `debian-rootfs`
across guest restarts. Launch settings are in `debian-runtime/etc/config.toml`;
private lifecycle events are available through `debianctl events 20`.
Use `infractl` for its whole-environment lifecycle while runit owns it.
Guest services share the outer network namespace, so choose available ports.
For routine creation of multiple systems and snapshots, use Incus below.

## Incus usage manual

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
and storage accordingly. Default instances are unprivileged. This recipe has
not qualified virtual machines, kernel AppArmor protection or delegated
CPU/memory/PID limits; upstream features must be checked against host capabilities
and the installed Incus version before enabling them.

## Further documentation and persistence

Read [configuration](docs/CONFIGURATION.md), [operations](docs/OPERATIONS.md),
[architecture](docs/ARCHITECTURE.md), [build evidence](docs/BUILD-RECORD.md), and
the [direct-container notes](docs/SETUP-RECORD.md).

The outer services and Incus manager are trusted administrators of the shared
kernel/network. Reboots, refreshes and upgrades can be recovered when the full
`/workspace` installation survives or is restored with its numeric owners and
metadata. Local service tests cannot prove the platform's preservation contract;
check the Grok Bot scheduled task and its actual bootstrap events separately.

## Contributors

**Jun Zhang** — project contributor. [GitHub: woodegg](https://github.com/woodegg)
· [woodegg@hotmail.com](mailto:woodegg@hotmail.com).

See [contributor information](CONTRIBUTORS.md). Contributions should preserve
service policy and keep private deployment information out of source and history.
