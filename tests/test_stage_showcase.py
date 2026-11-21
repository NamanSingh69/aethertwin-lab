import json,shutil
from pathlib import Path
import pytest
from tools import stage_showcase as ss
ROOT=Path(__file__).resolve().parents[1];FIXTURE=ROOT/'web'/'e2e'/'showcase'/'fixture'

def build_dir(tmp_path,note='recorded real sensor data processed offline'):
    build=tmp_path/'dist';(build/'showcase').mkdir(parents=True);(build/'index.html').write_text('<!doctype html>',encoding='utf-8')
    (build/'showcase'/'index.json').write_text(json.dumps({'frame_count':3,'duration_s':0.3,'source':{'note':note}}),encoding='utf-8');return build

def test_staged_vercel_json_builds_nothing_and_keeps_headers():
    staged=ss.staged_vercel_json(ROOT/'web'/'vercel.json');source=json.loads((ROOT/'web'/'vercel.json').read_text(encoding='utf-8'))
    assert 'outputDirectory' not in staged and staged['buildCommand']=='' and staged['installCommand']==''
    assert staged['headers']==source['headers'] and staged['cleanUrls'] is True and staged['framework'] is None

def test_refuses_the_committed_test_export_fixture(tmp_path):
    assert ss.TEST_EXPORT_MARK in json.loads((FIXTURE/'index.json').read_text(encoding='utf-8'))['source']['note']
    build=build_dir(tmp_path,note=json.loads((FIXTURE/'index.json').read_text(encoding='utf-8'))['source']['note'])
    with pytest.raises(SystemExit,match='TEST EXPORT'): ss.main(['--build',str(build),'--out',str(tmp_path/'out'),'--skip-gate'])
    assert not (tmp_path/'out').exists()

def test_refuses_a_build_without_replay_data(tmp_path):
    (tmp_path/'dist').mkdir()
    with pytest.raises(SystemExit,match='No replay index'): ss.main(['--build',str(tmp_path/'dist'),'--out',str(tmp_path/'out'),'--skip-gate'])

def test_stages_files_project_link_and_config(tmp_path):
    build=build_dir(tmp_path);previous=tmp_path/'prev';(previous/'.vercel').mkdir(parents=True)
    (previous/'.vercel'/'project.json').write_text('{"projectId":"x"}',encoding='utf-8');(previous/'.gitignore').write_text('.vercel\n',encoding='utf-8')
    assert ss.main(['--build',str(build),'--out',str(tmp_path/'out'),'--previous',str(previous),'--skip-gate'])==0
    out=tmp_path/'out';assert (out/'index.html').is_file() and (out/'showcase'/'index.json').is_file()
    assert (out/'.vercel'/'project.json').is_file() and (out/'.gitignore').read_text(encoding='utf-8')=='.vercel\n'
    assert json.loads((out/'vercel.json').read_text(encoding='utf-8'))['buildCommand']=='' and b'\r' not in (out/'vercel.json').read_bytes()

def test_never_writes_into_a_non_empty_folder(tmp_path):
    build=build_dir(tmp_path);out=tmp_path/'out';out.mkdir();(out/'keep.txt').write_text('x',encoding='utf-8')
    with pytest.raises(SystemExit,match='not empty'): ss.main(['--build',str(build),'--out',str(out),'--skip-gate'])
    assert sorted(p.name for p in out.iterdir())==['keep.txt']

def test_a_wrong_previous_folder_or_a_linked_build_copies_nothing(tmp_path):
    build=build_dir(tmp_path);(tmp_path/'prev').mkdir()
    with pytest.raises(SystemExit,match='no .vercel'): ss.main(['--build',str(build),'--out',str(tmp_path/'out'),'--previous',str(tmp_path/'prev'),'--skip-gate'])
    assert not (tmp_path/'out').exists()
    (build/'.vercel').mkdir()
    with pytest.raises(SystemExit,match='project link'): ss.main(['--build',str(build),'--out',str(tmp_path/'out'),'--skip-gate'])
    assert not (tmp_path/'out').exists()

def test_gate_failure_stops_before_staging(tmp_path,monkeypatch):
    build=build_dir(tmp_path)
    monkeypatch.setattr(ss.subprocess,'run',lambda *a,**k:type('R',(),{'returncode':1})())
    with pytest.raises(SystemExit,match='gate failed'): ss.main(['--build',str(build),'--out',str(tmp_path/'out')])
    assert not (tmp_path/'out').exists()
