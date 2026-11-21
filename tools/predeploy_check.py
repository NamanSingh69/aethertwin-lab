"""Pre-deploy gate for a static site build: leak scan, size limits, vercel.json headers, local load simulation, 404 test.

Usage: python tools/predeploy_check.py <build dir> [--vercel-json PATH] [--visitors-per-month 3000] [--views-per-visit 3]
         [--load-seconds 5] [--concurrency 16] [--json-out report.json] [--allow-host HOST ...] [--allow-ip IP ...]
         [--allow-endpoint-file REL ...] [--deny-word WORD ...]
Prints PASS/WARN/FAIL lines and a summary; exits 1 on any FAIL. Works for any project: nothing here is AetherTwin specific
except the optional identifiers read from tools/rig.py and .local/rig.json next to this script (absent elsewhere).
It deploys nothing and contacts no host but the 127.0.0.1 server it starts on an ephemeral port.

Why this exists: a Vercel Hobby team shares 1,000,000 edge requests a month; a bot flood of 3.2M requests in one day on
another project soft-blocked the team (July 2026) and the one-time unblock is used. Every deploy runs this gate first.

Checks
1. Leak scan of every file (``*.gz`` decompressed first): private/loopback/CGNAT IPv4, local paths, key material, device
   and network identifiers (the Pi login name comes from tools/rig.py; only the pattern name is ever reported, never the
   matched value), local hostnames, backend endpoints (ws://, wss://, /api/v2, /ws/v2; allow a file only with
   --allow-endpoint-file), sourcemaps (*.map files, sourceMappingURL comments). External http(s) hosts are a WARN unless
   allowed with --allow-host. Known-safe IP literals pass only through --allow-ip.
2. Size: FAIL above Vercel Hobby's upload or file-count limit, WARN for a file over the Cloudflare single-file limit.
3. vercel.json: no functions, CSP and security headers on every path, immutable caching only for hashed names, no-cache
   for the HTML entry. Header sources are matched with a small path-to-regexp emulation (later rules override earlier).
4. Local load simulation: http.server (threaded, 127.0.0.1:0, background thread) emulating Vercel's cleanUrls, rewrites
   and 404 (no directory listings). One probe page view fetches index.html, everything it references, files the fetched
   JS/CSS reference that exist in the build, and every file under showcase/; then --concurrency virtual visitors repeat
   that view for --load-seconds. The monthly projection (visitors x views x requests per view) is CONSERVATIVE: it assumes
   no browser or CDN cache hits and no compression, and that every view loads every showcase chunk.
5. Failure tests: a nonexistent page and a nonexistent asset must 404 (an SPA fallback 200 hides broken links; WARN when
   vercel.json asks for it), and a path-traversal request must not 200.

web/vercel.json choices (JSON has no comments, so they live here)
- Static only: buildCommand ``npm run build:showcase`` writes ``showcase-dist`` with the Vercel project root at web/;
  framework null (no framework preset, no implicit functions), no ``functions``/``api/``, cleanUrls true.
- CSP is the Python service's (command_center/service.py CONTENT_SECURITY_POLICY): 'wasm-unsafe-eval' admits the meshopt
  glTF decoder (WebAssembly), worker-src 'self' blob: admits the scene decode worker, img-src/connect-src blob: admit
  embedded glTF images that GLTFLoader fetches from blob: URLs. connect-src is 'self' blob: only. PLACEHOLDER: if the
  showcase ever loads data from another origin (a CDN or R2 bucket for chunks), add exactly that origin to connect-src,
  never a wildcard or a bare scheme (this gate WARNs on those). upgrade-insecure-requests is added because Vercel is HTTPS.
- X-Content-Type-Options nosniff; Referrer-Policy no-referrer (a static showcase needs no referrer); Permissions-Policy
  denies camera, microphone, geolocation, USB, serial, Bluetooth and payment; HSTS two years with subdomains (no preload:
  that is a domain-wide commitment for the owner to make); X-Frame-Options DENY and COOP/CORP same-origin as
  belt-and-braces for old browsers and cross-origin reads.
- Cache-Control ``public, max-age=31536000, immutable`` for /assets/(.*) (Vite content-hashed names) and for /showcase/
  except index.json: an unhashed index cached for a year would pin returning visitors to an old chunk list, so it is
  no-cache like / and /index.html. Showcase chunk names must therefore be content-hashed (this gate WARNs otherwise).
"""
import argparse,gzip,html.parser,http.client,http.server,io,ipaddress,json,posixpath,re,secrets,sys,threading,time
from datetime import datetime,timezone
from functools import partial
from pathlib import Path
from urllib.parse import unquote,urljoin,urlsplit

