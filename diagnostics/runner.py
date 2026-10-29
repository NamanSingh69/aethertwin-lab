"""Commissioning diagnostic evidence from ordinary sensor/pipeline inputs."""
from pathlib import Path
import copy,hashlib,importlib.util,json,platform,time
import numpy as np
from core.resources import resource_root
from .audits import IMUAuditAccumulator,floor_alignment_audit,repeatability_audit,summary
from .virtual_targets import run_virtual_targets


def _make_source(config,source,input_path,root):
    if source=='sim':
        from ingestion.synthetic_lab_simulator import SyntheticLabSimulator
        result=SyntheticLabSimulator(config['seed'],config,config['simulation'].get('mobile',False))
        # Advance through separate commissioning captures. Those observations
        # are excluded from validation and never supplied as simulator geometry.
        result.commissioning_frames();return result
    if source=='replay':
        from ingestion.recorder_replayer import ReplaySource
        return ReplaySource(input_path)
    from ingestion.live_source import LiveSensorSource
    return LiveSensorSource(config['live'],root)


def _hash(path):
    with Path(path).open('rb') as stream:return hashlib.file_digest(stream,'sha256').hexdigest()


def _fingerprint(frame):
    digest=hashlib.sha256(frame.lidar_points.tobytes())
    if frame.depth is not None:digest.update(frame.depth.tobytes())
    return digest.hexdigest()


def _map_digest(pipeline):
    if pipeline is None:return None
    digest=hashlib.sha256()
    if hasattr(pipeline.occupancy.cells,'iter_tiles'):
        for tile,coords,values in pipeline.occupancy.cells.iter_tiles():
            order=np.lexsort(coords.T[::-1])
            digest.update(np.asarray(tile,np.int64).tobytes())
            digest.update(np.asarray(coords[order],np.int64).tobytes())
            digest.update(np.asarray(values[order],np.float64).tobytes())
        return digest.hexdigest()
    for key,value in sorted(pipeline.occupancy.cells.items()):
        digest.update(np.asarray(key,np.int64).tobytes());digest.update(np.asarray(value,np.float64).tobytes())
    return digest.hexdigest()


def _write(report,output):
    canonical=json.dumps(report,sort_keys=True,separators=(',',':'),allow_nan=False).encode()
    report['integrity']={'algorithm':'sha256','canonical_report_sha256':hashlib.sha256(canonical).hexdigest(),'scope':'Canonical sorted JSON excluding integrity'}
    report_path=output/'report.json';report_path.write_text(json.dumps(report,indent=2,allow_nan=False),encoding='utf-8')
    lines=['# Diagnostic evidence','',f"Source: **{report['source']['requested']}**. Software checks: **{report['software_checks_passed']}**. Hardware checks: **{report['hardware_checks_passed']}**.",
        '', '| Stage | Result | Evidence/limit |','|---|---|---|']
    for name,stage in report['stages'].items():
        detail=str(stage.get('reason',stage.get('limit','Measured details in report.json'))).replace('|','/').replace('\n',' ')
        lines.append(f"| {name} | {stage['status']} | {detail} |")
    lines+=['','No calibration, map, firmware or network configuration was promoted or changed. Virtual target injections are separately labelled and isolated from the active map.',
        '', 'Pending real validation: '+', '.join(report['pending_real_validation']), '',f"Canonical report SHA256: `{report['integrity']['canonical_report_sha256']}`"]
    (output/'report.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')
    manifest={'schema_version':1,'files':{name:_hash(output/name) for name in ('report.json','report.md')}}
    (output/'integrity.json').write_text(json.dumps(manifest,indent=2),encoding='utf-8')
    return report


