# Configure Debian and Incus environments

Use `infractl` for the whole Debian environment and the whole Incus manager.
It uses noninteractive sudo when needed. The two environments can be enabled
independently; outer SSHD and Tailscale have separate settings.

```sh
export PATH="/workspace/infrastructure-runtime/bin:$PATH"
infractl status
```

## Choose which environments run

Run both commands in the selected row. Settings take effect while the
supervisor runs and persist across supervisor restarts and scheduled startup.

| Desired environments | Debian command | Incus command |
| --- | --- | --- |
| Both | `infractl enable debian` | `infractl enable incus` |
| Debian only | `infractl enable debian` | `infractl disable incus` |
| Incus only | `infractl disable debian` | `infractl enable incus` |
| Neither | `infractl disable debian` | `infractl disable incus` |

Disabling Debian gracefully shuts down its systemd guest. Disabling Incus
shuts down the manager and its managed containers. Re-enabling Incus restores
previously running containers; deliberately stopped containers remain stopped.
These operations retain packages, configuration, accounts, files, the Incus
database, images and snapshots. Outer SSHD/Tailscale remain independent.

Enable can return before the guest is ready. Check readiness with:

```sh
debianctl exec -- systemctl is-system-running
incus list
```

Restart an enabled environment without changing its persistent policy:

```sh
infractl service-restart debian
infractl service-restart incus
```

## Supervisor and independent access services

If the supervisor is intentionally stopped, enable/disable only updates the
saved service policy. Explicitly start the supervisor to launch enabled services:

```sh
infractl start
```

`infractl stop` stops all four services and persistently disables the supervisor;
automatic startup then skips it. `infractl start` clears that whole-supervisor
disable, preserving each service's setting. `infractl restart` restarts an
enabled supervisor and refuses a persistently disabled one.

Access settings are controlled separately:

```sh
infractl enable sshd
infractl disable sshd
infractl enable tailscaled
infractl disable tailscaled
```

Disabling SSHD or Tailscale interrupts access through that service. Enabling
Tailscale does not authenticate a logged-out identity; check `tailscale status`.
Use `infractl` for persistent policy; native `sv up/down/restart` controls the
current process state without saving the same enable/disable policy.

## Saved policy and configuration paths

| Setting | Persistent location |
| --- | --- |
| Whole supervisor disabled | `/workspace/infrastructure-runtime/state/disabled` |
| Service disabled | `/workspace/infrastructure-runtime/services/NAME/down` |
| Debian guest disabled | `/workspace/debian-runtime/state/disabled` |
| Incus manager disabled | `/workspace/incus-runtime/state/disabled` |
| Supervisor filesystem/environment mapping | `/workspace/infrastructure-runtime/etc/config.toml` |
| Debian nspawn configuration | `/workspace/debian-runtime/etc/config.toml` |
| Incus manager nspawn configuration | `/workspace/incus-runtime/etc/config.toml` |

`infractl enable/disable` synchronizes the service down marker and guest disabled
marker. Use these commands instead of manually editing markers. TOML files
configure launch paths/capabilities, not boolean service-enable switches.
Configuration and private state are root-owned and protected with 0600/0700.

The example Debian machine is `debian-local`, with rootfs
`/workspace/debian-rootfs`. The Incus machine is `incus-manager`, with rootfs
`/workspace/incus-rootfs`. Both runtime trees remain outside their rootfs and
outside the source repository. The trusted Incus profile uses
`private_users="no"`, `allow_nesting=true`, `allow_tun=false`. The direct Debian
demo defaults to `private_users="identity"` and `allow_tun=false`. Review any
privilege changes deliberately. Optional access migration disables the guest
SSH/Tailscale units after moving access to the outer tools filesystem.

Changes to guest launch configuration require root and an environment restart.
Preserve the installation policy, identities and networking baseline. For an
advanced deliberate edit, use `sudoedit` on that environment's config, then
`infractl service-restart NAME`. Enabling/disabling needs no configuration edit.
The explicit storage/network initializer belongs to initial setup, never to
recurring startup or a routine service-enable operation.

Manage units inside Debian with `debianctl exec -- systemctl ...`. Manage an
individual Incus instance with `incus start NAME` or `incus stop NAME`; that
is separate from enabling/disabling the entire Incus manager.

## Recurring startup and persistence

### Optional swap policy

Swap is an independent outer-host resource. The manual procedure in
[FROM-SCRATCH.md](FROM-SCRATCH.md#optional-outer-host-swap) uses a root-owned
`/workspace/swap-runtime` directory, a mode-0600 4 GiB `swapfile`, and a private
`loop-device` record. The record is informational: rediscover the actual device
by its backing file after host recreation. Creation/activation is explicitly
opt-in; no swap enable setting or automatic activation is currently installed.
Infrastructure start/stop and service enable/disable do not change swap.
Preserving the file does not preserve the kernel's active swap registration.
Optional zswap is enabled manually through the kernel sysfs interface, with
the existing `lzo` compressor, `zbud` allocator and 20-percent pool limit in the
tested configuration. Its setting is kernel-wide and has no installed startup
hook or saved enable policy. Infrastructure service state does not control it.

### Infrastructure startup

Register this command in the platform's external five-minute schedule:

```sh
/bin/sh /workspace/infrastructure-runtime/startup.sh --trigger scheduled
```

It starts one missing supervisor, honors persistent disables and never
reinstalls/reinitializes surviving installations. The old Debian startup path
forwards to this supervisor. Inspect startup decisions with `infractl events 20`.

Persistence across service/guest restarts has been verified. Actual platform
reboot/recreation and external schedule registration remain unverified; rootfs
and runtime data survive platform replacement only if the platform preserves
or restores their installation trees with ownership and metadata.

See [operations](OPERATIONS.md), [rebuild instructions](FROM-SCRATCH.md) and
[qualification evidence](BUILD-RECORD.md).
