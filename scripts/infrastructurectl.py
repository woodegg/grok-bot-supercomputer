#!/usr/bin/python3
"""Persistent outer runit supervisor and independent environment controls."""
import argparse
import fcntl
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
import tomllib
from lifecycle_events import EventLog, process_ticks, show_events

DEFAULT=Path('/srv/container-infrastructure/infrastructure-runtime/etc/config.toml')

def configuration(path):
    data=tomllib.loads(path.read_text())
    for field in ('runtime','host_rootfs'):
        p=Path(data[field])
        if not p.is_absolute() or str(p) in ('/','/srv/container-infrastructure'):
            raise ValueError('Dedicated absolute '+field+' required')
        data[field]=p.resolve()
    for name, env in data['environments'].items():
        if name not in ('debian','incus'):
            raise ValueError('Unknown environment')
        env['config']=Path(env['config'])
        env['controller_dir']=Path(env['controller_dir'])
    return data

def valid_pid(record, required):
    try:
        pid,ticks=record.read_text().split()
        pid=int(pid)
        if process_ticks(pid)!=ticks:
            return None
        argv=Path(f'/proc/{pid}/cmdline').read_bytes().split(b'\0')
        match=any(required.encode()==arg for arg in argv)
        if required=='/usr/sbin/sshd':
            match=match or any(arg.startswith(b'sshd: /usr/sbin/sshd ') and b'[listener]' in arg for arg in argv)
        return pid if match else None
    except (ValueError,OSError):
        return None

def supervisor(config):
    root=config['host_rootfs']
    return valid_pid(config['runtime']/'state/supervisor.pid',str(root/'usr/bin/runsvdir'))

def call_sv(config,*args,check=True):
    return subprocess.run([str(config['runtime']/'bin/sv'),*args],text=True,check=check)

def env_config(config,name):
    row=config['environments'][name]
    sys.path.insert(0,str(row['controller_dir']))
    import containerctl
    return containerctl,containerctl.configuration(row['config'])

def shutdown(config,events):
    runtime=config['runtime']
    pid=supervisor(config)
    if pid:
        # HUP makes runsvdir send TERM to its own runsv children and exit.
        os.kill(pid,signal.SIGHUP)
    group_record=runtime/'state/cgroup.path'
    if group_record.exists():
        group=Path(group_record.read_text().strip())
        if group.name!='container-infrastructure' or not group.is_relative_to('/sys/fs/cgroup'):
            raise RuntimeError('Invalid supervisor cgroup record')
        if not pid and group.exists() and 'populated 1' in (group/'cgroup.events').read_text():
            # A killed scanner can leave its supervised services alive.
            for service in (runtime/'services').iterdir():
                call_sv(config,'exit',str(service),check=False)
        deadline=time.monotonic()+60
        while group.exists() and 'populated 1' in (group/'cgroup.events').read_text():
            if time.monotonic()>deadline:
                raise RuntimeError('Owned services did not drain; no forced kill issued')
            time.sleep(.2)
        if group.exists():
            from containerctl import prune_empty_cgroup
            prune_empty_cgroup(group)
    (runtime/'state/supervisor.pid').unlink(missing_ok=True)
    events.emit('supervisor_stopped')

def startup(config,config_path,events,automatic):
    runtime=config['runtime']
    if automatic and (runtime/'state/disabled').exists():
        events.emit('startup_skipped',reason='supervisor_persistently_disabled')
        return 'skipped_disabled'
    if supervisor(config):
        events.emit('supervisor_already_running',manager_pid=supervisor(config))
        return 'already_running'
    shutdown(config,events)
    if not automatic:
        (runtime/'state/disabled').unlink(missing_ok=True)
    relative=next(line.split('::')[1] for line in Path('/proc/self/cgroup').read_text().splitlines() if line.startswith('0::'))
    group=Path('/sys/fs/cgroup')/relative.lstrip('/')/'container-infrastructure'
    if group.exists():
        raise RuntimeError('Refusing existing unowned infrastructure cgroup')
    group.mkdir()
    group.chmod(0o755)
    (runtime/'state/cgroup.path').write_text(str(group)+'\n')
    log=runtime/'state/supervisor.log'
    if log.exists() and log.stat().st_size>1024*1024:
        log.replace(log.with_suffix('.log.1'))
    with log.open('a') as output:
        process=subprocess.Popen([sys.executable,str(runtime/'libexec/infrastructurectl.py'),
            '--config',str(config_path),'_worker',str(group)],stdin=subprocess.DEVNULL,
            stdout=output,stderr=subprocess.STDOUT,start_new_session=True)
    (runtime/'state/supervisor.pid').write_text(f'{process.pid} {process_ticks(process.pid)}\n')
    deadline=time.monotonic()+8
    while time.monotonic()<deadline:
        if supervisor(config):
            events.emit('supervisor_started',manager_pid=process.pid)
            return 'started'
        if process.poll() is not None:
            raise RuntimeError('Supervisor exited; inspect supervisor.log')
        time.sleep(.1)
    raise RuntimeError('Supervisor readiness timed out')

def worker(config,group):
    (group/'cgroup.procs').write_text(str(os.getpid()))
    runtime,root=config['runtime'],config['host_rootfs']
    loader=root/'lib64/ld-linux-x86-64.so.2'
    argv=[str(loader),'--library-path',str(root/'usr/lib/x86_64-linux-gnu'),
          str(root/'usr/bin/runsvdir'),str(runtime/'services')]
    os.execve(str(loader),argv,{'PATH':str(runtime/'bin')+':/usr/sbin:/usr/bin:/sbin:/bin','LANG':'C.UTF-8'})

