# Direct Debian demo notes

The direct Debian environment demonstrates booting systemd as namespace PID1
without replacing the outer platform's init. systemd-nspawn starts a native
Debian filesystem, and its controller enters that guest to run commands.
Outer runit owns its lifetime wrapper and recovers unexpected exits.

This approach is useful for demonstrating namespaces, native package persistence
and startup recovery. For creating and managing ordinary application containers,
use Incus: its CLI provides instance lifecycle, images, profiles, cloning and
snapshots. Disable the direct demo when it is unnecessary.

Each filesystem keeps its own native accounts, homes, APT database, configuration
and persistent journals. The outer tools filesystem provides independent SSH
and Tailscale access, allowing both environments to be stopped without moving
those access services into an application guest.

The direct demo defaults to identity-mapped private users with TUN disabled.
The trusted Incus manager deliberately uses its separate nesting-capable
profile. Do not copy that manager's privileges into application containers.

Start with FROM-SCRATCH.md, select environments with CONFIGURATION.md, and use
OPERATIONS.md for daily commands. BUILD-RECORD.md describes qualification and
its limits without publishing deployment identities or private topology.
