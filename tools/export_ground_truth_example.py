"""Export an explicitly synthetic sensor recording and separate exact GT pairs."""
from pathlib import Path
import argparse,json,sys
import numpy as np
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from ingestion.synthetic_lab_simulator import SyntheticLabSimulator
from ingestion.recorder_replayer import FrameRecorder
from perception_ml.recorded_dataset import correspondence_sidecar_arrays


def export_example(output,seed=17):
    output=Path(output).resolve()
    if output.exists():raise FileExistsError('Choose a new ground-truth example directory')
    output.mkdir(parents=True);labels=output/'labels';labels.mkdir()
    source=SyntheticLabSimulator(seed=seed,config={'lidar_points':1200,'width':80,'height':50},mobile=True)
    manifest={'schema_version':1,'data_source':'synthetic_exact_material_ground_truth',
        'seed':seed,'units':'metres_seconds','flow_direction':'past_to_current',
        'notes':'Truth-only clean mesh material pairs; never part of runtime sensor frames. Camera arrays are row-major flattened.',
        'recording':'recording','labels':'labels','frames':[]}
    try:
        with FrameRecorder(output/'recording',chunk_size=2,metadata={'synthetic':True,'labels_excluded':True}) as recorder:
            for _ in range(2):
                frame=source.next_frame();truth=source.last_ground_truth;recorder.write(frame)
                payload={key:getattr(truth,key) for key in ('semantic','flow','instance_ids','flow_valid','pose_world',
                    'camera_semantic','camera_flow','camera_instance_ids','camera_flow_valid')}
                payload.update(frame_id=frame.frame_id,timestamp=frame.timestamp,camera_timestamp=frame.camera_timestamp,
                    flow_frame='world',flow_direction='past_to_current',**correspondence_sidecar_arrays(truth))
                name=f'{frame.frame_id:06d}.npz';np.savez_compressed(labels/name,**payload)
                manifest['frames'].append({'frame_id':frame.frame_id,'label_file':name,
                    'lidar_pairs':len(truth.correspondences.valid),'valid_lidar_pairs':int(truth.correspondences.valid.sum()),
                    'camera_shape':list(frame.depth.shape),'valid_camera_pairs':int(truth.camera_correspondences.valid.sum())})
    finally:source.close()
    (output/'manifest.json').write_text(json.dumps(manifest,indent=2),encoding='utf-8')
    return output/'manifest.json'


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,default=ROOT/'assets/ground_truth_example')
    parser.add_argument('--seed',type=int,default=17)
    args=parser.parse_args();print(export_example(args.output,args.seed))
