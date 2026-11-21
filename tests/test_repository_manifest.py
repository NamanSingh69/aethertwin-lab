import hashlib,subprocess,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'tools'))
import repository_manifest


def git(root,*args):return subprocess.run(['git',*args],cwd=root,check=True,capture_output=True)


def test_index_mode_hashes_what_is_staged_not_unstaged_edits_of_other_agents(tmp_path):
    git(tmp_path,'init','-q');(tmp_path/'a.py').write_bytes(b'staged\n');(tmp_path/'b.py').write_bytes(b'kept\n')
    git(tmp_path,'add','a.py','b.py')
    (tmp_path/'a.py').write_bytes(b'someone else is still editing this\n')
    index=repository_manifest.inventory(tmp_path,index=True)['files']
    assert index['a.py']=={'bytes':7,'sha256':hashlib.sha256(b'staged\n').hexdigest()}
    assert index['b.py']['sha256']==hashlib.sha256(b'kept\n').hexdigest()
    work=repository_manifest.inventory(tmp_path)['files']
    assert work['a.py']['sha256']==hashlib.sha256(b'someone else is still editing this\n').hexdigest()


def test_check_index_verifies_the_staged_manifest_and_ignores_unstaged_edits(tmp_path):
    import json
    git(tmp_path,'init','-q');(tmp_path/'a.py').write_bytes(b'staged\n');git(tmp_path,'add','a.py')
    manifest=tmp_path/repository_manifest.MANIFEST.name
    manifest.write_text(json.dumps(repository_manifest.inventory(tmp_path,index=True),indent=2)+'\n');git(tmp_path,'add',manifest.name)
    (tmp_path/'a.py').write_bytes(b'another agent is editing\n')
    assert repository_manifest.check(tmp_path,index=True)==[]
    assert repository_manifest.check(tmp_path,index=False)==['a.py']
    (tmp_path/'a.py').write_bytes(b'staged change\n');git(tmp_path,'add','a.py')
    assert repository_manifest.check(tmp_path,index=True)==['a.py']
