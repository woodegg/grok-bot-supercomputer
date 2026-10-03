# Optional migration of existing native guest access

Fresh installations should follow FROM-SCRATCH.md and generate their own SSH
and Tailscale identities. This migration is only for an existing native Debian
guest whose SSH and Tailscale services are both enabled and whose identity the
operator explicitly wants to preserve in the outer access layer.

The public paths/account are generic defaults. Review scripts/migrate-access.py
and align its source, destination and runtime paths with the real installation.
The destination SSH configuration and account must match the authorized-key
filenames and account identity being migrated. Do not run it on unrelated data.

After provisioning the native host-tools role and installing controls:

```sh
sudo /workspace/debian-runtime/bin/debianctl start
sudo /workspace/infrastructure-runtime/bin/infractl start
sudo scripts/migrate-access.py
```

The script stops guest access units before copying the ED25519 host key,
authorized keys and flushed Tailscale state into the outer tools filesystem.
It applies private permissions, enables outer access and verifies readiness.
A successful sentinel prevents stale state from being recopied. On failure it
disables outer access and re-enables the original guest units. A private backup
remains under infrastructure-runtime/state/access-migration-backup; inspect it
before any --retry and remove it only after qualification and intentional
cleanup. It must never enter Git.

Compare the original host fingerprint privately, verify a fresh SSH connection,
and check platform connectivity and Tailscale preferences. Daemon readiness is
not tailnet authentication. Preserve disabled states and use an independent
recovery path for network changes. Keep a private deployment record outside the
source checkout. Obsolete host-specific prefix migration tools are not shipped.
