"""Stage a built showcase site for an upload-only Vercel deploy. Deploys nothing.

Usage: python tools/stage_showcase.py [--build web/showcase-dist] [--previous <last staging folder>] [--out <folder>]
         [--data-dir <export> --data-base <https URL>] [--allow-test-export] [--visitors-per-month 300] [--views-per-visit 2]

Replay data hosted elsewhere (docs/SHOWCASE.md section 4, option A): build with VITE_SHOWCASE_BASE=<data base>, then pass
--data-dir (the export folder, for the TEST EXPORT check) and --data-base (the same URL). The build must then carry no
showcase/ folder, its code must name that base, and web/vercel.json's connect-src must admit that origin.

Steps (docs/SHOWCASE.md sections 3 and 5): refuse a build whose replay is the 7-frame TEST EXPORT fixture (the e2e
run overwrites showcase-dist with it), run tools/predeploy_check.py (any FAIL stops here), copy the build into a new
folder (default ~/aethertwin_demo/vercel_showcase_<YYYYMMDD>), copy .vercel/ and .gitignore from --previous so the
folder stays linked to the existing project, and write vercel.json from web/vercel.json with outputDirectory removed
and buildCommand/installCommand set to "" (nothing is built on Vercel). It then prints the remaining manual steps:
the firewall check and `vercel deploy --prod --yes`, which only the owner runs.
"""
import argparse,datetime,json,re,shutil,subprocess,sys
from pathlib import Path
from urllib.parse import urlsplit
ROOT=Path(__file__).resolve().parents[1]
TEST_EXPORT_MARK='TEST EXPORT'

def replay_note(build):
    index=Path(build)/'showcase'/'index.json'
    if not index.is_file(): raise SystemExit(f'No replay index at {index}: build with SHOWCASE_DATA_DIR set to a real export (docs/SHOWCASE.md section 2).')
    data=json.loads(index.read_text(encoding='utf-8'));return str((data.get('source') or {}).get('note','')),data

def offsite_note(build,data_dir,data_base,vercel_json):
    """Checks for a build that loads its replay from data_base; returns (note, index) read from the export folder."""
    url=urlsplit(data_base);build=Path(build)
    if url.scheme!='https' or not url.netloc: raise SystemExit(f'--data-base must be an https URL, got {data_base!r}.')
    if (build/'showcase').exists(): raise SystemExit(f'{build} still holds showcase/: build with VITE_SHOWCASE_BASE set so the replay data stays off this host.')
    index=Path(data_dir)/'index.json'
    if not index.is_file(): raise SystemExit(f'No replay index at {index}: --data-dir must be the export folder that was published to {data_base}.')
    code=b''.join(f.read_bytes() for f in (build/'assets').rglob('*.js')) if (build/'assets').is_dir() else b''
    if data_base.encode() not in code: raise SystemExit(f'The build does not load its replay from {data_base}: rebuild with VITE_SHOWCASE_BASE={data_base}.')
    config=json.loads(Path(vercel_json).read_text(encoding='utf-8'));origin=f'{url.scheme}://{url.netloc}'
    csp=' '.join(h['value'] for rule in config.get('headers',[]) for h in rule.get('headers',[]) if h.get('key','').lower()=='content-security-policy')
    connect=(re.search(r'connect-src([^;]*)',csp) or [None,''])[1].split()
    if origin not in connect: raise SystemExit(f'connect-src in {vercel_json} does not admit {origin}; the replay could not load.')
    data=json.loads(index.read_text(encoding='utf-8'));return str((data.get('source') or {}).get('note','')),data

def staged_vercel_json(source):
    config=json.loads(Path(source).read_text(encoding='utf-8'));config.pop('outputDirectory',None);config['buildCommand']='';config['installCommand']='';return config