ROOT=Path(__file__).resolve().parents[1]

# Platform limits. Sources and date: Vercel limits https://vercel.com/docs/limits and plans https://vercel.com/docs/plans/hobby
# (Hobby: 100 MB per CLI upload, 15,000 files per deployment, 1,000,000 edge requests and 100 GB fast data transfer per
# month, shared by the team); Cloudflare Pages limits https://developers.cloudflare.com/pages/platform/limits/ (25 MiB per
# file). Values as stated on 2026-11-05 in the deploy rule; not re-fetched by this tool. RE-CHECK THESE PAGES BEFORE EACH
# DEPLOY: plans change, and a stale cap is how a team gets blocked.
MAX_UPLOAD_BYTES=100*1000*1000;MAX_FILES=15_000;MAX_SINGLE_FILE=25*1024*1024
EDGE_REQUESTS_PER_MONTH=1_000_000;TRANSFER_BYTES_PER_MONTH=100*1000**3;WARN_FRACTION=.5
GZ_MAX=512*1024*1024;IMMUTABLE_MIN_AGE=31536000;HTML_MAX_AGE=600
LEVELS=('PASS','WARN','FAIL')

PRIVATE_NETS=[(ipaddress.ip_network(n),n) for n in ('10.0.0.0/8','172.16.0.0/12','192.168.0.0/16','127.0.0.0/8','169.254.0.0/16','100.64.0.0/10')]
IPV4=re.compile(r'(?<![\d.])((?:\d{1,3}\.){3}\d{1,3})(?![\d.]*\d)')
LEAKS=[('local path \\Users\\ or /Users/',re.compile(r'[\\/]+Users[\\/]+')),('local path /home/',re.compile(r'/home/')),
    ('private key block',re.compile(r'BEGIN[A-Z ]{0,40}PRIVATE KEY')),('Google API key',re.compile(r'AIza[0-9A-Za-z_\-]{35}')),
    ('GitHub token',re.compile(r'\bghp_[A-Za-z0-9]{36}|\bgithub_pat_[A-Za-z0-9_]{22,}')),('AWS access key id',re.compile(r'\bAKIA[0-9A-Z]{16}\b')),
    ('credential assignment',re.compile(r'(?i)\b(?:api[_-]?key|secret|password|passwd)\b["\']?\s*[:=]\s*["\'][^"\'\s]{16,}["\']')),
    ('64-hex string',re.compile(r'(?<![0-9A-Fa-f])[0-9A-Fa-f]{64}(?![0-9A-Fa-f])')),
    ('device identifier boot_id',re.compile(r'boot_id')),('device identifier node_session',re.compile(r'node_session')),
    ('network identifier hostname',re.compile(r'(?i)hostname')),('device name raspberrypi',re.compile(r'(?i)raspberrypi')),
    ('local hostname (.local/.lan/.internal/.home.arpa/localhost)',re.compile(r'(?i)(?://|@)(?:localhost\b|[a-z0-9-]+(?:\.[a-z0-9-]+)*\.(?:local|lan|internal|home\.arpa)\b)')),
    ('sourceMappingURL comment',re.compile(r'[#@]\s*sourceMappingURL\s*='))]
ENDPOINTS=[('backend endpoint ws://',re.compile(r'ws://')),('backend endpoint wss://',re.compile(r'wss://')),
    ('backend endpoint /api/v2',re.compile(r'/api/v2')),('backend endpoint /ws/v2',re.compile(r'/ws/v2'))]
