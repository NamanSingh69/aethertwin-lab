"""AIDE2 C33: progress.json was written in place inside the per-frame try, so a Windows file lock (antivirus, indexer,
an editor) turned into a frame error, and a half-written file could be read. It is now written atomically
(temporary file + os.replace) and a failure is counted, never raised."""
import json,os
from command_center import runtime


def test_progress_is_written_atomically_and_completely(tmp_path):
    path=tmp_path/'progress.json'
    assert runtime.write_progress(path,{'frames':3})
    assert json.loads(path.read_text(encoding='utf-8'))=={'frames':3}
    assert [p.name for p in tmp_path.iterdir()]==['progress.json']


def test_a_locked_target_is_reported_not_raised_and_leaves_no_temporary_file(tmp_path,monkeypatch):
    path=tmp_path/'progress.json';path.write_text('{"frames": 1}',encoding='utf-8')
    def locked(src,dst):raise PermissionError(13,'The process cannot access the file')
    monkeypatch.setattr(runtime.os,'replace',locked)
    assert runtime.write_progress(path,{'frames':2}) is False
    assert json.loads(path.read_text(encoding='utf-8'))=={'frames':1}
    assert [p.name for p in tmp_path.iterdir()]==['progress.json']
