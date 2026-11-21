"""tools/rehearse_live.py guards (AIDE2 C19): one rehearsal per machine, nothing shared between runs, PID-only cleanup."""
import json,subprocess,sys,time
from pathlib import Path
import psutil
import pytest
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'tools'))
import rehearse_live as rl


def test_a_second_rehearsal_is_refused_while_the_first_holds_the_lock_and_a_stale_lock_is_taken_over(tmp_path):
    lock=tmp_path/'rehearsal.lock'
    assert rl.acquire_lock(tmp_path/'run1',lock)==lock
    with pytest.raises(RuntimeError,match='Another rehearsal is running'):rl.acquire_lock(tmp_path/'run2',lock)
    rl.release_lock(lock);assert not lock.exists()
    dead=subprocess.Popen([sys.executable,'-c','pass']);dead.wait()
    lock.write_text(json.dumps({'pid':dead.pid,'run_dir':'old'}))
    assert rl.acquire_lock(tmp_path/'run3',lock)==lock and json.loads(lock.read_text())['run_dir'].endswith('run3')
    rl.release_lock(lock)


def test_cleanup_stops_only_the_tree_it_started(tmp_path):
    sleeper=[sys.executable,'-c','import time;time.sleep(60)']
    mine=subprocess.Popen([sys.executable,'-c',f'import subprocess,time;subprocess.Popen({sleeper!r});time.sleep(60)'])
    other=subprocess.Popen(sleeper)  # another session's process with the same command line
    try:
        time.sleep(1.5);child=psutil.Process(mine.pid).children(recursive=True)
        stopped=rl.stop_tree(mine.pid)
        assert mine.pid in stopped and all(c.pid in stopped for c in child) and len(child)>=1
        assert psutil.pid_exists(other.pid) and other.poll() is None
    finally:other.kill()


def test_each_run_gets_its_own_key_ports_and_config(tmp_path):
    ports={rl.free_port() for _ in range(5)};assert len(ports)>=4
    config=rl.write_config(tmp_path,ROOT/'config/profiles/hotspot.yaml',tmp_path/'cal.json',tmp_path/'loopback.key',50123,True).read_text()
    assert 'capture_port: 50123' in config and 'parked: true' in config and 'allow_synthetic_capture: true' in config


def test_summary_reports_a_clean_run_and_a_run_that_stopped():
    def row(t,completed=False,error=None):
        return {'t':t,'rss_mb':1500.,'state':{'frame_id':t,'validity':{'localization':True},
                'diagnostics':{'processing_ms':400.,'module_ms':{'occupancy':200.},'acquisition_queue':{'acquired':10,'dropped_frames':5}}},
                'health':{'runtime':{'completed':completed,'error':error}}}
    clean=rl.summarize([row(t) for t in range(0,600,10)]+[row(600,completed=True)],600)
    assert clean['verdict'].startswith('ran the full') and clean['processing_ms']['p50']==400
    stopped=rl.summarize([row(t) for t in range(0,300,10)]+[row(300,error='RuntimeError: Continuous sensor acquisition failed')],600)
    assert stopped['verdict'].startswith('did NOT') and 'acquisition failed' in stopped['runtime_error']


def test_owns_port_recognises_this_process_tree_listener_and_not_others():
    import socket,os
    with socket.socket() as s:
        s.bind(('127.0.0.1',0));s.listen();port=s.getsockname()[1]
        assert rl.owns_port(os.getpid(),port)
        other=subprocess.Popen([sys.executable,'-c','import time;time.sleep(30)'])
        try:assert not rl.owns_port(other.pid,port)
        finally:other.kill()


def test_without_a_calibration_the_run_is_uncalibrated_on_both_ends(tmp_path):
    config=rl.write_config(tmp_path,ROOT/'config/profiles/hotspot.yaml',None,tmp_path/'loopback.key',50124,True).read_text()
    assert 'capture_bringup: true' in config and 'calibration:' not in config


def test_the_studio_url_is_printed_and_can_be_opened_for_a_demo(capsys):
    assert rl.studio_url(53317)=='http://127.0.0.1:53317/'
    try:rl.main(['--help'])
    except SystemExit:pass
    assert '--open-browser' in capsys.readouterr().out