URL_HOST=re.compile(r'https?://([A-Za-z0-9.-]+)')
SAFE_HOSTS={'www.w3.org'}  # XML/SVG namespace URIs: identifiers that are never fetched
HASHED=re.compile(r'[-.]([A-Za-z0-9_-]{8,})\.[A-Za-z0-9]')
JS_REF=re.compile(r'["\'`]((?:\.{1,2}/|/)?[\w./-]+\.(?:m?js|css|wasm|json|glb|gltf|bin|gz|png|jpe?g|webp|avif|svg|ktx2|woff2?|ttf|mp4|webm))["\'`]')
CSS_REF=re.compile(r'url\(\s*["\']?([^"\')]+)')


def result(level,check,detail):return {'level':level,'check':check,'detail':detail}


def files_of(build):return sorted(p for p in Path(build).rglob('*') if p.is_file())


def rel(build,p):return p.relative_to(build).as_posix()


def repo_deny_words(root=ROOT):
    """(label, value) identifiers of this repo's devices: the Pi login (tools/rig.py USER) and the found Pi's user/hostname."""
    words=[];rig=Path(root)/'tools/rig.py'
    if rig.is_file():
        m=re.search(r"\bUSER='([^']+)'",rig.read_text(encoding='utf-8',errors='replace'))
        if m:words.append(('Pi login name (tools/rig.py USER)',m.group(1)))
    state=Path(root)/'.local/rig.json'
    try:
        s=json.loads(state.read_text(encoding='utf-8'));pi=str(s.get('pi') or '')
        if '@' in pi:words.append(('Pi login name (.local/rig.json)',pi.split('@')[0]))
        if s.get('hostname'):words.append(('Pi hostname (.local/rig.json)',str(s['hostname'])))
    except (OSError,ValueError,AttributeError):pass
    return [(l,w) for l,w in words if len(w)>=3]


def file_text(p):
    """Decoded text (latin-1 keeps bytes 1:1) and a note; .gz files are scanned decompressed."""
    raw=p.read_bytes()
    if p.suffix=='.gz':
        try:raw=gzip.GzipFile(fileobj=io.BytesIO(raw)).read(GZ_MAX+1);return raw.decode('latin-1'),' (gunzipped)' if len(raw)<=GZ_MAX else ' (gunzipped, truncated)'
        except (OSError,EOFError,gzip.BadGzipFile):return raw.decode('latin-1'),' (not valid gzip, scanned raw)'
    return raw.decode('latin-1'),''


def scan_leaks(build,allow_ips=(),allow_endpoint_files=(),allow_hosts=(),deny_words=()):
    build=Path(build);hits={};hosts={};gz=0;allow_ips=set(allow_ips);allow_endpoint=set(allow_endpoint_files)
    deny=[(f'device identifier ({w[0]})' if isinstance(w,tuple) else 'denied word (--deny-word)',re.compile(re.escape(w[1] if isinstance(w,tuple) else w),re.I)) for w in deny_words]
    def add(label,name,n):hits.setdefault(label,{});hits[label][name]=hits[label].get(name,0)+n
    for p in files_of(build):
        name=rel(build,p);text,note=file_text(p);gz+=bool(note);where=name+note
        if p.suffix=='.map':add('sourcemap file',where,1)
        for m in IPV4.finditer(text):
            ip=m.group(1)
            if ip in allow_ips:continue
            try:addr=ipaddress.ip_address(ip)
            except ValueError:continue
            for net,label in PRIVATE_NETS:
                if addr in net:add(f'private/loopback IPv4 in {label}',where,1);break
        for label,rx in LEAKS+deny:
            n=len(rx.findall(text))
            if n:add(label,where,n)
        if name not in allow_endpoint:
            for label,rx in ENDPOINTS:
                n=len(rx.findall(text))
                if n:add(label,where,n)
        for h in URL_HOST.findall(text):
            h=h.lower().rstrip('.')
            if h in SAFE_HOSTS or h in allow_hosts or IPV4.fullmatch(h) or re.search(r'(?:^|\.)(?:localhost|local|lan|internal|home\.arpa)$',h):continue
            hosts.setdefault(h,set()).add(name)
    out=[]
    for label,where in sorted(hits.items()):
        items=sorted(where.items());shown=', '.join(f'{w} x{n}' for w,n in items[:8])+(f', +{len(items)-8} more files' if len(items)>8 else '')
        out.append(result('FAIL','leak-scan',f'{label}: {shown}'))
    if hosts:out.append(result('WARN','leak-scan',f'external hosts referenced (allow with --allow-host if intended): '+', '.join(f'{h} ({len(f)} files)' for h,f in sorted(hosts.items())[:20])))
    if not hits:out.append(result('PASS','leak-scan',f'{len(files_of(build))} files scanned ({gz} gunzipped), no leaks; allowed IP literals: {sorted(allow_ips) or "none"}, endpoint-allowed files: {sorted(allow_endpoint) or "none"}'))
    return out


