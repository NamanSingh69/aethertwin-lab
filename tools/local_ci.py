"""Local CI gate: the checks of .github/workflows/ci.yml, run on this laptop before a push.

GitHub Actions minutes are the free tier (2,000 a month; a Windows minute bills as two on this private repo), so
the gate is local and remote CI runs only when it is really needed (see the header of ci.yml).

    python tools/local_ci.py               # the stages the changed files need (vs origin/main), then a summary
    python tools/local_ci.py --dry-run     # print the plan only
    python tools/local_ci.py --all --e2e   # every stage plus the mocked Playwright suite (before a merge)
    python tools/local_ci.py --linux       # also the Ubuntu suite in WSL (tools/local_ci_linux.sh; replaces the CI runner)

Stages: manifest (repository_manifest.py --check: run it after committing), python (tools/fast_check.py --full),
web-typecheck, web-test, web-build (and the committed web/dist must match the fresh build), web-size, e2e-mock
(opt-in). Docs-only changes run the manifest check alone. Exit code 0 only when every stage passed.
"""
from __future__ import annotations
import argparse,shutil,subprocess,sys,time
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
WEB=['web-typecheck','web-test','web-build','web-size']
DOCS_PREFIXES=('docs/','reports/','artifacts/');IGNORED_PREFIXES=('.claude/',)
EVERYTHING=('.github/','requirements-','pyproject.toml','web/package.json','web/package-lock.json')


def plan(changed,e2e=False,run_all=False,linux=False):
    """Stages for the changed paths (repo-relative, forward slashes). None means unknown: run everything."""
    python=web=run_all or changed is None
    for path in changed or []:
        if path.startswith(IGNORED_PREFIXES):continue
        if path.startswith(EVERYTHING):python=web=True
        elif path.startswith(DOCS_PREFIXES) or path.endswith('.md') or path=='SOURCE_MANIFEST.json':continue
        elif path.startswith('web/'):web=True
        else:python=True
    stages=['manifest']+(['python']+(['linux'] if linux else []) if python else [])+(WEB if web else [])
    return stages+(['e2e-mock'] if e2e and web else [])


def _git(*args):
    done=subprocess.run(['git',*args],cwd=ROOT,capture_output=True,text=True)
    if done.returncode:raise RuntimeError(done.stderr.strip())
    return [line for line in done.stdout.splitlines() if line]


def changed_files(base):
    """Committed and uncommitted changes since the merge base with `base`, plus untracked files; None if git fails."""
    try:
        merge_base=_git('merge-base','HEAD',base)[0]
        return sorted(set(_git('diff','--name-only',merge_base))|set(_git('ls-files','--others','--exclude-standard')))
    except (RuntimeError,IndexError,OSError):return None


def wsl_path(path):
    """C:\\a\\b or C:/a/b -> /mnt/c/a/b (WSL's mount of a Windows drive)."""
    text=str(path).replace('\\','/')
    return f'/mnt/{text[0].lower()}{text[2:]}' if len(text)>1 and text[1]==':' else text


def _python():
    for candidate in (ROOT/'.venv/Scripts/python.exe',ROOT/'.venv/bin/python'):
        if candidate.exists():return str(candidate)
    return sys.executable


def _commands(name):
    npm=shutil.which('npm') or 'npm';web=ROOT/'web'
    return {'manifest':[([_python(),'tools/repository_manifest.py','--check'],ROOT)],
        'python':[([_python(),'tools/fast_check.py','--full'],ROOT)],
        'linux':[(['wsl.exe','-d','Ubuntu','-e','bash',wsl_path(ROOT/'tools/local_ci_linux.sh'),wsl_path(ROOT)],ROOT)],
        'web-typecheck':[([npm,'run','typecheck'],web)],'web-test':[([npm,'test'],web)],
        'web-build':[([npm,'run','build'],web),(['git','diff','--exit-code','--stat','--','dist','reports/production-startup.json'],web),
                     (['git','ls-files','--others','--exclude-standard','--','dist'],web)],
        'web-size':[([npm,'run','size'],web)],'e2e-mock':[([npm,'run','e2e:mock'],web)]}[name]


def run_stage(name):
    """Run one stage; returns (passed, last output lines)."""
    for k,(command,cwd) in enumerate(_commands(name)):
        done=subprocess.run(command,cwd=cwd,capture_output=True,text=True,errors='replace')
        output=(done.stdout+done.stderr).strip().splitlines()
        # The last web-build command lists untracked build output; there must be none.
        if name=='web-build' and k==2 and output:return False,['untracked build output in web/dist:',*output[:10]]
        if done.returncode:return False,output[-25:]
    return True,output[-1:] if output else []


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__,formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('--base',default='origin/main',help='compare against the merge base with this ref (default origin/main)')
    p.add_argument('--all',action='store_true',help='every stage, whatever changed');p.add_argument('--e2e',action='store_true',help='add the mocked Playwright suite')
    p.add_argument('--linux',action='store_true',help='also run the Ubuntu suite in WSL (tools/local_ci_linux.sh)')
    p.add_argument('--dry-run',action='store_true',help='print the plan and run nothing')
    a=p.parse_args(argv);changed=None if a.all else changed_files(a.base);stages=plan(changed,a.e2e,a.all,a.linux)
    scope='all files' if a.all else (f'{len(changed)} changed file(s) vs {a.base}' if changed is not None else 'changes unknown (git failed): everything')
    print(f'local_ci plan ({scope}): {" ".join(stages)}',flush=True)
    if a.dry_run:return 0
    results=[]
    for name in stages:
        started=time.perf_counter();ok,lines=run_stage(name);seconds=time.perf_counter()-started;results.append((name,ok,seconds))
        print(f'{"PASS" if ok else "FAIL"} {name} ({seconds:.0f} s)'+(f': {lines[-1]}' if ok and lines else ''),flush=True)
        if not ok:print('\n'.join('    '+line for line in lines),flush=True)
    failed=[name for name,ok,_ in results if not ok]
    print(f'local_ci: {"FAIL "+" ".join(failed) if failed else "PASS"} ({len(results)} stage(s), {sum(s for *_,s in results):.0f} s)')
    return 1 if failed else 0


if __name__=='__main__':raise SystemExit(main())
