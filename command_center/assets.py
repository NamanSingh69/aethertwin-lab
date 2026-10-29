"""Offline display-only GLB tiles. Measured occupancy remains authoritative."""
from __future__ import annotations
import hashlib
import json
from pathlib import Path
import struct
import numpy as np


def file_revision(path):
    path=Path(path)
    with path.open('rb') as stream:return hashlib.file_digest(stream,'sha256').hexdigest()[:24]


def write_glb(path,vertices,triangles,colors=None,normals=None):
    vertices=np.ascontiguousarray(vertices,dtype='<f4').reshape(-1,3)
    triangles=np.ascontiguousarray(triangles,dtype='<u4').reshape(-1,3)
    if not len(vertices) or not len(triangles):raise ValueError('GLB requires nonempty geometry')
    if not np.isfinite(vertices).all() or triangles.max()>=len(vertices):raise ValueError('Invalid GLB geometry')
    arrays=[vertices,triangles];kinds=[('VEC3',5126,34962),('SCALAR',5125,34963)]
    attrs={'POSITION':0}
    for name,value in (('COLOR_0',colors),('NORMAL',normals)):
        if value is None:continue
        array=np.ascontiguousarray(value,dtype='<f4').reshape(-1,3)
        if len(array)!=len(vertices) or not np.isfinite(array).all():raise ValueError('Invalid GLB attributes')
        attrs[name]=len(arrays);arrays.append(array);kinds.append(('VEC3',5126,34962))
    views=[];accessors=[];parts=[];offset=0
    for array,(kind,component,target) in zip(arrays,kinds):
        payload=array.tobytes();padding=(-offset)%4
        if padding:parts.append(bytes(padding));offset+=padding
        index=len(views);views.append({'buffer':0,'byteOffset':offset,'byteLength':len(payload),'target':target})
        accessor={'bufferView':index,'componentType':component,'count':int(array.size if kind=='SCALAR' else len(array)),'type':kind}
        if index==0:accessor.update(min=vertices.min(0).tolist(),max=vertices.max(0).tolist())
        accessors.append(accessor);parts.append(payload);offset+=len(payload)
    binary=b''.join(parts);binary+=bytes((-len(binary))%4)
    document={'asset':{'version':'2.0','generator':'AetherTwin measured display export','extras':{'coordinate_frame':'world_z_up','navigation_authority':False}},
              'scene':0,'scenes':[{'nodes':[0]}],'nodes':[{'mesh':0}],
              'meshes':[{'primitives':[{'attributes':attrs,'indices':1,'material':0,'mode':4}]}],
              'materials':[{'name':'Measured surface','doubleSided':True,'pbrMetallicRoughness':{'baseColorFactor':[.65,.85,.9,1],
                            'metallicFactor':0,'roughnessFactor':.9}}],
              'buffers':[{'byteLength':len(binary)}],'bufferViews':views,'accessors':accessors}
    encoded=json.dumps(document,separators=(',',':')).encode();encoded+=b' '*((-len(encoded))%4)
    total=12+8+len(encoded)+8+len(binary)
    Path(path).write_bytes(struct.pack('<4sII',b'glTF',2,total)+struct.pack('<I4s',len(encoded),b'JSON')+encoded+
                           struct.pack('<I4s',len(binary),b'BIN\0')+binary)


