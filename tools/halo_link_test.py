"""Link test for a flashed Aether Halo board: identity, packets, heartbeats and receiver robustness.

1. The real HaloController drives the board for --seconds with a synthetic snapshot (a person 2.5 m
   ahead): the board must identify as a known profile, keep heartbeating and count every packet.
2. Raw serial: after a fresh handshake, a valid packet, a corrupted packet, half a packet, a pause and
   another valid packet; the board's own counters must show +2 accepted, a rejected block and an
   inter-byte timeout, and it must keep heartbeating.
LED output is not observed here (no ring needed); the report says so. Nothing is written to flash.

    python tools/halo_link_test.py --port COM7 --output reports/hardware/halo/link_test_esp32s3.json
"""
from __future__ import annotations
import argparse,json,platform,sys,time
from datetime import datetime,timezone
from pathlib import Path
from types import SimpleNamespace

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))


def heartbeats(port,seconds,buffer):
    """Read for `seconds`; return parsed AETHER_HALO_ALIVE lines and any other lines."""
    alive=[];other=[];end=time.monotonic()+seconds
    while time.monotonic()<end:
        buffer.extend(port.read(256))
        while b'\n' in buffer:
            line,_,rest=buffer.partition(b'\n');buffer[:]=rest;line=bytes(line).rstrip(b'\r')
            fields=line.split()
            if line.startswith(b'AETHER_HALO_ALIVE') and len(fields)==5 and all(f.isdigit() for f in fields[1:]):
                alive.append(dict(zip(('uptime_ms','accepted','rejected','timeouts'),map(int,fields[1:]))))
            elif line:other.append(line.decode('ascii','replace')[:120])
        time.sleep(.01)
    return alive,other


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__.split('\n')[0])
    p.add_argument('--port',required=True);p.add_argument('--seconds',type=float,default=8.);p.add_argument('--output',type=Path,required=True)
    args=p.parse_args(argv)
    import numpy as np,serial
    from hardware_halo import HaloController,halo_state,encode_packet
    from hardware_halo.protocol import HELLO,PROFILE_BY_IDENTITY
    report={'schema_version':1,'kind':'halo_link_test','date_utc':datetime.now(timezone.utc).isoformat(),'port':args.port,
            'label':'physical board, serial link only; LED output not observed','leds_verified':False,'hardware_verified':False,
            'host':{'name':platform.node(),'platform':platform.platform(),'python':platform.python_version(),'pyserial':serial.__version__}}
    person=SimpleNamespace(semantic_class=2,confidence=.9,position=np.array([2.5,0.,0.]),track_id=1)
    controller=HaloController({'enabled':True,'port':args.port,'handshake_timeout_seconds':5.})
    try:
        end=time.monotonic()+args.seconds;samples=[]
        while time.monotonic()<end:
            controller.publish(SimpleNamespace(frame_id=1,timestamp=0.,processed_at_unix=time.time(),source_mode='test',diagnostics={'processing_ms':20.},
                                               sensor_pose_world=np.eye(4),entities=[person],geofences=[]))
            time.sleep(.1);status=controller.status()
            beat=status['device_heartbeat']
            if beat and (not samples or beat['device_uptime_ms']!=samples[-1]['device_uptime_ms']):samples.append(beat)  # one per heartbeat
        status=controller.status()
    finally:controller.close()
    keep=('connected','compatible','board_profile','packets_sent','connections','device_heartbeat','heartbeat_timeouts','last_error','last_device_error')
    report['controller']={k:status.get(k) for k in keep}|{'virtual_severity':status['virtual'].get('severity'),'heartbeats_seen':len(samples)}
    first,last=(samples[0],samples[-1]) if samples else (None,None)
    report['controller']['accepted_growth']=None if not samples else last['accepted_packets']-first['accepted_packets']
    # Raw robustness: fresh handshake, then valid / corrupted / truncated input.
    time.sleep(.5);buffer=bytearray()
    with serial.Serial(port=args.port,baudrate=115200,timeout=0,write_timeout=.5) as port:
        identity=None;end=time.monotonic()+5.
        while identity is None and time.monotonic()<end:
            port.write(HELLO);time.sleep(.25);buffer.extend(port.read(512))
            for line in bytes(buffer).split(b'\n'):
                if line.rstrip(b'\r') in PROFILE_BY_IDENTITY:identity=line.rstrip(b'\r')
        buffer.clear()
        before,_=heartbeats(port,2.2,buffer)
        state=halo_state(SimpleNamespace(frame_id=1,timestamp=0.,processed_at_unix=time.time(),source_mode='test',diagnostics={'processing_ms':20.},
                                         sensor_pose_world=np.eye(4),entities=[person],geofences=[]),now=time.time())
        valid=encode_packet(state,40000);corrupt=bytearray(encode_packet(state,40001));corrupt[9]^=0xFF
        # The receiver discards a stale partial packet when the next byte arrives, so a valid packet follows the pause.
        port.write(valid);time.sleep(.05);port.write(bytes(corrupt));time.sleep(.05);port.write(encode_packet(state,40002)[:7]);time.sleep(.2)
        port.write(encode_packet(state,40003));time.sleep(.05)
        after,other=heartbeats(port,2.5,buffer)
    report['identity']=None if identity is None else identity.decode()
    report['board_profile']=None if identity is None else PROFILE_BY_IDENTITY[identity]
    b,a=(before[-1] if before else None),(after[-1] if after else None)
    report['robustness']={'before':b,'after':a,'other_lines':other[:10],
        'accepted_delta':None if not (a and b) else a['accepted']-b['accepted'],
        'rejected_delta':None if not (a and b) else a['rejected']-b['rejected'],
        'timeouts_delta':None if not (a and b) else a['timeouts']-b['timeouts']}
    r=report['robustness'];c=report['controller']
    checks={'identified':report['board_profile'] is not None and c['compatible'] and c['board_profile']==report['board_profile'],
            'heartbeats':c['heartbeats_seen']>0 and c['heartbeat_timeouts']==0 and bool(after),
            'packets_accepted':bool(c['accepted_growth']) and c['accepted_growth']>0,
            'valid_packets_accepted':r['accepted_delta']==2,'corrupt_block_rejected':(r['rejected_delta'] or 0)>=1,
            'partial_packet_timed_out':(r['timeouts_delta'] or 0)>=1,'no_device_errors':c['last_device_error'] is None and not any('ERROR' in x for x in other)}
    report['checks']=checks;report['passed']=all(checks.values())
    args.output.parent.mkdir(parents=True,exist_ok=True);args.output.write_text(json.dumps(report,indent=2),encoding='utf-8')
    print(json.dumps({'passed':report['passed'],'checks':checks,'board_profile':report['board_profile']},indent=1))
    return 0 if report['passed'] else 1


if __name__=='__main__':sys.exit(main())
