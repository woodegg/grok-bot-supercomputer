# Operations

For image deployments, `backupctl status` reports saved policy and completed
recovery points. Use `backupctl quick` for layered sandbox capture and
`backupctl checkpoint` for a full stopped-image checkpoint. Checkpoints briefly
interrupt enabled infrastructure and then restore its saved policy. Verify and
extract into a new candidate before planning restoration; see
[versioned backups](IMAGE-STORAGE.md#automatic-versioned-backups). Local archives
still require independent storage protection.

For a loop-image deployment, start with the box-owned
`/workspace/infra-images/startup.sh` and use the image-aware ~/.local/bin commands.
Read [image storage operations](IMAGE-STORAGE.md) for mount checks, shared-base
sandbox creation/upgrades and consistent stopped backups. Native snapshots and
clones below apply to ordinary Incus-managed instances, not custom layered roots.

Use /workspace/infrastructure-runtime/bin commands. The installer exposes
infractl, sv, incus, debianctl and tailscale through ~/.local/bin for the invoking
user, preserving existing commands, and configures Bash PATH. For an existing
terminal, run `export PATH="$HOME/.local/bin:$PATH"`. Wrappers use sudo -n
as needed. The SSH operator account has the controls in its login and sudo PATH.

```sh
export PATH="/workspace/infrastructure-runtime/bin:$PATH"
infractl status
sv status debian incus sshd tailscaled
infractl enable debian
infractl disable debian
infractl enable incus
infractl disable incus
infractl service-restart debian
infractl service-restart incus
infractl service-restart sshd
infractl events 20
```

See [environment configuration](CONFIGURATION.md) for the both/Debian-only/
Incus-only/neither command matrix, saved markers and advanced launch settings.
Disabling Incus stops its instances; enabling restores previously running ones
and leaves intentionally stopped instances stopped. Outer access is independent.

Enable/disable records persistent state; enable can return before readiness.
Wait for `incus list` or `debianctl exec systemctl is-system-running` as needed.
Native `sv up/down/restart NAME` controls a supervised process immediately but
does not record your permanent policy. Use infractl disable to retain a stopped
state across scanner/host restarts. Do not run guest controller start/stop
concurrently with its runit owner; use infractl for whole-environment lifecycle.
`debianctl exec` is the command-entry interface into the Debian environment.

```sh
# Debian native packages and services:
debianctl exec -- apt-get update
debianctl exec -- apt-get install PACKAGE
debianctl exec -- systemctl status UNIT
# Incus instances and native dir snapshots:
incus launch images:debian/13 my-instance
incus exec my-instance -- /bin/bash
incus snapshot create my-instance checkpoint
incus stop my-instance
incus snapshot restore my-instance checkpoint
incus start my-instance
incus copy my-instance/checkpoint another-instance
incus list
incus storage show local
```

Snapshot/clone copies consume time and space proportional to their files.
Snapshot restore requires the instance stopped here. Stopped instances remain
stopped across manager restarts; running instances restart automatically.
Do not set CPU/memory/PID limits until the platform delegates those controllers.
Instances inherit the platform kernel. AppArmor protection is unavailable here;
this is not a hostile multi-tenant isolation environment.

```sh
# Manage native packages/units in the manager without replacing its database:
sudo /workspace/incus-runtime/bin/debianctl exec -- apt-get update
sudo /workspace/incus-runtime/bin/debianctl exec -- apt-get install PACKAGE
sudo /workspace/incus-runtime/bin/debianctl exec -- systemctl status incus
# Native outer tools package maintenance, while the named access service runs:
infractl host-exec sshd -- apt-get update
infractl host-exec sshd -- apt-get install PACKAGE
```

Maintain installation-policy conventions and preserved disabled states for
future changes. Audit routing before Tailscale preference changes. Its approved
baseline is TUN tailscale0, SOCKS127.0.0.1:1055, native nftables, no exit node,
accept-DNS off and accept-routes off. NeedsLogin means unauthenticated. Use
`tailscale status` to verify authentication; no automatic login is performed.
For explicit login, retain the baseline with
`tailscale login --accept-dns=false --accept-routes=false --exit-node=`.
Outer SSH listens on 0.0.0.0:22, allows operator public keys and disables passwords
and root login. Its keys/config/state live in infrastructure-rootfs.

The five-minute command is:

```sh
/bin/sh /workspace/infrastructure-runtime/startup.sh --trigger scheduled
```

It is idempotent and honors whole-supervisor and individual-service disables.
If the scanner was killed, bootstrap first drains its owned orphan supervisors
and then starts one replacement. Logs explain caller/trigger, outer boot/PID
identity observations, disable decisions, launches and readiness/errors. See
infrastructure-runtime/state/events.jsonl, each guest runtime/state/events.jsonl,
state/logs/SERVICE/current, and native guest persistent journals. Logs rotate;
private event data contains no command argument/environment dumps.

For a deliberate full shutdown use `infractl stop`; automatic calls skip it.
Use `infractl start` to explicitly re-enable; individual disabled services stay
disabled. `infractl restart` is refused if the whole supervisor is disabled.

Before backup, stop our infrastructure and check all owned guests have drained.
Copy all three rootfs and three runtime trees plus source with numeric ownership,
ACLs, xattrs and hardlinks preserved (for example GNU tar --numeric-owner --acls
--xattrs --sparse). Restore before triggering startup, exclude ephemeral live
/run mounts/PID records, preserve permanent down/disabled markers and keys.
Do not copy only project source or only Incus instance directories: its database,
images, certificates and storage must be restored together. Actual platform
recreation and external schedule registration remain operator responsibilities.