def check_size(build):
    files=[(p.stat().st_size,rel(build,p)) for p in files_of(build)];total=sum(s for s,_ in files);big=sorted(files,reverse=True)[:5]
    stats={'total_bytes':total,'file_count':len(files),'largest':[{'file':n,'bytes':s} for s,n in big]};out=[]
    if total>MAX_UPLOAD_BYTES:out.append(result('FAIL','size',f'{total/1e6:.1f} MB exceeds the Vercel Hobby upload limit {MAX_UPLOAD_BYTES/1e6:.0f} MB'))
    if len(files)>MAX_FILES:out.append(result('FAIL','size',f'{len(files)} files exceed the Vercel limit of {MAX_FILES}'))
    for s,n in files:
        if s>MAX_SINGLE_FILE:out.append(result('WARN','size',f'{n} is {s/2**20:.1f} MiB, over the 25 MiB Cloudflare Pages/R2 single-file limit'))
    if not any(r['level']=='FAIL' for r in out):
        out.append(result('PASS','size',f'{total/1e6:.2f} MB in {len(files)} files (limits {MAX_UPLOAD_BYTES/1e6:.0f} MB, {MAX_FILES}); largest: '+', '.join(f'{n} {s/1e3:.0f} kB' for s,n in big[:3])))
    return out,stats


def source_regex(src):
    """Vercel ``source`` (path-to-regexp) to a Python regex: (...) groups are raw regex, :name/:name*/:name+ are params."""
    out=[];i=0
    while i<len(src):
        c=src[i]
        if c=='(':
            depth=0;j=i
            while j<len(src):
                if src[j]=='\\':j+=2;continue
                depth+=(src[j]=='(')-(src[j]==')')
                if depth==0:break
                j+=1
            out.append(src[i:j+1]);i=j+1
        elif c==':' and re.match(r':\w',src[i:]):
            m=re.match(r':(\w+)([*+?]?)',src[i:]);out.append({'*':'.*','+':'.+','?':'[^/]*'}.get(m.group(2),'[^/]+'));i+=m.end()
        elif c=='\\' and i+1<len(src):out.append(re.escape(src[i+1]));i+=2
        else:out.append(re.escape(c));i+=1
    return re.compile('^'+''.join(out)+'$')


def headers_for(config,path):
    merged={}
    for rule in (config or {}).get('headers',[]):
        try:hit=source_regex(rule.get('source','')).match(path)
        except re.error:hit=None
        if hit:
            for h in rule.get('headers',[]):merged[h['key'].lower()]=h['value']
    return merged


def spa_rewrite(config,path='/__predeploy_missing'):
    for r in (config or {}).get('rewrites',[])+[{'source':x.get('src',''),'destination':x.get('dest','')} for x in (config or {}).get('routes',[]) if x.get('dest')]:
        try:
            if r.get('destination') in ('/index.html','/','/200.html') and source_regex(r.get('source','')).match(path):return r
        except re.error:continue
    return None


def csp_directives(value):return {d.split()[0].lower():d.split()[1:] for d in (value or '').split(';') if d.strip()}


def max_age(cc):m=re.search(r'max-age=(\d+)',cc or '');return int(m.group(1)) if m else None


