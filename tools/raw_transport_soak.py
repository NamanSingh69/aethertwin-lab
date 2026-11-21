"""Soak the raw-sensor transport over loopback with a synthetic capture node in its own process.

This measures the transport and receiver on this host only: synthetic devices,
loopback TCP, no Pi, no L2, no Gemini. It is not a hardware or network result.

    python tools/raw_transport_soak.py --duration 600 --out reports/raw_transport/soak_<host>.json
"""
from __future__ import annotations
import argparse,json,os,platform,subprocess,sys,tempfile,threading,time
from datetime import datetime,timezone
from pathlib import Path
import numpy as np

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))


def codec_bench(width,height,frames=20,seed=0):
    """Encode/decode cost and size per codec on synthetic frames (smooth depth + noise, a gradient image)."""
    from ingestion import raw_transport as rt
    from ingestion.capture_node import SyntheticDevices
    devices=SyntheticDevices(seed=seed,width=width,height=height);rng=np.random.default_rng(seed);results={}
    depths=[np.clip(devices.base_depth+rng.normal(0,3,devices.base_depth.shape),0,65535).astype(np.uint16) for _ in range(frames)]
    colors=[np.roll(devices.base_rgb,i,axis=1) for i in range(frames)]
    for kind,codecs,images,encode,decode in (('depth',rt.DEPTH_CODECS,depths,rt.encode_depth,rt.decode_depth),
                                              ('color',('raw','png','jpeg'),colors,rt.encode_color,rt.decode_color)):
        for codec in codecs:
            sizes=[];enc=[];dec=[]
            for image in images:
                t=time.perf_counter();data=encode(image,codec);enc.append(1000*(time.perf_counter()-t));sizes.append(len(data))
                shape=image.shape[:2];t=time.perf_counter();decode(data,codec,shape);dec.append(1000*(time.perf_counter()-t))
            results[f'{kind}_{codec}']={'bytes_mean':float(np.mean(sizes)),'ratio':float(images[0].nbytes/np.mean(sizes)),
                'encode_ms_p50':float(np.median(enc)),'decode_ms_p50':float(np.median(dec)),'megabytes_per_second_at_30fps':float(np.mean(sizes)*30/1e6)}
    return results


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__.split('\n')[0])
    p.add_argument('--duration',type=float,default=600.);p.add_argument('--out',type=Path,required=True)
    p.add_argument('--width',type=int,default=1280);p.add_argument('--height',type=int,default=800);p.add_argument('--fps',type=float,default=30.)
    p.add_argument('--imu-hz',type=float,default=250.);p.add_argument('--lidar-hz',type=float,default=10.);p.add_argument('--points',type=int,default=6400)
    p.add_argument('--depth-codec',default='raw');p.add_argument('--color-codec',default='jpeg');p.add_argument('--port',type=int,default=7611)
    p.add_argument('--depth-level',type=int,default=1);p.add_argument('--color-quality',type=int,default=90)
    p.add_argument('--camera-forward',default='all',help='all, or sweep (only frames fusable with a LiDAR sweep; hotspot profile)')
    p.add_argument('--fragment-bytes',type=int,default=1024*1024)
    p.add_argument('--sample-seconds',type=float,default=10.);p.add_argument('--consumer-ms',type=float,default=0.,help='Simulated processing time per frame')
    args=p.parse_args(argv)
    import psutil
    from ingestion import raw_transport as rt
    from ingestion.remote_source import RemoteSensorSource
    from core.bounded_acquisition import BufferedAcquisition
    work=Path(tempfile.mkdtemp(prefix='aethertwin-soak-'));key=rt.generate_key(work/'node.key')
    calibration=work/'calibration.json'
    calibration.write_text(json.dumps({'calibration_id':'soak-synthetic','measured':True,'image_size':[args.width,args.height],
        'K':[[900.,0,args.width/2],[0,900.,args.height/2],[0,0,1]],'distortion':[0]*5,'T_lidar_camera':np.eye(4).tolist(),'T_lidar_imu':np.eye(4).tolist()}))
    config=work/'config'/'node.yaml';config.parent.mkdir()
    import yaml
    config.write_text(yaml.safe_dump({'capture_node':{'key_file':str(key),'depth_codec':args.depth_codec,'color_codec':args.color_codec,
        'depth_level':args.depth_level,'color_quality':args.color_quality,'camera_forward':args.camera_forward,'fragment_bytes':args.fragment_bytes,
        'synthetic':{'seed':7,'imu_hz':args.imu_hz,'lidar_hz':args.lidar_hz,'points':args.points,'camera_fps':args.fps,'width':args.width,'height':args.height}}}))
    node=subprocess.Popen([sys.executable,'-m','ingestion.capture_node','--config',str(config),'--synthetic','--bind','127.0.0.1','--port',str(args.port),
        '--allow','127.0.0.1','--status-interval',str(args.sample_seconds),'--duration',str(args.duration+30)],cwd=ROOT,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True)
    node_lines=[];threading.Thread(target=lambda:[node_lines.append(line) for line in node.stdout],daemon=True).start()
    time.sleep(2.)
    source=RemoteSensorSource({'capture_host':'127.0.0.1','capture_port':args.port,'calibration':str(calibration),'allow_synthetic_capture':True,
        'capture_key_file':str(key)},ROOT)
    acquisition=BufferedAcquisition(source,max_frames=2).start()
    me=psutil.Process();node_process=psutil.Process(node.pid)
    # A Windows venv python.exe is a launcher; measure the interpreter it starts.
    node_process=next(iter(node_process.children()),node_process)
    # CPU is percent of one logical core over each sample window (psutil), node and receiver processes separately.
    node_process.cpu_percent(None);me.cpu_percent(None)
    samples=[];delivered=[0];errors=[];stop=threading.Event()
    def consume():
        try:
            for frame in acquisition:
                delivered[0]+=1
                if args.consumer_ms:time.sleep(args.consumer_ms/1000)
                if stop.is_set():return
        except BaseException as exc:errors.append(repr(exc))
    consumer=threading.Thread(target=consume,daemon=True);consumer.start()
    started=time.monotonic();next_sample=started+args.sample_seconds
    try:
        while time.monotonic()-started<args.duration and consumer.is_alive():
            time.sleep(max(0.,min(1.,next_sample-time.monotonic())))
            if time.monotonic()<next_sample:continue
            next_sample+=args.sample_seconds;status=source.status()
            samples.append({'t':time.monotonic()-started,'delivered_frames':delivered[0],'state':status['state'],'segment':status['segment'],
                'rates_hz':status['rates_hz'],'bandwidth_bytes_per_second':status['bandwidth_bytes_per_second'],
                'arrival_latency_ms':status['arrival_latency_ms'],'clock':{k:status['clock'][k] for k in ('residual_seconds','best_delay_seconds','jitter_seconds')},
                'receiver_rss_bytes':me.memory_info().rss,'node_rss_bytes':node_process.memory_info().rss,
                'node_cpu_percent':node_process.cpu_percent(None),'receiver_cpu_percent':me.cpu_percent(None),
                'receiver_queue_packets':source.reader.depth(),'acquisition':acquisition.status(),'node':status['node']})
            print(json.dumps({k:samples[-1][k] for k in ('t','delivered_frames','state','segment','bandwidth_bytes_per_second','receiver_rss_bytes')}),flush=True)
    finally:
        stop.set();final=source.status();acquisition.close();node.terminate()
        try:node.wait(10)
        except subprocess.TimeoutExpired:node.kill()
    elapsed=time.monotonic()-started
    def series(key):return [s[key] for s in samples if s.get(key) is not None]
    rss=series('receiver_rss_bytes');node_rss=series('node_rss_bytes');bw=series('bandwidth_bytes_per_second')
    # The first window includes start-up; leave it out of the CPU means when there are later ones.
    node_cpu=series('node_cpu_percent')[1:] or series('node_cpu_percent');receiver_cpu=series('receiver_cpu_percent')[1:] or series('receiver_cpu_percent')
    node_status=next((json.loads(line) for line in reversed(node_lines) if line.startswith('{') and '"event": "status"' in line),{})
    import cv2
    report={'schema_version':1,'kind':'raw_transport_loopback_soak','label':'synthetic devices over loopback on this host; not a Pi, network or sensor result',
        'date_utc':datetime.now(timezone.utc).isoformat(),
        'environment':{'host':platform.node(),'platform':platform.platform(),'processor':platform.processor(),'cpu_count':os.cpu_count(),
                       'python':platform.python_version(),'numpy':np.__version__,'opencv':cv2.__version__,'memory_bytes':psutil.virtual_memory().total},
        'settings':vars(args)|{'out':str(args.out)},
        'summary':{'elapsed_seconds':elapsed,'delivered_frames':delivered[0],'delivered_hz':delivered[0]/elapsed if elapsed else None,
            'consumer_errors':errors,'segments':final['segment'],'connections':final['connections'],'stalls':final['stalls'],'clock_jumps':final['clock_jumps'],
            'imu_samples':final['imu_samples'],'imu_sequence_breaks':final['imu_sequence_breaks'],'imu_missing_samples':final['imu_missing_samples'],
            'clouds':final['clouds'],'cloud_missing':final['cloud_missing'],'camera_frames':final['camera_frames'],'camera_missing':final['camera_missing'],
            'camera_incomplete':final['camera_incomplete'],'receiver_overflows':final['receiver_overflows'],'decode_errors':final['decode_errors'],
            'node_outbox':final['node'],'acquisition':samples[-1]['acquisition'] if samples else None,
            'bandwidth_megabytes_per_second':{'mean':float(np.mean(bw))/1e6 if bw else None,'max':float(np.max(bw))/1e6 if bw else None},
            'arrival_latency_ms_final_window':final['arrival_latency_ms'],'camera_decode_ms_p95':final['camera_decode_ms_p95'],
            'receiver_rss_megabytes':{'first':rss[0]/1e6,'last':rss[-1]/1e6,'max':max(rss)/1e6} if rss else None,
            'node_rss_megabytes':{'first':node_rss[0]/1e6,'last':node_rss[-1]/1e6,'max':max(node_rss)/1e6} if node_rss else None,
            'clock':final['clock'],
            'cpu_percent_of_one_core':{'node_mean':float(np.mean(node_cpu)) if node_cpu else None,'receiver_mean':float(np.mean(receiver_cpu)) if receiver_cpu else None},
            'node_devices':node_status.get('devices')},
        'codec_bench':codec_bench(args.width,args.height),'timeline':samples,'node_log_tail':node_lines[-3:]}
    args.out.parent.mkdir(parents=True,exist_ok=True);args.out.write_text(json.dumps(report,indent=2,default=str),encoding='utf-8')
    print(json.dumps(report['summary'],indent=2,default=str))
    return 0 if not errors and final['imu_sequence_breaks']==0 and final['segment']==1 else 1


if __name__=='__main__':sys.exit(main())
