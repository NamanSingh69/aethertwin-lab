"""Assemble measured release evidence; never infer physical verification."""
from pathlib import Path
from datetime import datetime,timezone
import csv,hashlib,json,xml.etree.ElementTree as ET
import numpy as np

ROOT=Path(__file__).resolve().parents[1]


def read(name):return json.loads((ROOT/name).read_text(encoding='utf-8'))
def digest(path):
    with Path(path).open('rb') as stream:return hashlib.file_digest(stream,'sha256').hexdigest()
def tests(name):
    s=ET.parse(ROOT/name).getroot().find('testsuite')
    result={key:int(s.attrib[key]) for key in ('tests','errors','failures','skipped')}
    return {**result,'passed':result['tests']-result['errors']-result['failures']-result['skipped'],
            'seconds':float(s.attrib['time']),'evidence':name}


def main():
    benchmark=read('artifacts/aethertwin_endurance_final/benchmark.json')
    endurance=read('artifacts/aethertwin_endurance_final/run_summary.json')
    rendering=read('artifacts/browser_stress_1080p_100k.json')
    gateway=read('artifacts/gateway_actual_pipeline/report.json')
    workflows=read('reports/windows_aethertwin.json')
    interactive=read('reports/interactive_browser.json')
    vitest=read('web/reports/vitest-release.json')
    wheel=ROOT/'dist/release/lidar_lab_digital_twin-2.0.0-py3-none-any.whl'
    with (ROOT/'artifacts/aethertwin_endurance_final/process_samples.csv').open() as stream:rows=list(csv.DictReader(stream))
    requests=np.array([float(r['request_ms']) for r in rows if not r['error']])
    http={'successful_samples':len(requests),'p50_ms':float(np.percentile(requests,50)),
          'p95_ms':float(np.percentile(requests,95)),'startup_or_failed_samples':sum(bool(r['error']) for r in rows)}
    source_changes=endurance['source_changes_during_run']
    backend_unchanged=all(name.startswith('web/dist/') for name in source_changes)
    assert endurance['completed'] and endurance['runtime_error'] is None and not endurance['interrupted_by_suspend']
    assert endurance['recording']['complete'] and endurance['recording']['dropped_frames']==0
    assert rendering['completed'] and not rendering['hidden_during_measurement'] and gateway['passed']
    assert all(case['passed'] for case in workflows['checks'].values()) and workflows['record_replay_equal']
    assert read('reports/cross_platform_aethertwin_replay.json')['passed']
    assert interactive['passed'] and backend_unchanged
    result={'schema_version':2,'generated_utc':datetime.now(timezone.utc).isoformat(),
        'status':'software_release_verified_physical_acceptance_pending','version':'2.0.0',
        'tests':{'windows':tests('reports/windows-aethertwin.xml'),
                 'ubuntu':tests('reports/ubuntu-aethertwin-release.xml'),
                 'frontend':{'passed':vitest['numPassedTests'],'skipped':vitest['numPendingTests'],
                             'failed':vitest['numFailedTests'],'evidence':'web/reports/vitest-release.json'}},
        'wheel':{'path':str(wheel.relative_to(ROOT)),'bytes':wheel.stat().st_size,'sha256':digest(wheel)},
        'spec_sha256':digest(ROOT/'SPEC.md'),'canonical_checkpoint':read('reports/canonical_checkpoint.json'),
        'locks_sha256':{name:digest(ROOT/name) for name in ('requirements-windows.lock','requirements-ubuntu.lock','web/package-lock.json')},
        'windows_fresh_hash_locked_install':read('reports/windows_installed_release.json'),
        'ubuntu_evidence':'reports/ubuntu_aethertwin.json',
        'cross_platform_replay':read('reports/cross_platform_aethertwin_replay.json'),
        'endurance':{'frames':benchmark['frames'],'wall_seconds':endurance['wall_seconds'],
            'processing_latency_ms':benchmark['processing_latency_ms'],'end_to_end_fps':benchmark['end_to_end_fps'],
            'rss_mb':benchmark['rss_mb'],'recording':endurance['recording'],'telemetry':endurance['final_telemetry'],
            'http_health':http,'backend_source_unchanged':backend_unchanged,'frontend_changes':source_changes,
            'conditions':'Windows CPU simulation, reduced raw resolution; concurrent verification/builds, browser clients and one 38.74s interactive pause. Frontend health/material fixes were rebuilt; final renderer tested separately for60s. No Jetson claim.',
            'evidence':'artifacts/aethertwin_endurance_final'},
        'rendering':rendering,'interactive_browser':'reports/interactive_browser.json',
        'gateway_actual_pipeline':'artifacts/gateway_actual_pipeline/report.json',
        'commissioning_actual_jobs':'artifacts/commissioning_actual_jobs_final/report.json',
        'physical_sensors_verified':False,'jetson_arm64_runtime_built':False,'tensorrt_gpu_verified':False,
        'firmware_flashed':False,'real_lab_accuracy_verified':False,
        'external_deployment_performed':False,
        'pending_physical':['Jetson image/wheel/GPU validation','connected L2/Gemini/ESP32 board and profiles',
            'measured extrinsics and time synchronization','surveyed dimensions and fixed-mount localization',
            'representative labelled real-lab evaluation','Jetson thermal/throttling/live endurance validation']}
    assert all(result['tests'][os]['failures']==0 and result['tests'][os]['errors']==0 for os in ('windows','ubuntu'))
    (ROOT/'reports/release_validation.json').write_text(json.dumps(result,indent=2)+'\n',encoding='utf-8')
    p=benchmark['processing_latency_ms'];m=benchmark['rss_mb'];f=rendering['frame_interval_ms_last_1200']
    text=f'''# Measured AetherTwin release

Windows laptop: AMD Ryzen7 8840HS, Radeon780M,31.3GB usable RAM, Python3.12, CPU PyTorch.
These are synthetic observations at reduced resolution. Physical sensors and Jetson performance remain unverified.

| Measurement | Final result |
|---|---:|
| Pipeline observations / wall duration | {benchmark['frames']} / {endurance['wall_seconds']:.1f}s |
| End-to-end processing | {benchmark['end_to_end_fps']:.3f}frames/s |
| Processing median / p95 / maximum | {p['p50']:.1f} / {p['p95']:.1f} / {p['max']:.1f}ms |
| Process memory first / last / peak | {m['first']:.1f} / {m['last']:.1f} / {m['max']:.1f}MiB |
| Final-half memory trend | {m['last_half_slope_mb_per_frame']:.4f}MiB/frame |
| Recorded frames / dropped frames | {endurance['recording']['written_frames']} / 0 |
| Encoder coalesced snapshots | {endurance['final_telemetry']['coalesced_snapshots']} |
| Health request median / p95 | {http['p50_ms']:.1f} / {http['p95_ms']:.1f}ms |
| Fixed1080p100,000-instance renderer | {rendering['mean_render_fps']:.2f}FPS mean |
| Lowest one-second render window | {rendering['minimum_window_fps']:.2f}FPS |
| Recent render-frame p50 / p95 / p99 | {f['p50']:.1f} / {f['p95']:.1f} / {f['p99']:.1f}ms |
| Stress-scene draw calls / triangles | {rendering['draw_calls']} / {rendering['triangles']:,} |

The35ms pipeline target remains future work. The fluid renderer has an independent
budget and does not disguise old observations as fresh data. Stress data is an
explicit static injection, separated from runtime perception. Its drawing buffer
was1920×1080 at pixel ratio1; the displayed canvas was1225×689. Frame percentiles
cover the final1200 intervals; FPS averages56 one-second windows after5s warmup.
Final colors were visually checked after correcting the instance material.

## Conditions and provenance

The30-minute run enabled all runtime algorithms, virtual halo, raw recording,
bounded tiled occupancy and browser telemetry. It used2400LiDAR beams,160×100RGB-D,
stride3,1400neural voxels,16spatial/12temporal neighbors,0.18m occupancy and0.25m
planning cells. Active map cache:128tiles with the same64MiB byte ceiling and4GiB
disk quota. Detailed configuration and source/checkpoint hashes are embedded in
the benchmark and run_provenance.json. Original calibrated observations were
recorded before deskew/preprocessing; the new capture contains no S2 stream.

Verification, installation/build work and multiple browser clients overlapped the
run. Interactive pause/resume held processing for38.74s; end-to-end FPS includes
that hold. One observer request preceded listener readiness; all1763 later samples
succeeded. The power clock found no standby interruption. Backend sources stayed
unchanged. Frontend availability warnings and the material color fix were rebuilt
during the interval; the final color-corrected renderer has a separate60s test.
This is a backend endurance result with final browser verification, rather than
a claim of30minutes on one immutable frontend build.

The bounded profile trades disk-backed map capacity for processing cost. Its
overall throughput is reported as measured. Component-specific native ray and
grouped-tile speedups are documented separately and are not overall FPS claims.
Training/reconstruction/pretraining are offline workflows with independent CLI
proofs. Tiny smoke-training weights never replace the canonical checkpoint.

Evidence: artifacts/aethertwin_endurance_final/,artifacts/browser_stress_1080p_100k.json,
reports/cross_platform_aethertwin_replay.json and reports/release_validation.json.
Full raw endurance captures remain in artifacts/aethertwin_endurance_final_raw/
in the workspace; the portable archive includes smaller sample recordings.
Earlier baselines, tuning attempts and the black-material screenshot are retained
as historical evidence and are not the final rendering acceptance result.
'''
    (ROOT/'reports/BENCHMARK.md').write_text(text,encoding='utf-8')
    print(json.dumps({'tests':result['tests'],'status':result['status'],'wheel':result['wheel']},indent=2))


if __name__=='__main__':main()