def check_paths(build,out,previous=None):
    """All refusals happen before anything is copied, so a mistake never leaves a half-staged folder."""
    build,out=Path(build),Path(out)
    if (build/'.vercel').exists(): raise SystemExit(f'{build} contains .vercel/; a build must not carry a project link.')
    if out.exists() and any(out.iterdir()): raise SystemExit(f'{out} exists and is not empty; choose a new --out.')
    if previous and not (Path(previous)/'.vercel').is_dir(): raise SystemExit(f'{previous} has no .vercel/ folder (the project link).')

def stage(build,out,vercel_json,previous=None):
    check_paths(build,out,previous);build,out=Path(build),Path(out)
    shutil.copytree(build,out,dirs_exist_ok=True)
    if previous:
        previous=Path(previous);shutil.copytree(previous/'.vercel',out/'.vercel')
        if (previous/'.gitignore').is_file(): shutil.copy2(previous/'.gitignore',out/'.gitignore')
    (out/'vercel.json').write_bytes((json.dumps(staged_vercel_json(vercel_json),indent=2)+'\n').encode('utf-8'));return out

def main(argv=None):
    ap=argparse.ArgumentParser(description=__doc__.split('\n')[0]);ap.add_argument('--build',default=str(ROOT/'web'/'showcase-dist'))
    ap.add_argument('--vercel-json',default=str(ROOT/'web'/'vercel.json'));ap.add_argument('--previous',help='last staging folder (holds .vercel/)')
    ap.add_argument('--out',default=str(Path.home()/'aethertwin_demo'/f'vercel_showcase_{datetime.date.today():%Y%m%d}'))
    ap.add_argument('--allow-test-export',action='store_true',help='stage the TEST EXPORT fixture anyway (never for the public site)')
    ap.add_argument('--data-dir',help='export folder whose copy is served at --data-base');ap.add_argument('--data-base',help='https URL the build loads its replay from')
    ap.add_argument('--visitors-per-month',default='300');ap.add_argument('--views-per-visit',default='2');ap.add_argument('--skip-gate',action='store_true',help=argparse.SUPPRESS)
    a=ap.parse_args(argv)
    if bool(a.data_dir)!=bool(a.data_base): raise SystemExit('Pass --data-dir and --data-base together.')
    note,index=offsite_note(a.build,a.data_dir,a.data_base,a.vercel_json) if a.data_base else replay_note(a.build)
    if TEST_EXPORT_MARK in note and not a.allow_test_export: raise SystemExit('showcase-dist holds the TEST EXPORT fixture (the e2e run rebuilds it). Rebuild with the real export: docs/SHOWCASE.md section 2.')
    check_paths(a.build,a.out,a.previous)
    print(f"replay: {index.get('frame_count')} frames, {index.get('duration_s')} s, recorded {index.get('recorded_at')}")
    if not a.skip_gate:
        extra=['--allow-host',urlsplit(a.data_base).hostname] if a.data_base else []
        gate=subprocess.run([sys.executable,str(ROOT/'tools'/'predeploy_check.py'),a.build,'--vercel-json',a.vercel_json,'--visitors-per-month',a.visitors_per_month,'--views-per-visit',a.views_per_visit]+extra)
        if gate.returncode: raise SystemExit('Pre-deploy gate failed: do not deploy.')
    out=stage(a.build,a.out,a.vercel_json,a.previous);linked=(out/'.vercel').is_dir()
    print(f'\nStaged {out}\nRemaining, by the owner only:\n 1. Vercel dashboard, project aethertwin-showcase: firewall has the 300/min per-IP rate limit and bot protection on; the team is not blocked.')
    print(f' 2. In PowerShell or cmd, one line at a time:\n      cd /d "{out}"   (PowerShell: Set-Location "{out}")\n      vercel deploy --prod --yes')
    if not linked: print('    Not linked yet: pass --previous <last staging folder>, or run `vercel link` to the EXISTING project aethertwin-showcase; never create a new one.')
    print(' 3. In a real browser: / loads without replay data, /replay plays, /explainer.html?x=1 keeps its query, an unknown path is 404.')
    return 0

if __name__=='__main__': sys.exit(main())
