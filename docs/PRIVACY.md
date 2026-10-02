# Publication and private deployment data

This repository contains reusable source, generic configuration examples and
sanitized technical qualification notes. Its publication history is separate
from the original deployment history. Commit authors use the contributor's
configured Git identity. Original deployment history and operator records are
not part of the publication repository.

Default paths under /srv/container-infrastructure, the operator account and
10.88.0.0/24 bridge are examples. Review and adapt them before installation;
changing source defaults does not migrate an existing deployment.

Keep real configuration, credentials, SSH host/private keys, authorized keys,
Tailscale state, Incus certificates/databases, rootfs, backups and logs outside
this checkout. Configure private network probe hosts only in the local
invocation environment. Never put endpoint inventories or secret deny-lists in
public tests. The ignore rules are a safeguard, not a substitute for reviewing
what git add includes.

Before publishing future changes, inspect tracked files, diffs and commit
metadata, and scan every reachable Git revision for credentials and identifying
host details. A clean working tree alone does not establish clean history.
Do not copy a live runtime or private operator record into documentation.

GitHub repository owner, repository name, platform activity and publication
metadata remain visible when a repository becomes public. Anonymizing deployment
data does not anonymize the contributor or the account that publishes it.
