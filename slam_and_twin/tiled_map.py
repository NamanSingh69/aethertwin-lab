"""SQLite tile authority with a byte/entry-bounded write-back LRU cache."""
from __future__ import annotations
from collections import OrderedDict
from collections.abc import MutableMapping
from pathlib import Path
import json,sqlite3,sys,threading,zlib,struct,zipfile,io,math
import numpy as np
from .log_odds_octomap import SparseMultiReturnVoxelMap


class MapBudgetExceeded(RuntimeError):
    pass


class DiskTileStore(MutableMapping):
    """Tuple coordinates remain identity; hash collisions cannot merge cells.

    Cache budgets cover resident Python tile pages at operation boundaries.
    SQLite's page cache and one tile codec buffer have separate explicit bounds.
    Values are copied on read; callers replace values rather than mutate a view.
    """
    def __init__(self,path,*,tile_size=16,value_width=3,max_resident_tiles=32,
                 max_resident_bytes=64*1024**2,max_disk_bytes=4*1024**3,sqlite_cache_bytes=2*1024**2,readonly=False):
        self.path=Path(path).resolve();self.path.mkdir(parents=True,exist_ok=True)
        self.tile_size=int(tile_size);self.value_width=int(value_width)
        self.max_resident_tiles=int(max_resident_tiles);self.max_resident_bytes=int(max_resident_bytes)
        self.max_disk_bytes=int(max_disk_bytes);self.sqlite_cache_bytes=int(sqlite_cache_bytes);self.readonly=bool(readonly)
        if not 1<=self.tile_size<=64 or min(self.value_width,self.max_resident_tiles,self.max_resident_bytes,self.max_disk_bytes,self.sqlite_cache_bytes)<1:
            raise ValueError('Invalid tile/cache/disk budget')
        self.lock=threading.RLock();self.cache=OrderedDict();self.resident_bytes=0;self.peak_resident_bytes=0;self.evictions=0;self.closed=False
        self.page_reads=0;self.page_writes=0;self.streamed_reads=0
        database=self.path/'tiles.sqlite'
        self.conn=sqlite3.connect(database.as_uri()+'?mode=ro' if readonly else database,uri=readonly,check_same_thread=False)
        self.conn.execute(f'PRAGMA cache_size=-{max(1,self.sqlite_cache_bytes//1024)}')
        if not readonly:
            self.conn.execute('PRAGMA journal_mode=DELETE');self.conn.execute('PRAGMA synchronous=FULL')
            page_size=int(self.conn.execute('PRAGMA page_size').fetchone()[0])
            # Reserve another database-sized allowance for rollback journaling.
            if self.max_disk_bytes<page_size*16:raise ValueError('Disk budget is smaller than SQLite metadata reserve')
            self.conn.execute(f'PRAGMA max_page_count={self.max_disk_bytes//(2*page_size)}')
            self.conn.execute('CREATE TABLE IF NOT EXISTS tiles (x INTEGER,y INTEGER,z INTEGER,n INTEGER,payload BLOB,lx INTEGER,ly INTEGER,lz INTEGER,ux INTEGER,uy INTEGER,uz INTEGER,PRIMARY KEY(x,y,z)) WITHOUT ROWID')
            self.conn.commit()
        self.count=int(self.conn.execute('SELECT COALESCE(SUM(n),0) FROM tiles').fetchone()[0])

    def _coordinate(self,key):
        if len(key)!=3:raise ValueError('Voxel coordinate must have three integer components')
        result=tuple(int(v) for v in key)
        if any(a!=b or not -(1<<63)<=a<(1<<63) for a,b in zip(result,key)):raise ValueError('Voxel coordinate is not int64')
        return result

    def _tile(self,key):return tuple(v//self.tile_size for v in key)

    @staticmethod
    def _item_bytes(key,value):
        return sys.getsizeof(key)+sum(sys.getsizeof(v) for v in key)+sys.getsizeof(value)+sum(sys.getsizeof(v) for v in value)

    def _write(self,tile,page):
        if not page['dirty']:return
        coords=np.array(list(page['data']),dtype='<i8').reshape(-1,3)
        values=np.array(list(page['data'].values()),dtype='<f8').reshape(-1,self.value_width)
        try:
            with self.conn:
                if len(coords):
                    payload=struct.pack('<IQ',1,len(coords))+zlib.compress(coords.tobytes()+values.tobytes(),1)
                    lo=coords.min(0).tolist();hi=coords.max(0).tolist()
                    self.conn.execute('INSERT OR REPLACE INTO tiles VALUES (?,?,?,?,?,?,?,?,?,?,?)',(*tile,len(coords),payload,*lo,*hi))
                else:self.conn.execute('DELETE FROM tiles WHERE x=? AND y=? AND z=?',tile)
        except sqlite3.OperationalError as exc:
            if 'full' in str(exc).lower():raise MapBudgetExceeded('Tile-store disk budget exhausted; previous published bundle remains intact') from exc
            raise
        page['dirty']=False
        self.page_writes+=1

    def _trim(self,exclude=None):
        while len(self.cache)>self.max_resident_tiles or self.resident_bytes>self.max_resident_bytes:
            candidate=next((key for key in self.cache if key!=exclude),None)
            if candidate is None:raise MapBudgetExceeded('One tile exceeds resident byte budget; reduce tile_size or increase cache budget')
            page=self.cache[candidate];self._write(candidate,page);self.resident_bytes-=page['bytes'];del self.cache[candidate];self.evictions+=1
        self.peak_resident_bytes=max(self.peak_resident_bytes,self.resident_bytes)

    def _read_arrays(self,tile):
        """One bounded codec buffer; no resident Python page is created."""
        if self.closed:raise RuntimeError('Tile store is closed')
        self.page_reads+=1
        row=self.conn.execute('SELECT n,payload FROM tiles WHERE x=? AND y=? AND z=?',tile).fetchone()
        if row is None:return np.empty((0,3),np.int64),np.empty((0,self.value_width),float)
        if row is not None:
            n,payload=row
            if not 0<n<=self.tile_size**3 or len(payload)<12:raise ValueError('Invalid tile payload count')
            version,stored_n=struct.unpack('<IQ',payload[:12])
            if version!=1 or stored_n!=n:raise ValueError('Invalid tile codec version/count')
            limit=n*(24+8*self.value_width);decoder=zlib.decompressobj();raw=decoder.decompress(payload[12:],limit+1)
            if len(raw)!=limit or not decoder.eof:raise ValueError('Tile payload exceeds declared shape')
            coords=np.frombuffer(raw,dtype='<i8',count=n*3).reshape(n,3)
            values=np.frombuffer(raw,dtype='<f8',offset=n*24).reshape(n,self.value_width)
            if not np.isfinite(values).all() or np.any(coords//self.tile_size!=np.asarray(tile)):raise ValueError('Invalid tile coordinate/value payload')
            return coords,values

    def _page(self,tile):
        if self.closed:raise RuntimeError('Tile store is closed')
        if tile in self.cache:self.cache.move_to_end(tile);return self.cache[tile]
        coords,values=self._read_arrays(tile)
        data={tuple(map(int,key)):value.tolist() for key,value in zip(coords,values)}
        size=sys.getsizeof(data)+sum(self._item_bytes(k,v) for k,v in data.items())
        page={'data':data,'dirty':False,'bytes':size};self.cache[tile]=page;self.resident_bytes+=size
        self._trim(exclude=tile);return page

    def _groups(self,keys):
        """Group by tile, retaining original order (including duplicates) within it."""
        raw=np.asarray(keys)
        if raw.size==0:return np.empty((0,3),np.int64),iter(())
        if raw.ndim!=2 or raw.shape[1]!=3:raise ValueError('Voxel coordinates require shape (N,3)')
        if raw.dtype.kind in 'iu' and (raw.dtype.kind!='u' or np.all(raw<=np.iinfo(np.int64).max)):
            coords=raw.astype(np.int64,copy=False)
        elif raw.dtype.kind=='f' and np.isfinite(raw).all() and np.all((raw>=-(1<<63))&(raw<(1<<63))) and np.all(raw==np.floor(raw)):
            coords=raw.astype(np.int64)
        else:coords=np.array([self._coordinate(key) for key in raw],np.int64)
        tiles=coords//self.tile_size;order=np.lexsort((np.arange(len(coords)),tiles[:,2],tiles[:,1],tiles[:,0]))
        boundaries=np.r_[0,np.flatnonzero(np.any(np.diff(tiles[order],axis=0),axis=1))+1,len(order)]
        def groups():
            for start,end in zip(boundaries[:-1],boundaries[1:]):
                selected=order[start:end];yield tuple(map(int,tiles[selected[0]])),selected
        return coords,groups()

    def get_many(self,keys):
        """One bounded page visit per tile, restoring caller row order."""
        coords,groups=self._groups(keys);values=np.zeros((len(coords),self.value_width),float);known=np.zeros(len(coords),bool)
        with self.lock:
            for tile,selected in groups:
                if tile in self.cache:
                    self.cache.move_to_end(tile);data=self.cache[tile]['data']
                    for index in selected:
                        value=data.get(tuple(map(int,coords[index])))
                        if value is not None:values[index]=value;known[index]=True
                else:
                    # Read-only misses must not evict/write back a dirty page.
                    # Local integer IDs index ONE tile, never a global grid or
                    # a pairwise distance matrix. The scratch table is bounded.
                    stored,rows=self._read_arrays(tile);self.streamed_reads+=1
                    if not len(stored):continue
                    local=stored%self.tile_size;flat=(local[:,0]*self.tile_size+local[:,1])*self.tile_size+local[:,2]
                    lookup=np.full(self.tile_size**3,-1,np.int32);lookup[flat]=np.arange(len(stored),dtype=np.int32)
                    requested=coords[selected]%self.tile_size;ids=(requested[:,0]*self.tile_size+requested[:,1])*self.tile_size+requested[:,2]
                    locations=lookup[ids];valid=locations>=0
                    values[selected[valid]]=rows[locations[valid]];known[selected[valid]]=True
        return values,known

    def update_many(self,keys,update):
        """Apply per-cell callbacks in original within-key order, grouped by tile.

        Callbacks see value copies. Cache trimming still occurs after EACH
        assignment, so neither entry nor byte budgets are increased temporarily
        across operation boundaries. No transaction-sized tile residency exists.
        """
        if self.readonly:raise TypeError('Tile store is read-only')
        coords,groups=self._groups(keys)
        with self.lock:
            for tile,selected in groups:
                page=self._page(tile);data=page['data']
                for index in selected:
                    key=tuple(map(int,coords[index]));old=data.get(key)
                    value=update(int(index),key,None if old is None else old.copy())
                    try:new=list(map(float,value))
                    except (TypeError,ValueError) as error:raise ValueError('Invalid tile callback value') from error
                    if len(new)!=self.value_width or not all(math.isfinite(v) for v in new):raise ValueError('Invalid tile callback value width/finiteness')
                    before=sys.getsizeof(data);data[key]=new
                    delta=sys.getsizeof(data)-before+self._item_bytes(key,new)-(0 if old is None else self._item_bytes(key,old))
                    page['bytes']+=delta;self.resident_bytes+=delta;page['dirty']=True
                    if old is None:self.count+=1
                    self._trim(exclude=tile)

    def __getitem__(self,key):
        with self.lock:
            key=self._coordinate(key);return self._page(self._tile(key))['data'][key].copy()

    def __setitem__(self,key,value):
        if self.readonly:raise TypeError('Tile store is read-only')
        value=np.asarray(value,dtype=float)
        if value.shape!=(self.value_width,) or not np.isfinite(value).all():raise ValueError('Invalid tile value width or non-finite data')
        with self.lock:
            key=self._coordinate(key);tile=self._tile(key);page=self._page(tile);data=page['data'];before=sys.getsizeof(data)
            old=data.get(key);new=value.tolist();data[key]=new
            delta=sys.getsizeof(data)-before+self._item_bytes(key,new)-(0 if old is None else self._item_bytes(key,old))
            page['bytes']+=delta;self.resident_bytes+=delta;page['dirty']=True
            if old is None:self.count+=1
            self._trim(exclude=tile)

    def __delitem__(self,key):
        if self.readonly:raise TypeError('Tile store is read-only')
        with self.lock:
            key=self._coordinate(key);tile=self._tile(key);page=self._page(tile);data=page['data'];before=sys.getsizeof(data);old=data.pop(key)
            delta=sys.getsizeof(data)-before-self._item_bytes(key,old);page['bytes']+=delta;self.resident_bytes+=delta;page['dirty']=True;self.count-=1

    def __len__(self):return self.count

    def __iter__(self):
        for _,coords,_ in self.iter_tiles():
            for coord in coords:yield tuple(map(int,coord))

    def flush(self):
        with self.lock:
            for tile,page in self.cache.items():self._write(tile,page)

    def tile_keys(self,bounds=None):
        """Tiles overlapping an inclusive voxel-index box (all when None), including unsaved pages; reads no payload."""
        sql='SELECT x,y,z FROM tiles';parameters=();lower=upper=None
        if bounds is not None:
            bounds=np.asarray(bounds,dtype=np.int64).reshape(2,3)
            lower=bounds[0]//self.tile_size;upper=bounds[1]//self.tile_size
            sql+=' WHERE x>=? AND y>=? AND z>=? AND x<=? AND y<=? AND z<=?';parameters=(*map(int,lower),*map(int,upper))
        # Dirty/new pages are part of the read view, without forcing write-back
        # on every renderer query. The extra-key set is bounded by cache size.
        with self.lock:
            extras=[tile for tile in self.cache if self.conn.execute('SELECT 1 FROM tiles WHERE x=? AND y=? AND z=?',tile).fetchone() is None]
            stored=[tuple(tile) for tile in self.conn.execute(sql+' ORDER BY x,y,z',parameters)]
        return stored+[tile for tile in extras if lower is None or np.all((np.asarray(tile)>=lower)&(np.asarray(tile)<=upper))]

    def read_tile(self,tile):
        """One tile's keys and values as new arrays, from its resident page or its stored payload."""
        with self.lock:
            if tile in self.cache:
                self.cache.move_to_end(tile);data=self.cache[tile]['data']
                return np.array(list(data),np.int64).reshape(-1,3),np.array(list(data.values()),float).reshape(-1,self.value_width)
            coords,values=self._read_arrays(tile);self.streamed_reads+=1
            return coords.copy(),values.copy()

    def iter_tiles(self,bounds=None):
        if bounds is not None:bounds=np.asarray(bounds,dtype=np.int64).reshape(2,3)
        for tile in self.tile_keys(bounds):
            coords,values=self.read_tile(tile)
            if bounds is not None:
                select=np.all((coords>=bounds[0])&(coords<=bounds[1]),axis=1);coords=coords[select];values=values[select]
            if len(coords):yield tuple(tile),coords,values

    def coordinate_bounds(self):
        row=self.conn.execute('SELECT MIN(lx),MIN(ly),MIN(lz),MAX(ux),MAX(uy),MAX(uz) FROM tiles').fetchone()
        bounds=None if row[0] is None else np.array(row,dtype=np.int64).reshape(2,3)
        for page in self.cache.values():
            if page['data']:
                coordinates=np.array(list(page['data']),np.int64);other=np.array([coordinates.min(0),coordinates.max(0)])
                bounds=other if bounds is None else np.array([np.minimum(bounds[0],other[0]),np.maximum(bounds[1],other[1])])
        return bounds

    def statistics(self):
        return {'cells':len(self),'resident_tiles':len(self.cache),'resident_cells':sum(len(page['data']) for page in self.cache.values()),
            'resident_bytes':self.resident_bytes,'peak_resident_bytes':self.peak_resident_bytes,
            'evictions':self.evictions,'max_resident_tiles':self.max_resident_tiles,'max_resident_bytes':self.max_resident_bytes,
            'page_reads':self.page_reads,'page_writes':self.page_writes,'streamed_reads':self.streamed_reads,
            'max_disk_bytes':self.max_disk_bytes,'sqlite_cache_bytes':self.sqlite_cache_bytes,
            'tile_codec_max_uncompressed_bytes':self.tile_size**3*(24+8*self.value_width),
            'tile_query_lookup_max_bytes':self.tile_size**3*np.dtype(np.int32).itemsize,
            'database_bytes':(self.path/'tiles.sqlite').stat().st_size}

    def close(self):
        if not self.closed:
            try:self.flush()
            finally:self.conn.close();self.closed=True;self.cache.clear();self.resident_bytes=0


class DiskBackedVoxelMap(SparseMultiReturnVoxelMap):
    """Identical evidence/DDA semantics with disk-backed cell identity and values."""
    def __init__(self,path,voxel_size=.25,decay_rate=1.,min_log_odds=-4,max_log_odds=4,max_age=float('inf'),*,readonly=False,
                 ray_backend='auto',max_traversal_cells=5_000_000,expiry_tiles_per_update=1,expiry_interval_updates=20,**budgets):
        super().__init__(voxel_size,decay_rate,min_log_odds,max_log_odds,max_age,ray_backend,max_traversal_cells)
        self.path=Path(path).resolve();self.cells=DiskTileStore(self.path,readonly=readonly,**budgets);self.readonly=readonly
        self.eager_pruning=False;self.expiry_tiles_per_update=max(0,int(expiry_tiles_per_update));self._expiry_after=None
        self.expiry_interval_updates=max(1,int(expiry_interval_updates));self._expiry_round=0

    def voxelize_and_update(self,*args,**kwargs):
        if self.readonly:raise TypeError('Loaded tile authority is read-only; open explicitly writable to integrate observations')
        result=super().voxelize_and_update(*args,**kwargs)
        if np.isfinite(self.max_age) and self._updates%self.expiry_interval_updates==0:self.prune_expired(self.expiry_tiles_per_update)
        return result

    def query(self,points,return_known=False):
        coordinates=np.floor(np.asarray(points)[:,:3]/self.voxel_size).astype(np.int64);values=np.zeros(len(coordinates),np.float32);known=np.zeros(len(coordinates),bool)
        stored,present=self.cells.get_many(coordinates)
        for i in np.flatnonzero(present):
            value=stored[i]
            if self.timestamp-float(value[2])<=self.max_age:
                known[i]=True;values[i]=float(value[0])*self.decay_rate**(max(0.,self.timestamp-float(value[2]))/.1)
        return (values,known) if return_known else values

    def _apply_evidence(self,free,hits,penetrations):
        free=list(free);keys=free+list(hits);boundary=len(free)
        def apply(index,key,old):
            previous=0. if old is None else old[0]*self.decay_rate**(max(0.,self.timestamp-old[2])/.1)
            if index<boundary:return [max(self.minimum,previous-.4),0.,self.timestamp]
            perm=penetrations[key] if old is None else .7*old[1]+.3*penetrations[key]
            return [min(self.maximum,previous+hits[key]),perm,self.timestamp]
        self.cells.update_many(keys,apply)

    def _observed_values(self,unique):
        stored,known=self.cells.get_many(unique)
        if not known.all():raise KeyError('Fresh observed voxel missing from tile authority')
        lo=np.array([float(v[0])*self.decay_rate**(max(0.,self.timestamp-float(v[2]))/.1) for v in stored],np.float32)
        return lo,stored[:,1].astype(np.float32)

    def prune_expired(self,max_tiles=1):
        if self.readonly or not np.isfinite(self.max_age) or max_tiles<1:return 0
        self._expiry_round+=1
        if self._expiry_round%2 and self.cells.cache:
            cache=list(self.cells.cache);start=(self._expiry_round//2)%len(cache)
            tiles=[cache[(start+i)%len(cache)] for i in range(min(max_tiles,len(cache)))]
        else:
            parameters=() if self._expiry_after is None else self._expiry_after
            sql='SELECT x,y,z FROM tiles'+('' if self._expiry_after is None else ' WHERE (x,y,z)>(?,?,?)')+' ORDER BY x,y,z LIMIT ?'
            tiles=list(self.cells.conn.execute(sql,(*parameters,int(max_tiles))))
            if not tiles:self._expiry_after=None;return 0
            self._expiry_after=tuple(tiles[-1])
        removed=0
        for tile in tiles:
            with self.cells.lock:data=list(self.cells._page(tile)['data'].items())
            for key,value in data:
                if self.timestamp-value[2]>self.max_age:del self.cells[key];removed+=1
        return removed

    @property
    def observed_bounds(self):
        bounds=self.cells.coordinate_bounds()
        if bounds is None:return None
        lo=bounds[0]*self.voxel_size;hi=(bounds[1]+1)*self.voxel_size
        return np.array([lo[0],hi[0],lo[1],hi[1],lo[2],hi[2]])

    def iter_observed(self,bounds=None):
        indices=None
        if bounds is not None:
            b=np.asarray(bounds,float).reshape(6);indices=np.array([np.floor(b[[0,2,4]]/self.voxel_size),np.ceil(b[[1,3,5]]/self.voxel_size)-1],np.int64)
        for tile,coords,values in self.cells.iter_tiles(indices):
            selected=self.timestamp-values[:,2]<=self.max_age;coords=coords[selected];values=values[selected]
            if not len(coords):continue
            values=values.copy();values[:,0]*=self.decay_rate**(np.maximum(0,self.timestamp-values[:,2])/.1)
            yield tile,coords,values

    def iter_occupied_points(self,threshold=.4,bounds=None):
        for _,coords,values in self.iter_observed(bounds):
            selected=values[:,0]>threshold
            if selected.any():yield (coords[selected]+.5)*self.voxel_size

    def occupied_points(self,threshold=.4,max_points=262144,bounds=None):
        chunks=[];count=0
        for points in self.iter_occupied_points(threshold,bounds):
            count+=len(points)
            if count>max_points:raise MapBudgetExceeded('Occupied-point materialization exceeds budget; use iter_occupied_points(bounds=...)')
            chunks.append(points)
        return np.concatenate(chunks) if chunks else np.empty((0,3))

    def free_points(self,threshold=-.2,max_points=262144):
        chunks=[];count=0
        for _,coords,values in self.iter_observed():
            points=(coords[values[:,0]<threshold]+.5)*self.voxel_size;count+=len(points)
            if count>max_points:raise MapBudgetExceeded('Free-point materialization exceeds budget; use iter_observed')
            if len(points):chunks.append(points)
        return np.concatenate(chunks) if chunks else np.empty((0,3))

    def flush(self):
        self.cells.flush()
        if self.readonly:return
        content={'schema_version':1,'format':'lidar-lab-occupancy-tiles','voxel_size':self.voxel_size,'decay_rate':self.decay_rate,
            'min_log_odds':self.minimum,'max_log_odds':self.maximum,'max_age':None if np.isinf(self.max_age) else self.max_age,
            'timestamp':self.timestamp,'updates':self._updates,'tile_size':self.cells.tile_size,
            'ray_backend':self.ray_backend,'max_traversal_cells':self.max_traversal_cells,
            'expiry_interval_updates':self.expiry_interval_updates,'expiry_tiles_per_update':self.expiry_tiles_per_update,
            'observed_bounds':None if self.observed_bounds is None else self.observed_bounds.tolist(),'budgets':self.cells.statistics()}
        temporary=self.path/'manifest.json.tmp';temporary.write_text(json.dumps(content,indent=2));temporary.replace(self.path/'manifest.json')

    def save(self,path):
        """Bounded-memory legacy NPZ export, or flush the existing tile directory."""
        path=Path(path)
        if path.suffix!='.npz':
            if path.resolve()!=self.path.resolve():raise ValueError('Tile authority relocation is performed by publishing its enclosing bundle')
            self.flush();return
        self.flush();path.parent.mkdir(parents=True,exist_ok=True);temporary=path.with_name(path.name+'.tmp')
        with zipfile.ZipFile(temporary,'w',compression=zipfile.ZIP_DEFLATED,compresslevel=1,allowZip64=True) as archive:
            for name,dtype,column in [('coords','<i8',0),('values','<f8',1)]:
                with archive.open(name+'.npy','w',force_zip64=True) as stream:
                    np.lib.format.write_array_header_1_0(stream,{'descr':np.dtype(dtype).str,'fortran_order':False,'shape':(len(self.cells),3)})
                    for _,coords,values in self.cells.iter_tiles():stream.write(np.asarray(coords if column==0 else values,dtype=dtype).tobytes())
            metadata={'voxel_size':self.voxel_size,'decay_rate':self.decay_rate,'timestamp':self.timestamp,'schema_version':1,
                'min_log_odds':self.minimum,'max_log_odds':self.maximum,'max_age':self.max_age,'updates':self._updates}
            for name,value in metadata.items():
                with archive.open(name+'.npy','w') as stream:np.save(stream,np.asarray(value),allow_pickle=False)
        temporary.replace(path)

    def close(self):
        if self.cells.closed:return
        try:self.flush()
        finally:self.cells.close()

    @classmethod
    def load(cls,path,readonly=True,**overrides):
        path=Path(path);metadata=json.loads((path/'manifest.json').read_text())
        if metadata.get('schema_version')!=1 or metadata.get('format')!='lidar-lab-occupancy-tiles':raise ValueError('Unsupported tiled map manifest')
        budgets={key:metadata['budgets'][key] for key in ('max_resident_tiles','max_resident_bytes','max_disk_bytes','sqlite_cache_bytes')}
        ray_backend=overrides.pop('ray_backend',metadata.get('ray_backend','auto'));traversal=overrides.pop('max_traversal_cells',metadata.get('max_traversal_cells',5_000_000))
        overrides.setdefault('expiry_interval_updates',metadata.get('expiry_interval_updates',20));overrides.setdefault('expiry_tiles_per_update',metadata.get('expiry_tiles_per_update',1))
        budgets.update(overrides);budgets['tile_size']=metadata['tile_size']
        obj=cls(path,metadata['voxel_size'],metadata['decay_rate'],metadata['min_log_odds'],metadata['max_log_odds'],
            float('inf') if metadata['max_age'] is None else metadata['max_age'],readonly=readonly,
            ray_backend=ray_backend,max_traversal_cells=traversal,**budgets)
        obj.timestamp=metadata['timestamp'];obj._updates=metadata['updates'];return obj


TiledVoxelMap=DiskBackedVoxelMap