def run_diagnostics(config,source='sim',input_path=None,twin_path=None,output=Path('artifacts/diagnostics'),max_frames=8,root=None):
    if source not in ('sim','replay','live'):raise ValueError('Diagnostic source must be sim, replay or live')
    if isinstance(max_frames,bool) or not isinstance(max_frames,int) or not 1<=max_frames<=256:raise ValueError('Diagnostic frame budget must be 1..256')
    root=Path(root or resource_root()).resolve();output=Path(output).resolve();output.mkdir(parents=True,exist_ok=True)
    if (output/'report.json').exists():raise FileExistsError('Choose a new diagnostic output directory; existing evidence is preserved')
    cfg=copy.deepcopy(config);cfg['input_mode']=source
    budgets=cfg.get('diagnostics',{});point_budget=budgets.get('max_points_per_frame',2000);imu_budget=budgets.get('max_imu_samples',100000)
    if any(isinstance(value,bool) or not isinstance(value,int) or value<30 for value in (point_budget,imu_budget)):raise ValueError('Diagnostic point/IMU budgets must be integer counts at least 30')
    report={'schema_version':1,'kind':'observed_pipeline_diagnostic_with_separate_virtual_tests','created_at_unix':time.time(),
        'source':{'requested':source,'actual':None,'input_path':str(input_path) if input_path else None},
        'settings':cfg,'limits':{'max_frames':max_frames,'floor_fit_points_max':point_budget,'max_unique_imu_samples':imu_budget,'max_commissioning_fingerprints':64,
            'point_selection':'Evenly spaced current observation voxel centroids when over the explicit point budget',
            'no_simulator_labels_or_geometry':True,'calibration_promotion':False},'platform':platform.platform(),
        'stages':{},'software_checks_passed':False,'hardware_checks_passed':False,
        'pending_real_validation':['connected-device acquisition and synchronization','independent measured extrinsic calibration',
            'independently annotated real-lab semantic/flow accuracy','physical target/trajectory safety testing']}
    stages=report['stages'];device_evidence={}
    for name in ('lab_sensors_native','pyorbbecsdk'):
        try:device_evidence[name]=importlib.util.find_spec(name) is not None
        except (ImportError,ValueError):device_evidence[name]=False
    stages['sdk_device_discovery']={'status':'not_tested','sdk_import_specs':device_evidence,
        'configured_lidar_ip':cfg.get('live',{}).get('lidar_ip'),'configured_s2_port':cfg.get('live',{}).get('s2_port'),
        'reason':'No physical device is opened for simulation/replay. SDK availability is not device verification.'}
    frame_count=0;clouds=[];voxel_counts=[];pipeline=None;capture=None;independent=False;assets=None;fingerprints=set();reuse=False
    imus=IMUAuditAccumulator(imu_budget);counts={'lidar_nonempty':0,'rgbd_calibrated_nonempty':0};calibration_ids=set();all_measured=True
    try:
        from core.pipeline import LabPipeline,load_commissioned_assets
        if source=='live' and twin_path is None:raise ValueError('Live diagnostics require an explicitly selected measured twin')
        if source=='replay' and input_path is None:raise ValueError('Replay diagnostics require a recording directory')
        if twin_path is None:
            candidate=Path(input_path)/'twin' if source=='replay' else root/f'assets/twin_seed{cfg["seed"]}'
            twin_path=candidate
        twin_path=Path(twin_path).resolve();assets=load_commissioned_assets(twin_path);twin,static,grid=assets
        checkpoint=Path(cfg['perception']['checkpoint']);checkpoint=checkpoint if checkpoint.is_absolute() else root/checkpoint
        report['resources']={'twin_path':str(twin_path),'measured_map_sha256':_hash(twin_path/'measured_map.npz'),'checkpoint_sha256':_hash(checkpoint)}
        independent=source=='sim'
        if source=='replay':
            from ingestion.recorder_replayer import ReplaySource
            metadata=ReplaySource(input_path).manifest.get('metadata',{})
            independent=metadata.get('independent_validation') is True
            if Path(input_path).resolve()==(twin_path/'commissioning').resolve():independent=False;reuse=True
        if source=='live':
            cfg['planning']['start']=None;cfg['planning']['goal']=None;cfg['geofences']=cfg.get('geofences',[])
            cfg['live'].setdefault('initial_pose_world',np.asarray(twin.poses[-1]).tolist());independent=True
        commissioning=twin_path/'commissioning'
        if (commissioning/'manifest.json').is_file():
            from ingestion.recorder_replayer import ReplaySource
            reference=ReplaySource(commissioning)
            try:
                for index,frame in enumerate(reference):
                    if index>=64:independent=False;break
                    fingerprints.add(_fingerprint(frame))
            finally:reference.close()
        capture=_make_source(cfg,source,input_path,root)
        mobile=bool(cfg['simulation'].get('mobile',False)) if source=='sim' else source=='live'
        if source=='replay':mobile=bool(getattr(capture,'manifest',{}).get('metadata',{}).get('motion_mode')=='mobile')
        pipeline=LabPipeline(cfg,checkpoint,twin,static,grid,mobile)
        stages['source_initialization']={'status':'passed','limit':'Selected source only; live failures never substitute simulation'}
        report['source']['actual']=source
        if source=='live':stages['sdk_device_discovery']={'status':'passed','sdk_import_specs':device_evidence,'limit':'Configured device initialization only; no accuracy or field reliability claim'}
        iterator=iter(capture)
        for _ in range(max_frames):
            try:frame=next(iterator)
            except StopIteration:break
            frame_count+=1;imus.update(frame);reuse|=_fingerprint(frame) in fingerprints
            capability=frame.capabilities
            report['source']['observed_capabilities']={'sensor_id':str(capability.sensor_id),'multi_return':bool(capability.multi_return),
                'per_point_time':bool(capability.per_point_time),'imu':bool(capability.imu),'hardware_sync':bool(capability.hardware_sync),
                'return_metadata_valid':bool(frame.return_metadata_valid)}
            counts['lidar_nonempty']+=bool(len(frame.lidar_points))
            counts['rgbd_calibrated_nonempty']+=frame.rgb is not None and frame.depth is not None and frame.calibration is not None and bool(np.count_nonzero((frame.depth>=.2)&(frame.depth<=6)&np.isfinite(frame.depth)))
            if frame.calibration is not None:calibration_ids.add(frame.calibration.calibration_id)
            all_measured=all_measured and frame.calibration is not None and frame.calibration.measured
            if frame.diagnostics.get('pose_prior_source')=='commissioning station calibration':reuse=True
            state=pipeline.process(frame);observed=np.asarray(state.voxel_centers);voxel_counts.append(len(observed))
            if len(observed)>point_budget:observed=observed[np.linspace(0,len(observed)-1,point_budget).astype(int)]
            clouds.append(observed.copy())
        stages['pipeline_observations']={'status':'passed' if clouds and any(len(c) for c in clouds) else 'failed',
            'observed_frames':frame_count,'processed_frames':len(clouds),'voxel_counts':voxel_counts,'diagnostic_sample_counts':[len(c) for c in clouds],
            'limit':'Ordinary deployed checkpoint and observation-only perception pipeline, no simulator label inputs'}
    except Exception as error:
        key='pipeline_observations' if pipeline is not None else 'source_initialization'
        stages[key]={'status':'failed','reason':f'{type(error).__name__}: {error}','observed_frames':frame_count,'processed_frames':len(clouds)}
        if source=='live':stages['sdk_device_discovery']['status']='failed';stages['sdk_device_discovery']['reason']=str(error)
    finally:
        if capture is not None:
            try:capture.close()
            except Exception as error:stages['source_shutdown']={'status':'failed','reason':str(error)}
    if frame_count:
        stages['imu_observations']=imus.report()
        stages['modality_coverage']={'status':'passed' if all(value==frame_count for value in counts.values()) else 'failed','frames':frame_count,**counts,
            'calibration_ids':sorted(calibration_ids),'calibration_measured':all_measured,
            'limit':'Profile/identity/coverage check; no extrinsic or semantic accuracy validation'}
    else:
        for name in ('imu_observations','modality_coverage'):stages[name]={'status':'not_tested','reason':'No captured observations'}
    stages['floor_alignment']=floor_alignment_audit(clouds[0],cfg.get('seed',7)) if clouds else {'status':'not_tested','reason':'No processed observations','calibration_written':False}
    independent=independent and not reuse
    evidence={'independent_observations_declared':independent,'commissioning_content_reuse_detected':reuse,
        'limit':'Simulation uses distinct post-commissioning captures; external replay requires metadata.independent_validation=true and rejects exact commissioning reuse.'}
    if independent and clouds and assets is not None:
        distances=np.concatenate([pipeline.diff.distances(c) for c in clouds if len(c)]) if any(len(c) for c in clouds) else np.empty(0)
        stages['map_residuals']={'status':'passed' if len(distances) else 'not_tested','nearest_measured_map_distance_m':summary(distances),**evidence,
            'limit':'Descriptive registration-conditioned observation centroids, including dynamics/new obstacles; not world-pose or real-lab accuracy validation.'}
        stages['floor_repeatability']=repeatability_audit(stages['floor_alignment'],clouds[1:])
    else:
        for name in ('map_residuals','floor_repeatability'):stages[name]={'status':'not_tested','reason':'Independent validation observation provenance is not established',**evidence}
    before=_map_digest(pipeline)
    try:stages['virtual_closed_loop']=run_virtual_targets()
    except Exception as error:stages['virtual_closed_loop']={'status':'failed','injected_virtual':True,'active_map_modified':False,'reason':str(error)}
    after=_map_digest(pipeline);stages['virtual_closed_loop'].update(active_map_digest_before=before,active_map_digest_after=after)
    if before!=after:stages['virtual_closed_loop'].update(status='failed',active_map_modified=True)
    stages['independent_extrinsic_calibration']={'status':'not_tested','reason':'No independent measured calibration targets supplied; floor constrains only three degrees of freedom','calibration_written':False}
    stages['physical_target_accuracy']={'status':'not_tested','reason':'Virtual closed-loop targets do not establish physical detection/tracking/planning accuracy'}
    try:
        if pipeline is not None and hasattr(pipeline,'close'):pipeline.close()
        elif pipeline is None:
            for resource in (locals().get('static'),locals().get('twin')):
                if hasattr(resource,'close'):resource.close()
        stages['resource_shutdown']={'status':'passed','limit':'Owned map databases and temporary runtime cache closed'}
    except Exception as error:stages['resource_shutdown']={'status':'failed','reason':str(error)}
    required=('source_initialization','pipeline_observations','imu_observations','modality_coverage','floor_alignment','virtual_closed_loop')
    report['software_checks_passed']=(all(stages.get(name,{}).get('status')=='passed' for name in required)
        and stages.get('source_shutdown',{}).get('status')!='failed' and stages['resource_shutdown']['status']=='passed')
    report['hardware_checks_passed']=source=='live' and all(stages.get(name,{}).get('status')=='passed' for name in ('sdk_device_discovery','independent_extrinsic_calibration','physical_target_accuracy'))
    report['hardware_status']='not_tested' if source!='live' else ('initialization_failed' if stages.get('source_initialization',{}).get('status')=='failed' else 'accuracy_validation_pending')
    report['finished_at_unix']=time.time();return _write(report,output)