def host_exec(config,name,command):
    expected='/usr/sbin/sshd' if name=='sshd' else str(config['runtime']/'libexec/host-service.py')
    pid=valid_pid(config['runtime']/'state'/(name+'.pid'),expected)
    if not pid:
        raise RuntimeError('Host service is stopped: '+name)
    actual=Path(f'/proc/{pid}/root').stat()
    expected_root=config['host_rootfs'].stat()
    if (actual.st_dev,actual.st_ino)!=(expected_root.st_dev,expected_root.st_ino):
        raise RuntimeError('Host service root identity mismatch')
    return subprocess.run(['/usr/bin/nsenter','--target',str(pid),'--mount','--root','--wd',
        '/usr/bin/env','-i','PATH=/usr/sbin:/usr/bin:/sbin:/bin','HOME=/root','LANG=C.UTF-8',*command]).returncode

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config',type=Path,default=DEFAULT)
    parser.add_argument('--trigger',choices=('manual','scheduled','bootstrap','host-startup'),default='manual')
    parser.add_argument('action',choices=('startup','start','stop','restart','status','events','enable','disable','service-restart','host-exec','_worker'))
    parser.add_argument('arguments',nargs=argparse.REMAINDER)
    args=parser.parse_args()
    if os.geteuid()!=0:
        os.execv('/usr/bin/sudo',['sudo','-n',sys.executable,str(Path(__file__).resolve()),*sys.argv[1:]])
    # Controls from an outer SSH chroot must execute in the platform mount/root.
    if Path('/').stat().st_ino!=Path('/proc/1/root').stat().st_ino or Path('/').stat().st_dev!=Path('/proc/1/root').stat().st_dev:
        os.execv('/usr/bin/nsenter',['nsenter','--target','1','--mount','--root','--wd',
            '/usr/bin/python3',str(Path(__file__).resolve()),*sys.argv[1:]])
    os.umask(0o077)
    config=configuration(args.config)
    runtime=config['runtime']
    sys.path.insert(0,str(config['environments']['debian']['controller_dir']))
    if args.action=='_worker':
        worker(config,Path(args.arguments[0]));return
    if args.action=='events':
        show_events(runtime/'state',int(args.arguments[0]) if args.arguments else 20);return
    if args.action=='host-exec':
        name=args.arguments[0]
        if name not in ('sshd','tailscaled'):
            raise ValueError('Unknown host daemon')
        command=args.arguments[1:]
        if command[:1]==['--']:command=command[1:]
        raise SystemExit(host_exec(config,name,command))
    if args.action=='status':
        print('Supervisor:',supervisor(config) or 'stopped', '; enabled=',not (runtime/'state/disabled').exists())
        for service in sorted((runtime/'services').iterdir()):
            print(service.name,'disabled' if (service/'down').exists() else 'enabled',flush=True)
            if supervisor(config):call_sv(config,'status',str(service),check=False)
            if service.name in config['environments']:
                ctl,cfg=env_config(config,service.name)
                print('  guest manager=',ctl.manager_pid(cfg),'init=',ctl.init_pid(cfg),flush=True)
        return
    log_config={'state_dir':runtime/'state','machine':'container-infrastructure'}
    events=EventLog(log_config,args.action,args.trigger)
    events.emit('invocation_started')
    try:
        events.observe_environment()
        with (runtime/'state/control.lock').open('a') as lock:
            fcntl.flock(lock,fcntl.LOCK_EX)
            if args.action in ('start','startup'):
                outcome=startup(config,args.config,events,args.action=='startup')
            elif args.action in ('stop','restart'):
                if args.action=='stop':(runtime/'state/disabled').touch(mode=0o600)
                elif (runtime/'state/disabled').exists():raise RuntimeError('Supervisor disabled; use start')
                shutdown(config,events)
                outcome='stopped' if args.action=='stop' else startup(config,args.config,events,True)
            else:
                name=args.arguments[0]
                if name not in ('debian','incus','sshd','tailscaled'):
                    raise ValueError('Unknown managed service')
                service=runtime/'services'/name
                if args.action=='disable':
                    (service/'down').touch(mode=0o600)
                    if name in config['environments']:
                        ctl,cfg=env_config(config,name);(cfg['state_dir']/'disabled').touch(mode=0o600)
                    if supervisor(config):call_sv(config,'-w','60','down',str(service))
                    if name in config['environments']:
                        ctl.stop(cfg,persistent=True)
                elif args.action=='enable':
                    if name in config['environments']:
                        ctl,cfg=env_config(config,name);(cfg['state_dir']/'disabled').unlink(missing_ok=True)
                    (service/'down').unlink(missing_ok=True)
                    if supervisor(config):call_sv(config,'up',str(service))
                else:
                    if (service/'down').exists():raise RuntimeError('Service disabled; enable explicitly')
                    call_sv(config,'-w','60','restart',str(service))
                outcome=args.action+'_'+name
                events.emit('service_configured',service=name,outcome=outcome)
        print(outcome)
        events.finish(outcome)
    except Exception as error:
        events.finish('failed',error)
        raise

if __name__=='__main__':
    try:main()
    except (OSError,ValueError,RuntimeError,subprocess.SubprocessError) as error:
        raise SystemExit('infrastructurectl: '+str(error))
