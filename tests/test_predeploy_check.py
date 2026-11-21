"""tools/predeploy_check.py on temporary static builds: leak scan, size, vercel.json headers, local load simulation, 404 test.
Planted secrets are built by concatenation so this file itself never matches the patterns it plants."""
import gzip,importlib.util,json,sys
from pathlib import Path
import pytest

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('predeploy_check',ROOT/'tools/predeploy_check.py');pc=importlib.util.module_from_spec(spec);sys.modules['predeploy_check']=pc;spec.loader.exec_module(pc)
REAL_VERCEL=ROOT/'web/vercel.json'
INDEX=('<!doctype html><html><head><script type="module" crossorigin src="/assets/app-Ab12Cd34.js"></script>'
    '<link rel="stylesheet" href="/assets/app-Ef56Gh78.css"></head><body><div id="root"></div></body></html>')
APP='console.log("showcase")';CSS='body{margin:0}'


def site(tmp_path,extra=None,index=INDEX):
    files={'index.html':index,'assets/app-Ab12Cd34.js':APP,'assets/app-Ef56Gh78.css':CSS,**(extra or {})}
    for name,body in files.items():
        p=tmp_path/'build'/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(body if isinstance(body,bytes) else body.encode())
    return tmp_path/'build'


def fails(results,check=None):return [r for r in results if r['level']=='FAIL' and (check is None or r['check']==check)]


def vercel(tmp_path,cfg):p=tmp_path/'vercel.json';p.write_text(json.dumps(cfg));return p


PLANTS={'private-ip':'const node="'+'192.'+'168.'+'1.7'+'"','local-path':'const p="/ho'+'me/alice/rig"',
    'google-key':'const k="'+'AI'+'za'+'Sy'+'B'*33+'"','windows-path':'const p="C:\\\\Us'+'ers\\\\alice\\\\x"'}


@pytest.mark.parametrize('kind',sorted(PLANTS))
def test_planted_leak_fails_and_value_is_not_reported(tmp_path,kind):
    build=site(tmp_path,{'assets/leak-Zz99Yy88.js':PLANTS[kind]});results=pc.scan_leaks(build)
    bad=fails(results,'leak-scan');assert bad,results
    assert all('leak-Zz99Yy88.js' in r['detail'] for r in bad)
    value=PLANTS[kind].split('"')[1];assert not any(value in r['detail'] for r in results)


def test_sourcemap_file_and_comment_fail(tmp_path):
    build=site(tmp_path,{'assets/app-Ab12Cd34.js.map':'{"version":3}'});assert fails(pc.scan_leaks(build),'leak-scan')
    build2=site(tmp_path/'b',{'assets/x-Ab12Cd34.js':'x()\n//# source'+'MappingURL=x.js.map'});assert fails(pc.scan_leaks(build2),'leak-scan')


def test_gzipped_chunk_with_planted_ip_is_caught(tmp_path):
    chunk=gzip.compress(b'\x00\x01frame{"source":"'+b'10.'+b'0.4.2'+b'"}\x00')
    build=site(tmp_path,{'showcase/index.json':'{"chunks":["chunks/0-Ab12Cd34.bin.gz"]}','showcase/chunks/0-Ab12Cd34.bin.gz':chunk})
    bad=fails(pc.scan_leaks(build),'leak-scan');assert bad and any('0-Ab12Cd34.bin.gz' in r['detail'] for r in bad)


def test_device_words_endpoints_and_deny_words(tmp_path):
    build=site(tmp_path,{'assets/a-Ab12Cd34.js':'x.boot_id;y="w'+'s://h/x"'})
    details=' '.join(r['detail'] for r in fails(pc.scan_leaks(build),'leak-scan'));assert 'boot_id' in details and 'ws://' in details
    allowed=' '.join(r['detail'] for r in fails(pc.scan_leaks(build,allow_endpoint_files=['assets/a-Ab12Cd34.js']),'leak-scan'))
    assert 'boot_id' in allowed and 'ws://' not in allowed  # the endpoint allowance does not cover device identifiers
    secret='pi'+'login42';build2=site(tmp_path/'d',{'assets/b-Ab12Cd34.js':'u="'+secret+'"'})
    results=pc.scan_leaks(build2,deny_words=[secret]);assert fails(results,'leak-scan') and secret not in json.dumps(results)
    assert pc.repo_deny_words(ROOT),'tools/rig.py USER should provide the Pi login name'


def test_allowed_ip_literal_passes(tmp_path):
    ip='127.'+'0.0.1';build=site(tmp_path,{'assets/a-Ab12Cd34.js':'h="'+ip+'"'})
    assert fails(pc.scan_leaks(build),'leak-scan') and not fails(pc.scan_leaks(build,allow_ips=[ip]),'leak-scan')


