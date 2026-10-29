"""Reproducible scene-split training and independent synthetic evaluation."""
from __future__ import annotations
import json,time,hashlib,sys,platform
from pathlib import Path
import numpy as np
import torch
from torch.nn import functional as F
from scipy.spatial import cKDTree
from core.se3_math import transform_points
from slam_and_twin.imu_deskew import ContinuousTimeIMUDeskewer
from slam_and_twin.icp_pose_graph import ICPPoseGraph
from ingestion.synthetic_lab_simulator import SyntheticLabSimulator
from .hybrid_3d_network import MultiTaskVoxelSceneFlowNet,multitask_loss,POINTNET_FPS_CAP,POINTNET_RADII
from .point_mae_pretrain import MAE_PATCH_POINTS,MAE_CENTER_CAP
from .inference_engine import prepare_frame,aggregate_targets,fused_observations
from slam_and_twin.log_odds_octomap import SparseMultiReturnVoxelMap


def validate_synthetic_splits(config):
    """Reject repeated or overlapping procedural scenes before any model work."""
    groups={}
    for name in ('train_seeds','validation_seeds','test_seeds'):
        values=config['training'].get(name,[])
        if not isinstance(values,(list,tuple)) or any(isinstance(seed,bool) or not isinstance(seed,(int,np.integer)) or seed<0 for seed in values):
            raise ValueError(f'{name} must contain nonnegative integer scene seeds')
        groups[name]=set(map(int,values))
        if len(groups[name])!=len(values):raise ValueError(f'Duplicate scene seed in {name}')
    if not groups['train_seeds'] or not groups['validation_seeds']:
        raise ValueError('Synthetic training requires nonempty train and validation splits')
    names=list(groups)
    for i,a in enumerate(names):
        for b in names[i+1:]:
            if groups[a]&groups[b]:raise ValueError(f'Synthetic split leakage: {a} and {b} overlap')


def _configured_model(config,device):
    perception=config['perception']
    return MultiTaskVoxelSceneFlowNet(neighbors=perception.get('neighbors',16),
        temporal_neighbors=perception.get('temporal_neighbors',12),
        range_image_height=perception.get('range_image_height',24),
        range_image_width=perception.get('range_image_width',96)).to(device)


def _sampling_settings(config,model):
    return {'max_voxels':config['perception']['max_voxels'],'neighbors':model.neighbors,
        'temporal_neighbors':model.temporal_neighbors,'rgbd_stride':config['perception'].get('rgbd_stride',3),
        'range_image_height':model.range.height,'range_image_width':model.range.width,
        'camera_feature_height':config['perception'].get('camera_feature_height'),
        'camera_feature_width':config['perception'].get('camera_feature_width')}


def _sampling_constants():
    return {'pointnet_fps_cap':POINTNET_FPS_CAP,'pointnet_radii_m':list(POINTNET_RADII),
        'mae_patch_points':MAE_PATCH_POINTS,'mae_center_cap':MAE_CENTER_CAP}


def prepare_training_frame(model,frame,points,tree,config,device,memory,ages,previous_coords=None):
    memory.voxelize_and_update(points,timestamp=frame.timestamp,integrate_rays=False)
    def age(xyz):
        output=[]
        for key in map(tuple,np.floor(xyz/config['mapping']['voxel_size']).astype(np.int64)):
            if key not in ages:ages[key]=frame.timestamp
            output.append(frame.timestamp-ages[key])
        return np.array(output)
    return prepare_frame(model,frame,points,lambda x:tree.query(x)[0],persistence=age,
        voxel_size=config['mapping']['voxel_size'],max_voxels=config['perception']['max_voxels'],device=device,occupancy=memory.query,
        previous_coords=previous_coords,camera_feature_height=config['perception'].get('camera_feature_height'),
        camera_feature_width=config['perception'].get('camera_feature_width'))