def check_vercel(path,build=None):
    path=Path(path) if path else None;out=[]
    if not path or not path.is_file():return [result('FAIL','vercel-json',f'{path} missing: a static deploy needs explicit headers')],None
    try:config=json.loads(path.read_text(encoding='utf-8'))
    except ValueError as e:return [result('FAIL','vercel-json',f'{path} is not valid JSON: {e}')],None
    if config.get('functions'):out.append(result('FAIL','vercel-json','defines serverless functions ("functions"): the showcase must be static'))
    if config.get('crons'):out.append(result('FAIL','vercel-json','defines crons, which run functions'))
    for b in config.get('builds',[]):
        if b.get('use')!='@vercel/static':out.append(result('FAIL','vercel-json',f'builds entry uses {b.get("use")!r}, not @vercel/static'))
    if (path.parent/'api').exists():out.append(result('FAIL','vercel-json',f'{path.parent.name}/api/ exists: Vercel deploys files there as functions'))
    for r in config.get('rewrites',[]):
        if str(r.get('destination','')).startswith(('http://','https://')):out.append(result('WARN','vercel-json',f'rewrite {r.get("source")} proxies to another origin (counts as edge requests, exposes a backend)'))
    probes={'/':'html','/index.html':'html','/assets/index-Ab12Cd34.js':'hashed'}
    if build is None or (Path(build)/'showcase').is_dir():probes['/showcase/chunks/0-Ab12Cd34.bin.gz']='hashed'
    for p,kind in probes.items():
        h=headers_for(config,p);csp=csp_directives(h.get('content-security-policy'));miss=[]
        if not csp:miss.append('Content-Security-Policy')
        else:
            for d,v in (('default-src',"'self'"),('frame-ancestors',"'none'"),('object-src',"'none'")):
                if csp.get(d)!=[v]:miss.append(f"CSP {d} {v}")
            wild=[s for s in csp.get('connect-src',[]) if '*' in s or re.fullmatch(r'[a-z][a-z0-9+.-]*:',s) and s!='blob:']
            if wild and p=='/':out.append(result('WARN','vercel-json',f'wildcard connect-src {wild}: name the exact origins'))
        if h.get('x-content-type-options','').lower()!='nosniff':miss.append('X-Content-Type-Options nosniff')
        for k in ('referrer-policy','permissions-policy'):
            if not h.get(k):miss.append(k.title())
        if not (max_age(h.get('strict-transport-security')) or 0)>0:miss.append('Strict-Transport-Security max-age')
        cc=h.get('cache-control','')
        if kind=='hashed' and not ('immutable' in cc and (max_age(cc) or 0)>=IMMUTABLE_MIN_AGE):miss.append(f'Cache-Control immutable max-age>={IMMUTABLE_MIN_AGE}')
        if kind=='html' and (not cc or 'immutable' in cc or not ('no-cache' in cc or 'no-store' in cc or (max_age(cc) is not None and max_age(cc)<=HTML_MAX_AGE))):
            miss.append(f'short or no-cache Cache-Control (have {cc or "none"!r})')
        if miss:out.append(result('FAIL','vercel-json',f'{p}: missing '+'; '.join(miss)))
    if build is not None:
        stale=[rel(build,f) for f in files_of(build) if 'immutable' in headers_for(config,'/'+rel(build,f)).get('cache-control','')
            and not any(re.search(r'\d',t) or (t.lower()!=t and t.upper()!=t) for t in HASHED.findall(f.name))]
        if stale:out.append(result('WARN','vercel-json',f'{len(stale)} immutable-cached files have no content hash in their name (stale for a year after a redeploy): '+', '.join(stale[:6])))
    if not any(r['level']=='FAIL' for r in out):out.append(result('PASS','vercel-json',f'{path.name}: static only, CSP and security headers on {len(probes)} probe paths, cache rules correct'))
    return out,config


class SiteHandler(http.server.SimpleHTTPRequestHandler):
    """Static files like Vercel: cleanUrls, rewrites for missing files, 404 instead of directory listings, quiet logs."""
    def log_message(self,*a):pass
    def list_directory(self,path):self.send_error(404);return None
    def do_GET(self):
        config=self.server.vercel or {};path=urlsplit(self.path).path;fs=Path(self.translate_path(path))
        if not (fs.is_file() or (fs.is_dir() and (fs/'index.html').is_file())):
            if config.get('cleanUrls') and Path(self.translate_path(path.rstrip('/')+'.html')).is_file():self.path=path.rstrip('/')+'.html'
            elif (r:=spa_rewrite(config,path)):self.path=r['destination']
        return super().do_GET()


def serve(build,config=None):
    server=http.server.ThreadingHTTPServer(('127.0.0.1',0),partial(SiteHandler,directory=str(build)));server.daemon_threads=True;server.vercel=config
    threading.Thread(target=server.serve_forever,daemon=True,name='predeploy-http').start();return server