def test_clean_build_passes_whole_gate(tmp_path):
    build=site(tmp_path);out=tmp_path/'report.json'
    code=pc.run([str(build),'--vercel-json',str(REAL_VERCEL),'--load-seconds','0.3','--concurrency','2','--json-out',str(out)])
    report=json.loads(out.read_text());assert code==0,[r for r in report['results'] if r['level']=='FAIL']
    assert report['summary']['FAIL']==0 and report['stats']['load']['requests_per_view']==3


def test_size_limits_fail_over_cap(tmp_path,monkeypatch):
    build=site(tmp_path);assert not fails(pc.check_size(build)[0])
    monkeypatch.setattr(pc,'MAX_FILES',2);assert fails(pc.check_size(build)[0],'size')


def test_vercel_json_missing_csp_fails_and_real_one_passes(tmp_path):
    cfg=json.loads(REAL_VERCEL.read_text());results,_=pc.check_vercel(REAL_VERCEL);assert not fails(results),fails(results)
    for rule in cfg['headers']:rule['headers']=[h for h in rule['headers'] if h['key'].lower()!='content-security-policy']
    assert fails(pc.check_vercel(vercel(tmp_path,cfg))[0],'vercel-json')


def test_vercel_json_missing_functions_and_wildcard(tmp_path):
    assert fails(pc.check_vercel(tmp_path/'nope.json')[0],'vercel-json')
    cfg=json.loads(REAL_VERCEL.read_text());cfg['functions']={'api/*.js':{'memory':128}};assert fails(pc.check_vercel(vercel(tmp_path,cfg))[0],'vercel-json')
    cfg=json.loads(REAL_VERCEL.read_text())
    for rule in cfg['headers']:
        for h in rule['headers']:
            if h['key']=='Content-Security-Policy':h['value']=h['value'].replace("connect-src 'self'","connect-src *")
    results=pc.check_vercel(vercel(tmp_path,cfg))[0];assert any(r['level']=='WARN' and 'connect-src' in r['detail'] for r in results)
    cfg=json.loads(REAL_VERCEL.read_text());cfg['headers']=[r for r in cfg['headers'] if r['source']!='/index.html']
    assert fails(pc.check_vercel(vercel(tmp_path,cfg))[0],'vercel-json')


def test_load_counts_requests_per_view_on_three_file_site(tmp_path):
    build=site(tmp_path);results,stats=pc.simulate_load(build,seconds=.3,concurrency=2)
    assert not fails(results),results
    assert stats['requests_per_view']==3 and stats['bytes_per_view']==len(INDEX)+len(APP)+len(CSS)
    assert stats['views_completed']>=1 and stats['http_5xx']==0 and stats['p95_ms']>=stats['p50_ms']>=0


def test_load_flags_dangling_reference(tmp_path):
    build=site(tmp_path,index=INDEX.replace('</head>','<script src="/assets/missing-Qq11Ww22.js"></script></head>'))
    bad=fails(pc.simulate_load(build,seconds=.2,concurrency=1)[0],'load');assert bad and any('missing-Qq11Ww22.js' in r['detail'] for r in bad)


def test_showcase_chunks_count_toward_page_view(tmp_path):
    build=site(tmp_path,{'showcase/index.json':'{"chunks":[{"file":"chunks/0-Ab12Cd34.bin.gz"},{"file":"chunks/1-Ef56Gh78.bin.gz"}]}',
        'showcase/chunks/0-Ab12Cd34.bin.gz':gzip.compress(b'a'),'showcase/chunks/1-Ef56Gh78.bin.gz':gzip.compress(b'b')})
    assert pc.simulate_load(build,seconds=.2,concurrency=1)[1]['requests_per_view']==6


def test_projection_thresholds():
    ok=pc.project_monthly(10,100_000,3000,3)[0];assert not fails(ok) and not any(r['level']=='WARN' for r in ok)  # 90k requests, 0.9 GB
    assert any(r['level']=='WARN' for r in pc.project_monthly(10,100_000,20_000,3)[0])  # 600k requests = 60%
    assert fails(pc.project_monthly(10,100_000,40_000,3)[0],'projection')  # 1.2M requests
    assert fails(pc.project_monthly(1,50_000_000,1000,3)[0],'projection')  # 150 GB transfer


def test_nonexistent_path_is_404_and_spa_rewrite_warns(tmp_path):
    build=site(tmp_path);results=pc.failure_tests(build);assert not fails(results) and any(r['level']=='PASS' for r in results)
    spa={'rewrites':[{'source':'/(.*)','destination':'/index.html'}]};results=pc.failure_tests(build,spa)
    assert any(r['level']=='WARN' and '404' in r['detail'] for r in results) and not fails(results)
