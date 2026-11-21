"""Stage a built showcase site for an upload-only Vercel deploy. Deploys nothing.

Usage: python tools/stage_showcase.py [--build web/showcase-dist] [--previous <last staging folder>] [--out <folder>]
         [--allow-test-export] [--visitors-per-month 300] [--views-per-visit 2]

Steps (docs/SHOWCASE.md sections 3 and 5): refuse a build whose replay is the 7-frame TEST EXPORT fixture (the e2e
run overwrites showcase-dist with it), run tools/predeploy_check.py (any FAIL stops here), copy the build into a new
folder (default ~/aethertwin_demo/vercel_showcase_<YYYYMMDD>), copy .vercel/ and .gitignore from --previous so the
folder stays linked to the existing project, and write vercel.json from web/vercel.json with outputDirectory removed
and buildCommand/installCommand set to "" (nothing is built on Vercel). It then prints the remaining manual steps:
the firewall check and `vercel deploy --prod --yes`, which only the owner runs.
"""
import argparse,datetime,json,shutil,subprocess,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
TEST_EXPORT_MARK='TEST EXPORT'

def replay_note(build):
    index=Path(build)/'showcase'/'index.json'
    if not index.is_file(): raise SystemExit(f'No replay index at {index}: build with SHOWCASE_DATA_DIR set to a real export (docs/SHOWCASE.md section 2).')
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
    ap.add_argument('--visitors-per-month',default='300');ap.add_argument('--views-per-visit',default='2');ap.add_argument('--skip-gate',action='store_true',help=argparse.SUPPRESS)
    a=ap.parse_args(argv);note,index=replay_note(a.build)
    if TEST_EXPORT_MARK in note and not a.allow_test_export: raise SystemExit('showcase-dist holds the TEST EXPORT fixture (the e2e run rebuilds it). Rebuild with the real export: docs/SHOWCASE.md section 2.')
    check_paths(a.build,a.out,a.previous)
    print(f"replay: {index.get('frame_count')} frames, {index.get('duration_s')} s, recorded {index.get('recorded_at')}")
    if not a.skip_gate:
        gate=subprocess.run([sys.executable,str(ROOT/'tools'/'predeploy_check.py'),a.build,'--vercel-json',a.vercel_json,'--visitors-per-month',a.visitors_per_month,'--views-per-visit',a.views_per_visit])
        if gate.returncode: raise SystemExit('Pre-deploy gate failed: do not deploy.')
    out=stage(a.build,a.out,a.vercel_json,a.previous);linked=(out/'.vercel').is_dir()
    print(f'\nStaged {out}\nRemaining, by the owner only:\n 1. Vercel dashboard, project aethertwin-showcase: firewall has the 300/min per-IP rate limit and bot protection on; the team is not blocked.')
    print(f' 2. In PowerShell or cmd, one line at a time:\n      cd /d "{out}"   (PowerShell: Set-Location "{out}")\n      vercel deploy --prod --yes')
    if not linked: print('    Not linked yet: pass --previous <last staging folder>, or run `vercel link` to the EXISTING project aethertwin-showcase; never create a new one.')
    print(' 3. In a real browser: / loads without replay data, /replay plays, /explainer.html?x=1 keeps its query, an unknown path is 404.')
    return 0

if __name__=='__main__': sys.exit(main())
