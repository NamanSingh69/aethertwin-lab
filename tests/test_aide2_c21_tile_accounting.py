"""AIDE2 C21: _item_bytes was half of the live occupancy stage (731k calls per 40 real frames)."""
import sys
import numpy as np
from slam_and_twin.tiled_map import DiskTileStore


def recomputed(store,page):
    return sys.getsizeof(page['data'])+sum(store._item_bytes(k,v) for k,v in page['data'].items())


def test_resident_byte_accounting_stays_exact_through_inserts_and_overwrites(tmp_path):
    store=DiskTileStore(tmp_path/'t',tile_size=8,value_width=3,max_resident_tiles=64)
    rng=np.random.default_rng(0);keys=rng.integers(-20,20,(4000,3))
    store.update_many(keys,lambda i,k,old:[1.,2.,3.])
    for _ in range(3):store.update_many(keys,lambda i,k,old:old+np.array([.5,0,1]) if old is not None else [0.,0.,0.])
    store[(1,2,3)]=[9.,9.,9.];store[(1,2,3)]=[8.,8.,8.]
    assert store.resident_bytes==sum(page['bytes'] for page in store.cache.values())
    for page in store.cache.values():assert page['bytes']==recomputed(store,page)


def test_overwriting_existing_cells_does_not_walk_every_key_and_float(tmp_path,monkeypatch):
    # Every tile stays resident: a page loaded from disk legitimately walks its items once to size itself.
    store=DiskTileStore(tmp_path/'t',tile_size=8,value_width=3,max_resident_tiles=1000,max_resident_bytes=1<<30)
    keys=np.random.default_rng(1).integers(-20,20,(3000,3));store.update_many(keys,lambda i,k,old:[1.,2.,3.])
    calls={'n':0};original=DiskTileStore._item_bytes
    monkeypatch.setattr(DiskTileStore,'_item_bytes',staticmethod(lambda k,v:(calls.__setitem__('n',calls['n']+1),original(k,v))[1]))
    store.update_many(keys,lambda i,k,old:np.asarray(old)+1)
    assert calls['n']==0
