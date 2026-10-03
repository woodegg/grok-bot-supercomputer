# Project instructions

This repository provides a direct Debian systemd-nspawn demo and an Incus
manager, with outer runit supervision and optional SSH/Tailscale access.
Recommend Incus for ordinary container creation and management.

Keep deployed rootfs, configuration, identities, credentials, logs and backups
outside Git. Read the host installation policy before installation or service
changes. The source checkout can live in a writable project directory. The
documented installed-data layout is /workspace, with each
filesystem/runtime in a dedicated directory outside the checkout. Choose
storage that the host actually preserves across recreation.

Preserve disabled states, machine identity, accounts and user data. Do not
reorganize platform processes/cgroups, change parent controllers or outer
routes/DNS, migrate services or publish without an explicit request.

Source edits do not update deployed root-owned launch copies. Review changes
before running install-controls.sh or install-infrastructure.py. Do not deploy
public example paths over an existing installation with different paths.
Document saved policy in docs/CONFIGURATION.md. Integration tests restart
services and create/remove disposable accounts and instances; explain their
impact before running against an environment with active work.

Document verification and limits in docs/BUILD-RECORD.md. Never add private
operator records to qualification evidence or commit history.
