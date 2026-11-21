"""Static showcase export (tools/export_showcase.py): format v1, keyframe chunks, and the network/path scrub."""
from dataclasses import replace
import gzip
import hashlib
import json
from pathlib import Path
import struct
import numpy as np
import pytest

from command_center.protocol import SceneAccumulator,decode_packet
from ingestion.recorder_replayer import ReplaySource
from tools import export_showcase as showcase

ROOT=Path(__file__).resolve().parents[1]
SAMPLE=ROOT/'assets/sample_recording'
# Planted secrets: the kinds the real Pi diagnostics carry (Wi-Fi lease, node hostname, local paths, boot/session ids).
PLANTED={'ip':'192.168.137.37','host':'pi-secret-host-77','path':'C:\\Users\\alice\\aethertwin_rec\\bench','home':'/home/pi/capture',
         'session':'be9368c8a4884f6c803cb0351f1fa9ea','boot':'e66c7149-cf2e-47af-a149-cadd0e897013'}


def planted_iter(original):
    def iterate(self):
        for frame in original(self):
            diagnostics={**frame.diagnostics,'raw_transport':{'capture_host':PLANTED['ip'],'hostname':PLANTED['host']},
                'node_identity':{'host':PLANTED['host'],'node_session':PLANTED['session'],'boot_id':PLANTED['boot']},
                'recording_path':PLANTED['path'],'node_path':PLANTED['home'],'processing_note':f"from {PLANTED['ip']}"}
            calibration=None if frame.calibration is None else replace(frame.calibration,calibration_id=PLANTED['path']+'\\calib.json')
            yield replace(frame,diagnostics=diagnostics,calibration=calibration)
    return iterate


def records(data):
    out=[];offset=0
    while offset<len(data):
        size,t=struct.unpack_from('<Id',data,offset);offset+=12
        out.append((t,data[offset:offset+size]));offset+=size
    assert offset==len(data)
    return out


@pytest.fixture(scope='module')
def export(tmp_path_factory):
    import torch
    torch.set_num_threads(2)
    out=tmp_path_factory.mktemp('showcase')/'site'
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(ReplaySource,'__iter__',planted_iter(ReplaySource.__iter__))
        summary=showcase.main(['--recording',str(SAMPLE),'--twin',str(SAMPLE/'twin'),'--config',str(ROOT/'config/system_config.yaml'),
            '--seconds','60','--chunk-seconds','0.5','--output',str(out),'--device','cpu','--allow-synthetic'])
    return out,summary


def test_index_schema_and_chunk_list_are_consistent(export):
    out,summary=export
    index=json.loads((out/'index.json').read_text(encoding='utf-8'))
    assert index['format']=='aethertwin-showcase' and index['version']==1 and index['label']=='Recorded replay - not live'
    source=index['source']
    assert set(source)=={'recording','commit','calibration','lidar_height','hardware_verified','note'}
    assert source['recording']=='sample_recording' and source['hardware_verified'] is False
    assert source['calibration'] in ('measured','placeholder','none') and source['lidar_height'] in ('measured','assumed')
    assert source['calibration']=='placeholder'  # the simulator calibration is not measured
    assert 'synthetic' in source['note']  # a synthetic test export never claims real sensor data
    assert index['bootstrap']=='bootstrap.json' and index['assets_prefix'].startswith('assets/') and index['assets_prefix'].endswith('/')
    assert isinstance(index['recorded_at'],str) and index['recorded_at'][:2]=='20'
    chunks=index['chunks'];assert len(chunks)>=2
    first=0
    for i,chunk in enumerate(chunks):
        data=(out/chunk['file']).read_bytes()  # content-hashed name: the site caches chunks as immutable
        assert chunk['file']==f'chunks/{i:04d}-{hashlib.sha256(data).hexdigest()[:10]}.bin.gz' and chunk['first_frame']==first and chunk['frames']>=1
        assert chunk['bytes']==(out/chunk['file']).stat().st_size and chunk['start_s']<=chunk['end_s']
        first+=chunk['frames']
    assert first==index['frame_count']==len(ReplaySource(SAMPLE))
    assert index['duration_s']==chunks[-1]['end_s'] and chunks[0]['start_s']==0
    assert summary['frame_count']==index['frame_count'] and summary['total_bytes']>0


def test_chunks_parse_monotonic_and_every_chunk_starts_with_a_keyframe(export):
    out,_=export
    index=json.loads((out/'index.json').read_text(encoding='utf-8'));previous=-1.
    for chunk in index['chunks']:
        rows=records(gzip.decompress((out/chunk['file']).read_bytes()))
        assert len(rows)==chunk['frames'] and rows[0][0]==chunk['start_s'] and rows[-1][0]==chunk['end_s']
        header,_=decode_packet(rows[0][1]);assert header['kind']=='keyframe'
        accumulator=SceneAccumulator()  # a fresh client seeking to this chunk reconstructs every delta exactly
        for t,packet in rows:
            assert t>previous;previous=t
            accumulator.apply(packet)
            scene=accumulator.scene
            assert scene['source_mode']=='replay' and scene['validity']['hardware_verified'] is False
            assert scene['identity']['calibration_id'] in ('measured','placeholder','none')
            assert scene['diagnostics']['source_mode']=='replay' and 'processing_ms' in scene['diagnostics']


