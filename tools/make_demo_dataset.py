"""Create explicitly SYNTHETIC annotated recordings demonstrating lab-data I/O."""
from pathlib import Path
import argparse,json,sys
import numpy as np
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from ingestion.synthetic_lab_simulator import SyntheticLabSimulator
from ingestion.recorder_replayer import FrameRecorder
from slam_and_twin.static_twin_builder import StaticTwinBuilder
from perception_ml.recorded_dataset import correspondence_sidecar_arrays


def create_example(output,frames=3):
    output=Path(output).resolve()
    if output.exists():raise FileExistsError('Choose a new example directory; existing datasets are preserved')
    output.mkdir(parents=True)
    manifest={'schema_version':1,'data_source':'synthetic_example_annotations',
        'notes':'Simulator truth sidecars demonstrate annotation format. This is not measured real-lab evidence.',
        'splits':{'train':[],'validation':[],'test':[]}}
    for split,seed in zip(manifest['splits'],(31,32,33)):
        name=f'scene_{seed}';folder=output/name;folder.mkdir();labels=folder/'labels';labels.mkdir()
        sim=SyntheticLabSimulator(seed=seed,config={'lidar_points':1200,'width':96,'height':60})
        try:
            twin=StaticTwinBuilder(voxel_size=.12,poisson_depth=6).build(sim.commissioning_frames())
            twin.save(folder/'twin')
            with FrameRecorder(folder/'recording',chunk_size=2) as recorder:
                for _ in range(frames):
                    frame=sim.next_frame();truth=sim.last_ground_truth;recorder.write(frame)
                    np.savez_compressed(labels/f'{frame.frame_id:06d}.npz',
                        frame_id=frame.frame_id,timestamp=frame.timestamp,camera_timestamp=frame.camera_timestamp,
                        flow_frame='world',flow_direction='past_to_current',pose_world=truth.pose_world,
                        semantic=truth.semantic,flow=truth.flow,instance_ids=truth.instance_ids,flow_valid=truth.flow_valid,
                        camera_semantic=truth.camera_semantic,camera_flow=truth.camera_flow,
                        camera_instance_ids=truth.camera_instance_ids,camera_flow_valid=truth.camera_flow_valid,
                        **correspondence_sidecar_arrays(truth))
        finally:sim.close()
        manifest['splits'][split].append({'scene_id':name,'recording':f'{name}/recording',
            'labels':f'{name}/labels','twin':f'{name}/twin','mobile':False})
        print(f'Created {split}: {name}',flush=True)
    (output/'dataset.json').write_text(json.dumps(manifest,indent=2),encoding='utf-8')
    return output/'dataset.json'


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,default=ROOT/'assets/supervised_example')
    parser.add_argument('--frames',type=int,default=3)
    args=parser.parse_args()
    if args.frames<1:parser.error('--frames must be positive')
    print(create_example(args.output,args.frames))
