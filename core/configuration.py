"""Local configuration profiles with explicit, recursive dictionary overrides."""
from pathlib import Path
import copy
import yaml


def _merge(base,override):
    out=copy.deepcopy(base)
    for key,value in override.items():
        out[key]=_merge(out[key],value) if isinstance(value,dict) and isinstance(out.get(key),dict) else copy.deepcopy(value)
    return out


def load_config(path,_seen=()):
    path=Path(path).resolve()
    if path in _seen or len(_seen)>=8:raise ValueError('Configuration inheritance cycle or depth exceeded')
    content=yaml.safe_load(path.read_text(encoding='utf-8'))
    if not isinstance(content,dict):raise ValueError('Configuration must be a mapping')
    parent=content.pop('extends',None)
    if parent is None:return content
    if not isinstance(parent,str):raise ValueError('Configuration extends must name a local file')
    target=(path.parent/parent).resolve()
    return _merge(load_config(target,(*_seen,path)),content)
