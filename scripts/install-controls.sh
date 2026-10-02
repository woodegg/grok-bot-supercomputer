#!/bin/bash
set -euo pipefail
if [ "$(id -u)" != 0 ]; then
 exec sudo -n /usr/bin/env "RUNTIME_DIR=${RUNTIME_DIR:-/srv/container-infrastructure/debian-runtime}" /bin/bash "$0" "$@"
fi
PROJECT=$(cd -- "$(dirname -- "$0")/.." && pwd)
RUNTIME_DIR=${RUNTIME_DIR:-/srv/container-infrastructure/debian-runtime}
python3 - "$PROJECT" "$RUNTIME_DIR" <<'PY'
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import sys
import tempfile
project = Path(sys.argv[1]).resolve()
runtime = Path(sys.argv[2])
if not runtime.is_absolute() or runtime == Path('/'):
 raise SystemExit('RUNTIME_DIR must be a dedicated absolute directory')
runtime = runtime.resolve()
if runtime == project or runtime.is_relative_to(project):
 raise SystemExit('Runtime must be outside the source repository')
for folder, mode in [(runtime,0o755),(runtime/'bin',0o755),
                     (runtime/'libexec',0o755),(runtime/'etc',0o700)]:
 folder.mkdir(parents=True,exist_ok=True)
 folder.chmod(mode);os.chown(folder,0,0)
controller=runtime/'libexec/containerctl.py'
for name in ('lifecycle_events.py', 'containerctl.py'):
 with tempfile.NamedTemporaryFile(dir=runtime/'libexec',delete=False) as output:
  temporary=Path(output.name)
 try:
  shutil.copyfile(project/'scripts'/name,temporary)
  temporary.chmod(0o755);os.chown(temporary,0,0)
  temporary.replace(runtime/'libexec'/name)
 finally:
  temporary.unlink(missing_ok=True)
config=runtime/'etc/config.toml'
if not config.exists():
 text=(project/'templates/config.example.toml').read_text()
 text=re.sub(r'^state_dir\s*=.*$', lambda _: 'state_dir = '+json.dumps(str(runtime/'state')), text, flags=re.M)
 config.write_text(text)
config.chmod(0o600);os.chown(config,0,0)
quote=lambda path:shlex.quote(str(path))
scripts={
 runtime/'bin/debianctl':'exec /usr/bin/python3 '+quote(controller)+' --config '+quote(config)+' "$@"',
 runtime/'bin/tailscale':('if [ -x /srv/container-infrastructure/infrastructure-runtime/bin/infractl ]; then exec /srv/container-infrastructure/infrastructure-runtime/bin/tailscale "$@"; fi\n' if runtime==Path('/srv/container-infrastructure/debian-runtime') else '')+'exec '+quote(runtime/'bin/debianctl')+' exec -- /usr/bin/tailscale "$@"',
 runtime/'startup.sh':('if [ -x /srv/container-infrastructure/infrastructure-runtime/bin/infractl ]; then exec /srv/container-infrastructure/infrastructure-runtime/startup.sh "$@"; fi\n' if runtime==Path('/srv/container-infrastructure/debian-runtime') else '')+'exec '+quote(runtime/'bin/debianctl')+' --trigger bootstrap "$@" startup',
}
for path,command in scripts.items():
 path.write_text('#!/bin/sh\n'+command+'\n');path.chmod(0o755);os.chown(path,0,0)
# Alias supports the previous helper directory through an optional home symlink.
alias=runtime/'libexec/startup.sh'
if alias.is_symlink() and alias.readlink()==Path('../startup.sh'):
 pass
elif alias.exists() or alias.is_symlink():
 raise SystemExit('Unexpected existing startup alias: '+str(alias))
else:
 alias.symlink_to('../startup.sh')
print('Installed controls; configure external trigger: /bin/sh '+quote(runtime/'startup.sh'))
PY