def export_twin_assets(twin,output,map_revision,tile_size=4.,max_tile_triangles=100000,url_prefix='/api/v2/assets/',max_manifest_tiles=100000):
    """Export every persisted TSDF tile lazily; LOD geometry is display only."""
    if tile_size<=0 or max_tile_triangles<1 or max_manifest_tiles<1:raise ValueError('Invalid display tile budget')
    output=Path(output);output.mkdir(parents=True,exist_ok=True);manifest_path=output/'manifest.json'
    if manifest_path.exists():
        cached=json.loads(manifest_path.read_text(encoding='utf-8'))
        if cached.get('geometry_export_version')==2 and cached.get('map_revision')==map_revision and cached.get('tile_size_m')==tile_size and cached.get('max_tile_triangles')==max_tile_triangles and len(cached.get('tiles',[]))<=max_manifest_tiles:
            if all((output/lod['file']).is_file() for tile in cached['tiles'] for lod in tile['lods']):return cached
    archive=getattr(twin,'tsdf_archive',None) is not None
    source=twin.iter_tsdf_meshes() if archive else iter((twin.mesh,))
    tiles=[];lower=None;upper=None;source_meshes=0;used_archive=False
    revision_tag=hashlib.sha256(f'{map_revision}:{tile_size}:{max_tile_triangles}:2'.encode()).hexdigest()[:12]
    import open3d as o3d
    def meshes():
        nonlocal used_archive
        found=False
        for mesh in source:
            if len(mesh.triangles):found=True;used_archive=archive;yield mesh
        if not found and archive and len(twin.mesh.triangles):yield twin.mesh
    for source_index,mesh in enumerate(meshes()):
        vertices=np.asarray(mesh.vertices);triangles=np.asarray(mesh.triangles)
        if not len(vertices) or not len(triangles):continue
        source_meshes+=1
        keys=np.floor(vertices[triangles].mean(1)[:,:2]/tile_size).astype(np.int64)
        unique,inverse=np.unique(keys,axis=0,return_inverse=True)
        for group,key in enumerate(unique):
            selected=triangles[inverse==group]
            for part,start in enumerate(range(0,len(selected),max_tile_triangles)):
                if len(tiles)>=max_manifest_tiles:raise ValueError('Display manifest tile budget exceeded; reduce tile detail or select a region')
                faces=selected[start:start+max_tile_triangles];indices,remapped=np.unique(faces,return_inverse=True)
                chunk=o3d.geometry.TriangleMesh(o3d.utility.Vector3dVector(vertices[indices]),o3d.utility.Vector3iVector(remapped.reshape(-1,3)))
                if mesh.has_vertex_colors():chunk.vertex_colors=o3d.utility.Vector3dVector(np.asarray(mesh.vertex_colors)[indices])
                chunk.compute_vertex_normals();tile_id=f'tile_{revision_tag}_{source_index}_{int(key[0])}_{int(key[1])}_{part}'
                lo=np.asarray(chunk.vertices).min(0);hi=np.asarray(chunk.vertices).max(0)
                lower=lo if lower is None else np.minimum(lower,lo);upper=hi if upper is None else np.maximum(upper,hi)
                bounds=np.r_[lo,hi].tolist();lods=[]
                for level,(ratio,distance) in enumerate(((1.,0),(.45,8),(.15,18))):
                    reduced=chunk if level==0 or len(chunk.triangles)<32 else chunk.simplify_quadric_decimation(max(12,int(len(chunk.triangles)*ratio)))
                    reduced.compute_vertex_normals();name=f'{tile_id}_lod{level}.glb'
                    write_glb(output/name,np.asarray(reduced.vertices),np.asarray(reduced.triangles),
                        np.asarray(reduced.vertex_colors) if reduced.has_vertex_colors() else None,np.asarray(reduced.vertex_normals))
                    lods.append({'url':f'{url_prefix}{name}','file':name,'distance':distance,'triangles':len(reduced.triangles)})
                tiles.append({'id':tile_id,'bounds':bounds,'lods':lods})
    if lower is None:raise ValueError('Twin has no measured display mesh')
    manifest={'schema_version':1,'geometry_export_version':2,'map_revision':map_revision,'coordinate_frame':'world_z_up','units':'m',
        'bounds':np.r_[lower,upper].tolist(),'tile_size_m':tile_size,'max_tile_triangles':max_tile_triangles,'max_manifest_tiles':max_manifest_tiles,
        'navigation_authority':False,'geometry_source':'all_persisted_tsdf_tiles' if used_archive else 'legacy_or_bounded_preview',
        'source_meshes':source_meshes,'tiles':tiles}
    temporary=manifest_path.with_suffix('.tmp');temporary.write_text(json.dumps(manifest,indent=2),encoding='utf-8');temporary.replace(manifest_path)
    return manifest