def dataset(seeds,frames_per_scene,config):
    scenes=[]
    rgbd_stride=config['perception'].get('rgbd_stride',3)
    for seed in seeds:
        mobile=bool(config['simulation'].get('mobile',False))
        sim=SyntheticLabSimulator(seed=seed,config=config,mobile=mobile)
        commissioning=sim.commissioning_frames()
        static=np.concatenate([transform_points(f.sensor_pose_world,f.lidar_points[:,:3]) for f in commissioning])
        tree=cKDTree(static)
        frames=[];deskewer=None;previous_pose=None;previous_pose_time=None
        odometry=ICPPoseGraph(reference_points=static) if mobile else None
        for _ in range(frames_per_scene):
            frame=sim.next_frame();truth=sim.last_ground_truth
            if deskewer is None:deskewer=ContinuousTimeIMUDeskewer(initial_pose=frame.sensor_pose_world,imu_to_lidar=frame.imu_to_lidar)
            frame=deskewer.deskew_frame(frame)
            if mobile:
                from dataclasses import replace
                pose=odometry.add_scan(frame.lidar_points[:,:3],frame.sensor_pose_world)
                pose_time=frame.timestamp+frame.sweep_duration
                velocity=(pose[:3,3]-previous_pose[:3,3])/(pose_time-previous_pose_time) if previous_pose is not None else deskewer.velocity
                deskewer.correct_state(pose,velocity);previous_pose=pose.copy();previous_pose_time=pose_time
                frame=replace(frame,sensor_pose_world=pose)
            points=frame.lidar_points.copy();points[:,:3]=transform_points(frame.sensor_pose_world,points[:,:3])
            points,truth=fused_observations(frame,points,truth,stride=rgbd_stride)
            frames.append((frame,truth,points))
        sim.close();scenes.append((seed,tree,frames))
    return scenes


def tensors(prepared,truth,device):
    labels,flow,valid,instances=aggregate_targets(prepared,truth)
    return [torch.as_tensor(x,device=device,dtype=kind) for x,kind in zip(
        (labels,flow,valid,instances),(torch.long,torch.float32,torch.bool,torch.long))]


@torch.no_grad()
def evaluate_model(model,scenes,config,device='cpu'):
    model.eval();confusion=np.zeros((7,7),np.int64);errors=[];zeros=[];class_counts=np.zeros(7,int)
    dynamic_errors=[];dynamic_zeros=[];losses=[];frames=0
    for seed,tree,sequence in scenes:
        previous=None;memory=SparseMultiReturnVoxelMap(config['mapping']['voxel_size']);ages={}
        for frame,truth,points in sequence:
            prepared=prepare_training_frame(model,frame,points,tree,config,device,memory,ages,None if previous is None else previous[0])
            kwargs=dict(prepared.inputs)
            if previous is not None:kwargs['coords_prev'],kwargs['cached_stage2_prev']=previous
            logits,flow,embedding=model(**kwargs)
            previous=(kwargs['coords_t'],embedding)
            labels,target,valid,instances=tensors(prepared,truth,device)
            pred=logits.argmax(1).cpu().numpy();y=labels.cpu().numpy()
            np.add.at(confusion,(y,pred),1);class_counts+=np.bincount(y,minlength=7)
            e=(flow-target).norm(dim=-1).cpu().numpy();z=target.norm(dim=-1).cpu().numpy();mask=valid.cpu().numpy()
            dynamic=mask&np.isin(y,[2,3])
            errors.extend(e[mask].tolist());zeros.extend(z[mask].tolist())
            dynamic_errors.extend(e[dynamic].tolist());dynamic_zeros.extend(z[dynamic].tolist())
            loss,_=multitask_loss(logits,flow,labels,target,valid,kwargs['coords_t'],instances);losses.append(float(loss));frames+=1
    denom=confusion.sum(0)+confusion.sum(1)-confusion.diagonal()
    iou=np.divide(confusion.diagonal(),denom,out=np.zeros(7),where=denom>0)
    majority=int(class_counts.argmax());baseline=np.zeros(7);baseline[majority]=class_counts[majority]/max(class_counts.sum(),1)
    return {'frames':frames,'sampling':_sampling_settings(config,model),'sampling_constants':_sampling_constants(),
            'scene_seeds':[int(s[0]) for s in scenes if isinstance(s[0],(int,np.integer))],
            'scene_ids':[str(s[0]) for s in scenes],
            'semantic_miou':float(iou.mean()),'per_class_iou':iou.tolist(),'confusion_matrix':confusion.tolist(),
            'majority_class_miou':float(baseline.mean()),'flow_epe_m':float(np.mean(errors)) if errors else 0.,
            'zero_flow_epe_m':float(np.mean(zeros)) if zeros else 0.,
            'dynamic_flow_epe_m':float(np.mean(dynamic_errors)) if dynamic_errors else 0.,
            'dynamic_zero_flow_epe_m':float(np.mean(dynamic_zeros)) if dynamic_zeros else 0.,
            'loss':float(np.mean(losses)) if losses else 0.}


