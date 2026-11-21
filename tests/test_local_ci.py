"""tools/local_ci.py: the local gate that stands in for remote CI (Actions minutes are the free tier)."""
import importlib.util
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('local_ci',ROOT/'tools/local_ci.py');local_ci=importlib.util.module_from_spec(spec);spec.loader.exec_module(local_ci)
WEB=['web-typecheck','web-test','web-build','web-size']


def test_docs_only_change_runs_only_the_manifest_check():
    assert local_ci.plan(['docs/LAB_DAY.md','README.md','reports/rehearsal/README.md','SOURCE_MANIFEST.json'])==['manifest']


def test_python_change_runs_the_python_suite_and_no_web_stage():
    assert local_ci.plan(['core/pipeline.py','tests/test_x.py'])==['manifest','python']


def test_web_change_runs_the_web_stages_and_no_python_suite():
    assert local_ci.plan(['web/src/App.tsx'])==['manifest',*WEB]


def test_e2e_is_opt_in_and_only_with_web_stages():
    assert local_ci.plan(['web/src/App.tsx'],e2e=True)==['manifest',*WEB,'e2e-mock']
    assert local_ci.plan(['core/pipeline.py'],e2e=True)==['manifest','python']


def test_workflow_or_lock_change_runs_everything():
    for path in ('.github/workflows/ci.yml','requirements-windows.lock','web/package-lock.json'):
        assert local_ci.plan([path])==['manifest','python',*WEB],path


def test_all_forces_every_stage():
    assert local_ci.plan([],run_all=True)==['manifest','python',*WEB]
    assert local_ci.plan([],run_all=True,e2e=True)==['manifest','python',*WEB,'e2e-mock']


def test_dry_run_prints_the_plan_and_runs_nothing(capsys,monkeypatch):
    monkeypatch.setattr(local_ci,'changed_files',lambda base:['core/pipeline.py'])
    monkeypatch.setattr(local_ci,'run_stage',lambda name:(_ for _ in ()).throw(AssertionError('ran '+name)))
    assert local_ci.main(['--dry-run'])==0
    out=capsys.readouterr().out
    assert 'manifest' in out and 'python' in out and 'web-build' not in out


def test_linux_stage_is_opt_in_and_runs_after_the_windows_suite():
    assert local_ci.plan(['core/pipeline.py'],linux=True)==['manifest','python','linux']
    assert local_ci.plan(['docs/LAB_DAY.md'],linux=True)==['manifest']
    assert local_ci.plan([],run_all=True,linux=True)==['manifest','python','linux',*WEB]


def test_windows_paths_map_to_wsl_mounts():
    assert local_ci.wsl_path(r'C:\Users\user\Desktop\Aethertwin')=='/mnt/c/Users/user/Desktop/Aethertwin'
    assert local_ci.wsl_path('D:/data/x.sh')=='/mnt/d/data/x.sh'
