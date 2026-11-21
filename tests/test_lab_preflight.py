import sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'tools'))
import lab_preflight as lp


def test_exit_code_is_the_worst_status(monkeypatch,capsys):
    monkeypatch.setattr(lp,'CHECKS',(lambda:[lp.result('a','PASS','ok')],));assert lp.main([])==0
    monkeypatch.setattr(lp,'CHECKS',(lambda:[lp.result('a','PASS','ok'),lp.result('b','WARN','hm','do x')],));assert lp.main([])==1
    def broken():raise RuntimeError('boom')
    monkeypatch.setattr(lp,'CHECKS',(broken,));assert lp.main([])==2 and 'boom' in capsys.readouterr().out


def test_disk_check_thresholds(tmp_path):
    assert lp.check_disk(tmp_path,need_gb=0.001)[0]['status']=='PASS'
    assert lp.check_disk(tmp_path,need_gb=10**9)[0]['status']=='FAIL'


def test_the_capture_key_is_checked_for_presence_and_never_read(monkeypatch):
    opened=[];real=Path.read_text;real_bytes=Path.read_bytes
    def guard(self,*a,**k):
        if self.name.endswith('.key'):opened.append(self)
        return real(self,*a,**k)
    def guard_bytes(self,*a,**k):
        if self.name.endswith('.key'):opened.append(self)
        return real_bytes(self,*a,**k)
    monkeypatch.setattr(Path,'read_text',guard);monkeypatch.setattr(Path,'read_bytes',guard_bytes)
    monkeypatch.setattr(lp,'run',lambda cmd,timeout=120:(0,'640 4'))
    names=[r['check'] for r in lp.check_config()]
    assert 'Capture stream key (presence only)' in names and not opened