def train(config,output,epochs=None,device='cpu',progress=print,mae_checkpoint=None,initial_checkpoint=None,data_manifest=None):
    cfg=config['training']
    run_epochs=cfg['epochs'] if epochs is None else epochs
    if isinstance(run_epochs,bool) or not isinstance(run_epochs,(int,np.integer)) or run_epochs<1:
        raise ValueError('Training epochs must be a positive integer')
    if not data_manifest:validate_synthetic_splits(config)
    seed=int(config.get('seed',7));torch.manual_seed(seed);np.random.seed(seed)
    model=_configured_model(config,device)
    if mae_checkpoint and initial_checkpoint:raise ValueError('Choose full-checkpoint fine-tuning or MAE initialization, not both')
    if initial_checkpoint:
        weights=torch.load(initial_checkpoint,map_location=device,weights_only=True)
        model.load_state_dict(weights['model'])
    elif mae_checkpoint:
        weights=torch.load(mae_checkpoint,map_location=device,weights_only=True)
        model.load_state_dict(weights['backbone'])
    if data_manifest:
        from .recorded_dataset import load_recorded_dataset
        split=load_recorded_dataset(data_manifest,config);training=split['train'];validation=split['validation']
        if not training or not validation:raise ValueError('Fine-tuning requires independent training and validation scenes')
        progress('Streaming annotated lab recordings; labels remain separate from observations')
    else:
        progress('Generating independent training and validation scenes...')
        training=dataset(cfg['train_seeds'],cfg['frames_per_scene'],config)
        validation=dataset(cfg['validation_seeds'],max(4,cfg['frames_per_scene']//2),config)
    learning_rate=cfg.get('fine_tune_learning_rate',cfg['learning_rate']*.1) if initial_checkpoint else cfg['learning_rate']
    optimizer=torch.optim.AdamW(model.parameters(),lr=learning_rate,weight_decay=.0001)
    output=Path(output);output.parent.mkdir(parents=True,exist_ok=True)
    root=Path(__file__).resolve().parents[1]
    recipe_files=[*sorted((root/'core').glob('*.py')),*sorted((root/'perception_ml').glob('*.py')),
                  *sorted((root/'slam_and_twin').glob('*.py')),root/'ingestion/synthetic_lab_simulator.py',
                  root/'ingestion/sensor_synchronizer.py']
    recipe={'source_sha256':{str(path.relative_to(root)).replace('\\','/'):hashlib.sha256(path.read_bytes()).hexdigest() for path in recipe_files},
            'configuration':config,'epochs_requested':int(run_epochs),
            'ground_truth_version':'clean_material_world_v1' if not data_manifest else 'annotated_recording_v1',
            'environment':{'python':sys.version,'platform':platform.platform(),'torch':str(torch.__version__),'numpy':str(np.__version__)},
            'mae_checkpoint_sha256':hashlib.sha256(Path(mae_checkpoint).read_bytes()).hexdigest() if mae_checkpoint else None}
    history=[];best=float('inf');started=time.perf_counter()
    for epoch in range(run_epochs):
        model.train();losses=[]
        # Shuffle complete sequences, never frames: temporal memory stays causal.
        order=np.random.default_rng(seed+epoch).permutation(len(training))
        for scene_index in order:
            _,tree,sequence=training[scene_index];previous=None;memory=SparseMultiReturnVoxelMap(config['mapping']['voxel_size']);ages={}
            for frame,truth,points in sequence:
                optimizer.zero_grad(set_to_none=True)
                prepared=prepare_training_frame(model,frame,points,tree,config,device,memory,ages,None if previous is None else previous[0])
                kwargs=dict(prepared.inputs)
                if previous is not None:kwargs['coords_prev'],kwargs['cached_stage2_prev']=previous
                logits,flow,embedding=model(**kwargs)
                labels,target,valid,instances=tensors(prepared,truth,device)
                loss,details=multitask_loss(logits,flow,labels,target,valid,kwargs['coords_t'],instances)
                if truth.camera_semantic is not None and prepared.camera_logits is not None:
                    target_image=torch.as_tensor(np.array(truth.camera_semantic,copy=True),device=device,dtype=torch.long)[None]
                    if target_image.shape[-2:]!=prepared.camera_logits.shape[-2:]:
                        target_image=F.interpolate(target_image[:,None].float(),size=prepared.camera_logits.shape[-2:],mode='nearest')[:,0].long()
                    if (target_image>=0).any():loss=loss+.25*F.cross_entropy(prepared.camera_logits,target_image,ignore_index=-1)
                if not torch.isfinite(loss):raise FloatingPointError('Non-finite training loss')
                loss.backward();torch.nn.utils.clip_grad_norm_(model.parameters(),5);optimizer.step()
                previous=(kwargs['coords_t'].detach(),embedding.detach());losses.append(float(loss.detach()))
        report=evaluate_model(model,validation,config,device)
        report.update(epoch=epoch+1,training_loss=float(np.mean(losses)),elapsed_seconds=time.perf_counter()-started)
        history.append(report);progress(json.dumps(report))
        # Validation objective measures learned semantics AND dynamic flow, not training loss.
        score=(1-report['semantic_miou'])+5*report['dynamic_flow_epe_m']
        if score<best:
            best=score
            payload={'model':model.state_dict(),'metadata':{'schema_version':1,'synthetic_only':True if not data_manifest else None,
                'data_source':'annotated_recordings' if data_manifest else 'procedural_simulator','seed':seed,
                'recipe':recipe,
                'train_seeds':cfg['train_seeds'] if not data_manifest else [],'validation_seeds':cfg['validation_seeds'] if not data_manifest else [],'epoch':epoch+1,
                'train_scene_ids':[str(s[0]) for s in training],'validation_scene_ids':[str(s[0]) for s in validation],
                'train_recordings':[str(s[2].recording.resolve()) for s in training if hasattr(s[2],'recording')],
                'validation_recordings':[str(s[2].recording.resolve()) for s in validation if hasattr(s[2],'recording')],
                'dataset_manifest':str(data_manifest) if data_manifest else None,'initial_checkpoint':str(initial_checkpoint) if initial_checkpoint else None,
                'learning_rate':float(learning_rate),
                'dense_rgbd_fusion':True,'mae_initialization':str(mae_checkpoint) if mae_checkpoint else None,
                'voxel_size':config['mapping']['voxel_size'],
                'sampling':_sampling_settings(config,model),'sampling_constants':_sampling_constants(),
                'descriptor_order':'8 specified + eigenvalue_sum + height_std'},
                'validation':report}
            temporary=output.with_suffix('.tmp');torch.save(payload,temporary);temporary.replace(output)
        output.with_suffix('.history.json').write_text(json.dumps(history,indent=2),encoding='utf-8')
    return history


def evaluate_checkpoint(checkpoint,config,output=None,device='cpu',data_manifest=None):
    model=_configured_model(config,device)
    data=torch.load(checkpoint,map_location=device,weights_only=True);model.load_state_dict(data['model'])
    if data_manifest:
        from .recorded_dataset import load_recorded_dataset
        scenes=load_recorded_dataset(data_manifest,config)['test']
        if not scenes:raise ValueError('Recorded evaluation requires a held-out test split')
        used=set(data['metadata'].get('train_scene_ids',[])+data['metadata'].get('validation_scene_ids',[]))
        if any(str(s[0]) in used for s in scenes):raise ValueError('Evaluation scenes overlap training or validation')
        used_paths=set(data['metadata'].get('train_recordings',[])+data['metadata'].get('validation_recordings',[]))
        if any(str(s[2].recording.resolve()) in used_paths for s in scenes):raise ValueError('Evaluation recording paths overlap training or validation')
    else:
        seeds=config['training']['test_seeds']
        if set(seeds)&set(data['metadata']['train_seeds']+data['metadata']['validation_seeds']):
            raise ValueError('Evaluation seeds overlap training or validation')
        scenes=dataset(seeds,config['training']['frames_per_scene'],config)
    result=evaluate_model(model,scenes,config,device)
    result['synthetic_only']=True if not data_manifest else None;result['checkpoint_metadata']=data['metadata']
    result['semantic_beats_baseline']=result['semantic_miou']>result['majority_class_miou']
    result['dynamic_flow_beats_baseline']=result['dynamic_flow_epe_m']<result['dynamic_zero_flow_epe_m']
    if output:Path(output).write_text(json.dumps(result,indent=2),encoding='utf-8')
    return result