def test_bootstrap_is_static_read_only_and_assets_resolve(export):
    out,_=export
    index=json.loads((out/'index.json').read_text(encoding='utf-8'))
    boot=json.loads((out/'bootstrap.json').read_text(encoding='utf-8'))
    assert boot['protocol_version']==2 and boot['source_mode']=='replay' and boot['role']=='viewer'
    assert boot['hardware_verified'] is False and boot['hardware_status']=='not_tested' and boot['label']==index['label']
    assert boot['capabilities'] and not any(boot['capabilities'].values())
    assert boot['auth']=='unavailable' and boot['controls']=='read_only' and boot['source']==index['source']
    assert boot['map_manifest_url']==index['assets_prefix']+'manifest.json' and boot['replay']['frame_count']==index['frame_count']
    manifest=json.loads((out/boot['map_manifest_url']).read_text(encoding='utf-8'))
    assert manifest['map_revision']==boot['map_revision'] and manifest['tiles']
    for tile in manifest['tiles']:
        for lod in tile['lods']:
            # URLs inside manifest.json resolve relative to the manifest (as browsers and tools/predeploy_check.py
            # resolve them), not relative to the showcase root.
            assert lod['url']==lod['file'] and ((out/boot['map_manifest_url']).parent/lod['url']).is_file()


def test_planted_ip_hostname_and_paths_leave_no_trace_in_any_output_byte(export):
    out,_=export
    files=[path for path in out.rglob('*') if path.is_file()];assert files
    needles=[value.encode() for value in PLANTED.values()]+[b'Users',b'boot_id',b'node_session',b'hostname',b'192.168.']
    for path in files:
        data=path.read_bytes()
        if path.suffix=='.gz':data=gzip.decompress(data)
        for needle in needles:assert needle not in data,(path.name,needle)
    showcase.scan_for_leaks(out)  # the clean export passes its own final scan


@pytest.mark.parametrize('content',[b'{"capture_host":"10.0.0.7"}',b'{"p":"C:\\\\Users\\\\bob\\\\x"}',b'C:/Users/bob/x',b'/home/pi/rec',
    b'{"boot_id":1}',b'{"node_session":1}',b'hostname',b'{"id":"be9368c8a4884f6c803cb0351f1fa9ea"}',b'raspberrypi.local',
    b'{"stream_url":"ws://example/x"}',b'{"url":"/api/v2/bootstrap"}',b'{"login":"labpi"}'])
def test_leak_scan_raises_on_each_planted_leak(tmp_path,content):
    (tmp_path/'chunks').mkdir();(tmp_path/'index.json').write_bytes(b'{"ok":1.5}')
    showcase.scan_for_leaks(tmp_path)
    target=tmp_path/'chunks/0000.bin.gz';target.write_bytes(gzip.compress(b'\x00'*64+content+b'\x00'*64))
    with pytest.raises(showcase.LeakDetected):showcase.scan_for_leaks(tmp_path)
    target.unlink();(tmp_path/'bootstrap.json').write_bytes(content)
    with pytest.raises(showcase.LeakDetected):showcase.scan_for_leaks(tmp_path)


def test_leak_scan_also_rejects_extra_literals_and_failed_export_leaves_no_output(tmp_path,monkeypatch):
    (tmp_path/'a.json').write_bytes(b'{"camera":"CPC0000000XX"}')
    showcase.scan_for_leaks(tmp_path)
    with pytest.raises(showcase.LeakDetected):showcase.scan_for_leaks(tmp_path,extra_literals=['CPC0000000XX'])
    monkeypatch.setattr(showcase,'scan_for_leaks',lambda *args,**kwargs:(_ for _ in ()).throw(showcase.LeakDetected('planted')))
    out=tmp_path/'site'
    with pytest.raises(showcase.LeakDetected):
        showcase.main(['--recording',str(SAMPLE),'--twin',str(SAMPLE/'twin'),'--config',str(ROOT/'config/system_config.yaml'),
            '--seconds','0.25','--output',str(out),'--device','cpu','--allow-synthetic'])
    assert not out.exists() and not [p for p in tmp_path.iterdir() if p.name.startswith('.site')]


def test_diagnostics_allowlist_keeps_numbers_and_flags_only():
    clean=showcase.sanitize_diagnostics({'processing_ms':12.5,'module_ms':{'occupancy':3.,'bad key!':1.},'end_to_end_fps':9.,
        'navigation_observations_valid':True,'raw_transport':{'capture_host':'192.168.137.37'},'hostname':'pi',
        'parked_localization':{'accepted':True,'fitness':.8,'rmse_m':.05,'correction_m':.1,'correction_deg':2.,'prior_pose':[[1]],'reason':'C:\\Users\\x'},
        'traffic_grid':{'bounds_xy':[0,1,0,1],'resolution_m':.25,'decay_seconds':30.,'path':'/home/x'},'processing_ms_bad':'1.2.3.4',
        'trajectory_status':'not_requested','source_mode':'live'},frame_index=3)
    assert clean['processing_ms']==12.5 and clean['module_ms']=={'occupancy':3.} and clean['end_to_end_fps']==9.
    assert clean['parked_localization']=={'accepted':True,'fitness':.8,'rmse_m':.05,'correction_m':.1,'correction_deg':2.}
    assert clean['traffic_grid']=={'bounds_xy':[0.,1.,0.,1.],'resolution_m':.25,'decay_seconds':30.}
    assert clean['source_mode']=='replay' and clean['playback']['frame_index']==3 and clean['trajectory_status']=='not_requested'
    assert not {'raw_transport','hostname','processing_ms_bad'}&set(clean)
    assert '192.168' not in json.dumps(clean) and 'Users' not in json.dumps(clean) and np.isfinite(clean['processing_ms'])
