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

## Set up with Codex, Claude, Antigravity or Grok Build

Use a coding-agent session with file and terminal access to the Linux host where
you want the containers to run. The host needs the privileges and kernel
features listed in the [from-scratch playbook](docs/FROM-SCRATCH.md); an agent
cannot supply capabilities that its host does not permit.

Paste this task into **Codex, Claude Code, Antigravity or Grok Build**:

```text
Set up https://github.com/woodegg/grok-bot-supercomputer on this Linux host.

1. Clone the repository into /srv/container-infrastructure/source. If it is
   already checked out, inspect the existing installation instead of replacing it.
2. Read AGENTS.md, the host's installation policy if present, and
   docs/FROM-SCRATCH.md, docs/CONFIGURATION.md and docs/OPERATIONS.md.
3. Check the playbook prerequisites, available disk space, namespace/cgroup
   permissions and bridge subnet conflicts before provisioning.
4. Follow docs/FROM-SCRATCH.md in order, using its signed native package
   sources, provisioning scripts, control installers and Incus initializer.
   Keep rootfs, configuration, credentials and runtime state outside Git.
5. On a fresh installation, enable Incus and disable the direct Debian demo.
   Preserve saved service policy and identities on an existing installation.
   Leave SSHD/Tailscale disabled unless I supply the access setup separately.
6. Run unit tests and the playbook's Incus-only instance/snapshot/clone checks
   on the fresh installation. Verify startup is idempotent and disabled states
   survive. The optional full integration suite enables all four services;
   do not run it without separate access/disruption authorization.
7. Give me the exact startup command to register in the host schedule, the
   service status, test results and remaining requirements. Keep the private
   deployment record outside the checkout. Do not claim a host reboot or
   scheduler registration was verified unless it actually was.
```

If you prefer to clone first, run this on a fresh target and open the resulting
folder in your coding agent:

```sh
sudo install -d -m 755 -o "$(id -u)" -g "$(id -g)" /srv/container-infrastructure/source
git clone https://github.com/woodegg/grok-bot-supercomputer.git /srv/container-infrastructure/source
cd /srv/container-infrastructure/source
```

For [Codex CLI](https://developers.openai.com/codex/cli/), run `codex` in that
directory and paste the task above. In the other agents, use a session connected
to the same directory and target host. Ask each agent to read `AGENTS.md`
explicitly. Agent installation and sign-in are separate prerequisites.
While the repository is private, GitHub authentication is also required to clone
it; an authenticated `gh repo clone` can be used instead of `git clone`.

The playbook is a sequence of reviewed scripts and commands, rather than a
single unattended installer. For regular use, the resulting configuration should
run Incus; enable the direct Debian guest when you want to explore the demo.

## Installation layout and controls

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

## Contributors

**Jun Zhang** — project contributor. [GitHub: woodegg](https://github.com/woodegg)
· [woodegg@hotmail.com](mailto:woodegg@hotmail.com).

See [contributor information](CONTRIBUTORS.md). Contributions should preserve
service policy and keep private deployment information out of source and history.