def fetch(port,path,timeout=10):
    t=time.perf_counter();c=http.client.HTTPConnection('127.0.0.1',port,timeout=timeout)
    try:c.request('GET',path);r=c.getresponse();body=r.read();return r.status,len(body),(time.perf_counter()-t)*1e3,body
    except (OSError,http.client.HTTPException):return 0,0,(time.perf_counter()-t)*1e3,b''
    finally:c.close()


class Refs(html.parser.HTMLParser):
    def __init__(self):super().__init__();self.refs=[];self.icon=False
    def handle_starttag(self,tag,attrs):
        a=dict(attrs);rels=(a.get('rel') or '').lower().split()
        if tag=='link' and a.get('href') and set(rels)&{'stylesheet','modulepreload','preload','icon','apple-touch-icon','manifest','shortcut'}:
            self.refs.append(a['href']);self.icon|=bool({'icon','shortcut'}&set(rels))
        for k in ('src','poster'):
            if tag in ('script','img','source','video','audio','iframe','embed') and a.get(k):self.refs.append(a[k])


def local(ref,base='/'):
    if not ref or ref.startswith(('data:','blob:','#','//','mailto:')) or re.match(r'[a-z][a-z0-9+.-]*:',ref,re.I):return None
    return posixpath.normpath(urlsplit(urljoin(base,ref)).path)


def exists(build,path):p=Path(build)/unquote(path).lstrip('/');return p.is_file()


def probe_view(build,port):
    """One page view: (urls in order, explicit refs, results). Explicit refs (HTML, CSS url(), showcase index) must exist."""
    build=Path(build);out=[];view=['/'];explicit={};index=build/'index.html'
    if not index.is_file():return view,explicit,[result('FAIL','load','index.html missing from the build')],False
    parser=Refs();parser.feed(index.read_text(encoding='utf-8',errors='replace'))
    for r in parser.refs:
        u=local(r)
        if u:explicit.setdefault(u,'index.html')
    if (build/'showcase/index.json').is_file():explicit.setdefault('/showcase/index.json','showcase/')
    queue=list(explicit);seen={'/'}
    while queue:
        u=queue.pop(0)
        if u in seen:continue
        seen.add(u);view.append(u);p=build/unquote(u).lstrip('/')
        if not p.is_file():continue
        if p.suffix in ('.js','.mjs','.css','.json') and p.stat().st_size<20_000_000:
            text=p.read_text(encoding='utf-8',errors='replace')
            if p.suffix=='.css':
                for r in CSS_REF.findall(text):
                    v=local(r.strip(),u)
                    if v:explicit.setdefault(v,u);queue.append(v)
            elif p.suffix=='.json' and u.startswith('/showcase/'):
                try:data=json.loads(text)
                except ValueError:data=None;out.append(result('FAIL','load',f'{u} is not valid JSON'))
                stack=[data]
                while stack:
                    x=stack.pop()
                    if isinstance(x,dict):stack+=list(x.values())
                    elif isinstance(x,list):stack+=x
                    elif isinstance(x,str) and JS_REF.fullmatch(f'"{x}"'):
                        v=local(x,u)
                        if v:explicit.setdefault(v,u);queue.append(v)
            else:
                for r in JS_REF.findall(text):
                    for v in (local(r,u),local(r,'/')):
                        if v and exists(build,v):queue.append(v);break
    if (build/'showcase').is_dir():view+=[x for x in ('/'+rel(build,f) for f in files_of(build/'showcase')) if x not in view]  # conservative: a view loads every chunk
    for u in view:
        status,_,_,_=fetch(port,u)
        if status>=500 or status==0:out.append(result('FAIL','load',f'{u} -> {status or "no response"}'))
        elif status==404 and u in explicit:out.append(result('FAIL','load',f'dangling reference: {u} (from {explicit[u]}) -> 404'))
        elif status>=400:out.append(result('WARN','load',f'{u} -> {status}'))
    return view,explicit,out,parser.icon


def pct(xs,q):xs=sorted(xs);return round(xs[min(len(xs)-1,int(round(q*(len(xs)-1))))],2) if xs else 0.


