"""Bounded subprocess benchmarks on NEW synthetic targets only. No URL/target option."""
import argparse
import ctypes
import json
import os
import signal
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
import time
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from benchmarks.t17 import limits,PROFILES,rss,disk_bytes


def _terminate_windows_worker(pid):
    """Wait for our worker, not just the venv redirector, to release its files."""
    from ctypes import wintypes
    kernel=ctypes.WinDLL('kernel32',use_last_error=True)
    kernel.OpenProcess.argtypes=[wintypes.DWORD,wintypes.BOOL,wintypes.DWORD]
    kernel.OpenProcess.restype=wintypes.HANDLE
    kernel.WaitForSingleObject.argtypes=[wintypes.HANDLE,wintypes.DWORD]
    kernel.WaitForSingleObject.restype=wintypes.DWORD
    kernel.CloseHandle.argtypes=[wintypes.HANDLE]
    # Hold a synchronization handle before termination; PID reuse cannot redirect
    # this wait to an unrelated process. No process enumeration or shell kill.
    handle=kernel.OpenProcess(0x00100000,False,pid)
    if not handle:
        if ctypes.get_last_error()==87:return  # Worker already exited.
        raise RuntimeError('Cannot wait for owned benchmark worker.')
    try:
        try:os.kill(pid,signal.SIGTERM)
        except ProcessLookupError:pass
        if kernel.WaitForSingleObject(handle,10000)!=0:
            raise RuntimeError('Owned benchmark worker did not terminate in time.')
    finally:kernel.CloseHandle(handle)


def terminate_tree(process):
    if os.name=='nt':
        # Kill the actual owned worker first: taskkill /T can leave a venv
        # redirector waiting with the database still open on managed Windows.
        actual_pid=getattr(process,'_t17_worker_pid',None)
        if actual_pid and actual_pid!=process.pid:
            _terminate_windows_worker(actual_pid)
        if process.poll() is None:process.kill()
    else:process.terminate()
    process.wait(timeout=15)


def guarded(profile,config):
    limits(**config)
    if profile not in PROFILES:raise ValueError('Refusing unknown/non-synthetic target')
    with TemporaryDirectory(prefix='inventraiq-t17-') as temp:
        root=Path(temp);control=root/'control.json';result_path=root/'result.json'
        control.write_text(json.dumps(dict(purpose='new-synthetic-t17-only',profile=profile,limits=config)),encoding='utf-8')
        environment=dict(os.environ,APP_ENV='testing',DATABASE_URL='sqlite:///:memory:',PYTHON_DOTENV_DISABLED='1',
            PYTHONDONTWRITEBYTECODE='1',OPENBLAS_NUM_THREADS='1',OMP_NUM_THREADS='1',PYTHONIOENCODING='utf-8')
        start=time.perf_counter();peak=disk_peak=0;stopped=None
        # Private logs never leave the temporary directory. Errors are classified
        # by exit/exception type; raw requests/credentials/tracebacks aren't published.
        with (root/'worker.log').open('wb') as log:
            process=subprocess.Popen([sys.executable,str(Path(__file__).resolve()),'--worker',str(control)],
                env=environment,stdout=log,stderr=log)
            try:
                while process.poll() is None:
                    memory=rss(process.pid)
                    worker_pid_path=root/'worker.pid'
                    if worker_pid_path.is_file():
                        actual_pid=int(worker_pid_path.read_text(encoding='ascii'))
                        process._t17_worker_pid=actual_pid
                        if actual_pid!=process.pid:memory['peak']+=rss(actual_pid)['peak']
                    peak=max(peak,memory['peak']);disk_peak=max(disk_peak,disk_bytes(root))
                    elapsed=time.perf_counter()-start
                    if elapsed>config['seconds']:stopped='wall_time_limit'
                    elif peak>config['memory_mb']*1024*1024:stopped='rss_limit'
                    elif disk_peak>config['disk_mb']*1024*1024:stopped='temporary_disk_limit'
                    if stopped:terminate_tree(process);break
                    time.sleep(.2)
            finally:
                if process.poll() is None:terminate_tree(process)
        envelope=dict(profile=profile,wall_seconds=round(time.perf_counter()-start,3),peak_rss_bytes=peak,
            sampled_peak_temporary_bytes=disk_peak,exit_code=process.returncode,resource_stop=stopped,
            resource_sampling_ms=200,limits=config)
        if result_path.is_file():envelope['result']=json.loads(result_path.read_text(encoding='utf-8'))
        partial=root/'partial.json'
        if (process.returncode or stopped) and partial.is_file():envelope['partial']=json.loads(partial.read_text(encoding='utf-8'))
        if process.returncode or stopped:envelope['status']='INCOMPLETE'
        else:envelope['status']='COMPLETED'
        return envelope


def worker(path):
    path=Path(path)
    if path.name!='control.json' or path.is_symlink() or not path.is_file() or path.stat().st_size>4096:
        raise ValueError('Invalid new-target control')
    parent=path.parent
    if not parent.name.startswith('inventraiq-t17-') or parent.resolve()!=parent.absolute():raise ValueError('Invalid worker directory')
    value=json.loads(path.read_text(encoding='utf-8'))
    if set(value)!={'purpose','profile','limits'} or value['purpose']!='new-synthetic-t17-only':raise ValueError('Invalid control contract')
    with (parent/'worker.pid').open('x',encoding='ascii') as handle:handle.write(str(os.getpid()))
    from benchmarks.t17 import execute
    try:
        result=execute(parent,value['profile'],value['limits'])
    except Exception as exc:
        # Publish classification, not an exception message possibly containing SQL.
        import traceback
        frames=[dict(file=Path(f.filename).name,line=f.lineno,function=f.name) for f in traceback.extract_tb(exc.__traceback__)[-6:]]
        (parent/'result.json').write_text(json.dumps(dict(error_type=type(exc).__name__,status='FAILED',frames=frames)),encoding='utf-8')
        raise SystemExit(1)
    (parent/'result.json').write_text(json.dumps(result,indent=2),encoding='utf-8')


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--worker',help=argparse.SUPPRESS)
    parser.add_argument('--profiles',nargs='+',choices=list(PROFILES),default=list(PROFILES))
    parser.add_argument('--samples',type=int,default=20);parser.add_argument('--rounds',type=int,default=2)
    parser.add_argument('--workers',type=int,default=25);parser.add_argument('--seconds',type=int,default=300)
    parser.add_argument('--memory-mb',type=int,default=1536);parser.add_argument('--disk-mb',type=int,default=512)
    parser.add_argument('--output')
    args=parser.parse_args()
    if args.worker:return worker(args.worker)
    if not args.output:parser.error('--output is required')
    output=Path(args.output)
    if output.suffix!='.json' or output.exists() or not output.parent.is_dir():parser.error('Use a new JSON output file in an existing directory')
    try:config=limits(args.samples,args.rounds,args.workers,args.seconds,args.memory_mb,args.disk_mb)
    except ValueError as exc:parser.error(str(exc))
    result=dict(schema_version='inventraiq-t17-envelope-v1',profiles=[])
    for profile in args.profiles:
        evidence=guarded(profile,config);result['profiles'].append(evidence)
        # Durable partial evidence if a later workload hits a resource gate.
        output.write_text(json.dumps(result,indent=2)+'\n',encoding='utf-8')
        print(json.dumps({k:evidence[k] for k in ('profile','status','wall_seconds','peak_rss_bytes','resource_stop')}),flush=True)
    if any(p['status']!='COMPLETED' for p in result['profiles']):raise SystemExit(1)


if __name__=='__main__':main()