def simulate_load(build,config=None,seconds=5.,concurrency=16):
    server=serve(build,config);port=server.server_address[1]
    try:
        view,explicit,out,icon=probe_view(build,port);sizes={};lat=[];views=[];codes={};lock=threading.Lock();stop=time.monotonic()+float(seconds)
        def visitor():
            while time.monotonic()<stop:
                t=time.perf_counter();mine=[]
                for u in view:
                    status,n,ms,_=fetch(port,u);mine.append((u,status,n,ms))
                with lock:
                    for u,status,n,ms in mine:lat.append(ms);codes[status]=codes.get(status,0)+1;sizes[u]=n
                    views.append((time.perf_counter()-t)*1e3)
        threads=[threading.Thread(target=visitor,daemon=True) for _ in range(max(1,int(concurrency)))]
        for t in threads:t.start()
        for t in threads:t.join(float(seconds)+60)
    finally:server.shutdown();server.server_close()
    if not sizes:sizes={u:fetch_size(build,u) for u in view}
    errors=sum(n for c,n in codes.items() if c==0 or c>=400);e5=sum(n for c,n in codes.items() if c==0 or c>=500)
    stats={'requests_per_view':len(view),'implicit_requests_per_view':0 if icon else 1,'bytes_per_view':sum(sizes.get(u,0) for u in view),
        'views_completed':len(views),'requests_total':len(lat),'http_errors':errors,'http_5xx':e5,'status_counts':{str(k):v for k,v in sorted(codes.items())},
        'p50_ms':pct(lat,.5),'p95_ms':pct(lat,.95),'view_p50_ms':pct(views,.5),'view_p95_ms':pct(views,.95),
        'requests_per_second':round(len(lat)/max(float(seconds),1e-9),1),'concurrency':int(concurrency),'seconds':float(seconds),'view_urls':view}
    if e5:out.append(result('FAIL','load',f'{e5} server errors or dropped connections under load'))
    if not icon:out.append(result('WARN','load','no <link rel="icon">: browsers also request /favicon.ico (counted in the projection)'))
    if not any(r['level']=='FAIL' for r in out):
        out.append(result('PASS','load',f'{stats["views_completed"]} views by {concurrency} visitors in {seconds:g}s: {len(view)} requests and {stats["bytes_per_view"]/1e3:.1f} kB per view, '
            f'request p50 {stats["p50_ms"]} ms p95 {stats["p95_ms"]} ms, errors {errors}'))
    return out,stats


def fetch_size(build,u):p=Path(build)/(unquote(u).lstrip('/') or 'index.html');p=p/'index.html' if p.is_dir() else p;return p.stat().st_size if p.is_file() else 0


def project_monthly(requests_per_view,bytes_per_view,visitors,views_per_visit):
    pv=visitors*views_per_visit;req=pv*requests_per_view;tb=pv*bytes_per_view;out=[]
    stats={'page_views':pv,'edge_requests':req,'transfer_bytes':tb,'request_cap':EDGE_REQUESTS_PER_MONTH,'transfer_cap':TRANSFER_BYTES_PER_MONTH,
        'request_fraction':round(req/EDGE_REQUESTS_PER_MONTH,4),'transfer_fraction':round(tb/TRANSFER_BYTES_PER_MONTH,4),
        'break_even_visitors':int(min(EDGE_REQUESTS_PER_MONTH/max(1,views_per_visit*requests_per_view),TRANSFER_BYTES_PER_MONTH/max(1,views_per_visit*bytes_per_view)))}
    note='conservative: no browser/CDN cache hits, no compression'
    for name,val,cap,fmt in (('edge requests',req,EDGE_REQUESTS_PER_MONTH,lambda v:f'{v:,.0f}'),('transfer',tb,TRANSFER_BYTES_PER_MONTH,lambda v:f'{v/1e9:.2f} GB')):
        frac=val/cap;level='FAIL' if frac>1 else 'WARN' if frac>WARN_FRACTION else 'PASS'
        out.append(result(level,'projection',f'{name} {fmt(val)}/month = {frac:.1%} of {fmt(cap)} ({visitors:,} visitors x {views_per_visit} views; {note})'))
    out.append(result('PASS','projection',f'break-even: {stats["break_even_visitors"]:,} visitors/month at {views_per_visit} views each exhausts the Hobby cap'))
    return out,stats


def failure_tests(build,config=None):
    server=serve(build,config);port=server.server_address[1];out=[];spa=spa_rewrite(config)
    try:
        for path in (f'/__predeploy_missing_{secrets.token_hex(4)}',f'/assets/__predeploy_missing-{secrets.token_hex(4)}.js'):
            status=fetch(port,path)[0]
            if status==404:out.append(result('PASS','failure',f'nonexistent {path} -> 404'))
            elif status==200 and spa_rewrite(config,path):out.append(result('WARN','failure',f'nonexistent {path} -> 200 via SPA rewrite {spa_rewrite(config,path)["source"]}: broken links will not 404'))
            elif status==200:out.append(result('FAIL','failure',f'nonexistent {path} -> 200: a fallback hides broken links (expected 404)'))
            else:out.append(result('FAIL','failure',f'nonexistent {path} -> {status or "no response"} (expected 404)'))
        status=fetch(port,'/assets/..%2f..%2f..%2fREADME.md')[0];status2=fetch(port,'/../../README.md')[0]
        out.append(result('PASS' if 200 not in (status,status2) or spa else 'FAIL','failure',f'path traversal -> {status}/{status2}'))
    finally:server.shutdown();server.server_close()
    return out


def run(argv=None):
    ap=argparse.ArgumentParser(description=__doc__.split('\n')[0]);ap.add_argument('build');ap.add_argument('--vercel-json')
    ap.add_argument('--visitors-per-month',type=int,default=3000);ap.add_argument('--views-per-visit',type=float,default=3)
    ap.add_argument('--load-seconds',type=float,default=5.);ap.add_argument('--concurrency',type=int,default=16);ap.add_argument('--json-out')
    ap.add_argument('--allow-host',action='append',default=[]);ap.add_argument('--allow-ip',action='append',default=[])
    ap.add_argument('--allow-endpoint-file',action='append',default=[],help='build-relative file allowed to contain backend endpoint strings')
    ap.add_argument('--deny-word',action='append',default=[],help='extra identifier that must not appear (never printed)')
    a=ap.parse_args(argv);build=Path(a.build).resolve();vj=Path(a.vercel_json).resolve() if a.vercel_json else build.parent/'vercel.json'
    results=[];stats={}
    if not build.is_dir():results.append(result('FAIL','build',f'{a.build} is not a directory'))
    else:
        results+=scan_leaks(build,a.allow_ip,a.allow_endpoint_file,{h.lower() for h in a.allow_host},repo_deny_words()+list(a.deny_word))
        r,stats['size']=check_size(build);results+=r
        r,config=check_vercel(vj,build);results+=r
        r,stats['load']=simulate_load(build,config,a.load_seconds,a.concurrency);results+=r
        r,stats['projection']=project_monthly(stats['load']['requests_per_view']+stats['load']['implicit_requests_per_view'],stats['load']['bytes_per_view'],a.visitors_per_month,a.views_per_visit);results+=r
        results+=failure_tests(build,config)
    summary={k:sum(r['level']==k for r in results) for k in LEVELS}
    for r in results:print(f'{r["level"]:<5} {r["check"]:<12} {r["detail"]}')
    print(f'SUMMARY pass={summary["PASS"]} warn={summary["WARN"]} fail={summary["FAIL"]} -> {"GATE FAILED: do not deploy" if summary["FAIL"] else "gate passed"}')
    if a.json_out:
        report={'generated_utc':datetime.now(timezone.utc).isoformat(timespec='seconds'),'build':str(build),'vercel_json':str(vj),'summary':summary,'results':results,'stats':stats,
            'limits':{'max_upload_bytes':MAX_UPLOAD_BYTES,'max_files':MAX_FILES,'max_single_file':MAX_SINGLE_FILE,'edge_requests_per_month':EDGE_REQUESTS_PER_MONTH,
                'transfer_bytes_per_month':TRANSFER_BYTES_PER_MONTH,'as_of':'2026-11-05, re-check before each deploy'}}
        Path(a.json_out).write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
    return 1 if summary['FAIL'] else 0


if __name__=='__main__':sys.exit(run())
