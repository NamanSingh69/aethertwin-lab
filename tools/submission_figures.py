"""Regenerate the submission figures and their index (INDEX.md) from real recordings and hardware reports.

Every figure is computed from the recordings and reports it is given, nothing else; each PNG carries its source
line and INDEX.md carries one caption per figure saying what it shows, which recording, and what it does NOT
show. The captions take the recording's own capture_report.json (motion, label, uncalibrated flag) at its word.

    python tools/submission_figures.py --recording C:/Users/user/aethertwin_rec/wifi_stationary_60s \
        --recording C:/Users/user/aethertwin_rec/wifi_stationary_600s --reports reports/hardware \
        --output reports/submission/figures

Figures: (1a) L2 cloud accumulated in the LiDAR frame, plan views after one sweep, 1 s, 10 s and the whole
window; (1b) the same cloud in perspective plus voxel coverage against accumulation time; (2) one RGB frame and
its depth map with the nearest L2 sweep drawn separately in its own frame (no LiDAR-to-camera projection:
that extrinsic is not measured); (3a) per-second frame rate, transport bandwidth and clock agreement from the
per-frame transport diagnostics; (3b) capture-report summary table; (4) IMU still-period histograms and biases;
(5) the start gap between consecutive L2 sweeps.

Reading: the manifest is validated by ingestion.recorder_replayer.ReplaySource (which also refuses active or
incomplete recordings); arrays are then read lazily member by member so a 600 s recording costs seconds, not a
full RGB-D decode. Chunk checksums are not re-verified here (ReplaySource iteration does that).
Rendering uses OpenCV only (no plotting library in the locked environment), supersampled 2x then area-averaged.
"""
from __future__ import annotations
import argparse,datetime,json,math,sys
from pathlib import Path
import numpy as np
import cv2

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
G=9.80665;SS=2;FONT=cv2.FONT_HERSHEY_SIMPLEX
OLD_DESKEW_LIMIT_S=1e-6;DESKEW_TOLERANCE_S=.002  # slam_and_twin/imu_deskew.py before/after the overlap fix
def _bgr(h):h=h.lstrip('#');return (int(h[4:6],16),int(h[2:4],16),int(h[0:2],16))
SURFACE=_bgr('#fcfcfb');INK=_bgr('#0b0b0b');INK2=_bgr('#52514e');MUTED=_bgr('#898781');GRID=_bgr('#e8e7e3');AXIS=_bgr('#b5b3ab')
BAND=_bgr('#f3f2ef');NODATA=_bgr('#d9d7d1')
SERIES=[_bgr(h) for h in ('#2a78d6','#eb6834','#1baf7a','#eda100','#e87ba4')]
RAMP=np.array([_bgr(h) for h in ('#b7d3f6','#9ec5f4','#86b6ef','#6da7ec','#5598e7','#3987e5','#2a78d6','#256abf','#1c5cab','#184f95','#104281','#0d366b')],float)


def ramp(v):
    """Sequential one-hue blue ramp (light = low, dark = high); v in [0,1] -> BGR uint8."""
    v=np.clip(np.nan_to_num(np.asarray(v,float)),0,1)*(len(RAMP)-1);x=np.arange(len(RAMP))
    return np.stack([np.interp(v,x,RAMP[:,c]) for c in range(3)],-1).astype(np.uint8)


# ---------------------------------------------------------------- drawing kit (final-pixel coordinates)
def _sc(size):return size*SS/21.
def _th(bold):return max(1,int(round(SS*(1.7 if bold else 1.0))))
def text_width(s,size=13,bold=False):return cv2.getTextSize(s,FONT,_sc(size),_th(bold))[0][0]/SS


class Figure:
    def __init__(self,w,h):self.w=int(w);self.h=int(h);self.img=np.full((self.h*SS,self.w*SS,3),SURFACE,np.uint8)
    def text(self,x,y,s,size=13,color=INK,ha='l',va='b',bold=False):
        sc=_sc(size);th=_th(bold);(w,h),_=cv2.getTextSize(s,FONT,sc,th)
        X=int(round(x*SS-{'l':0,'c':w/2,'r':w}[ha]));Y=int(round(y*SS+{'b':0,'m':h/2,'t':h}[va]))
        cv2.putText(self.img,s,(X,Y),FONT,sc,color,th,cv2.LINE_AA)
    def vtext(self,x,y,s,size=13,color=INK2):
        """Text rotated 90 degrees counter-clockwise, centred on (x, y)."""
        sc=_sc(size);th=_th(False);(w,h),b=cv2.getTextSize(s,FONT,sc,th)
        tile=np.full((h+b+4,w+4,3),SURFACE,np.uint8);cv2.putText(tile,s,(2,h+2),FONT,sc,color,th,cv2.LINE_AA)
        tile=np.rot90(tile);X=int(x*SS-tile.shape[1]/2);Y=int(y*SS-tile.shape[0]/2);self.paste(tile,X,Y)
    def paste(self,tile,X,Y):
        H,W=self.img.shape[:2];x0=max(X,0);y0=max(Y,0);x1=min(X+tile.shape[1],W);y1=min(Y+tile.shape[0],H)
        if x1>x0 and y1>y0:self.img[y0:y1,x0:x1]=tile[y0-Y:y1-Y,x0-X:x1-X]
    def rect(self,x,y,w,h,color,fill=True,width=1):
        cv2.rectangle(self.img,(int(x*SS),int(y*SS)),(int((x+w)*SS)-1,int((y+h)*SS)-1),color,-1 if fill else max(1,int(width*SS)))
    def line(self,x0,y0,x1,y1,color,width=1):
        cv2.line(self.img,(int(x0*SS*16),int(y0*SS*16)),(int(x1*SS*16),int(y1*SS*16)),color,max(1,int(width*SS)),cv2.LINE_AA,4)
    def header(self,title,subtitle):
        self.text(28,40,title,20,INK,bold=True);self.text(28,66,subtitle,13,INK2)
    def footer(self,s):
        self.line(28,self.h-34,self.w-28,self.h-34,GRID);self.text(28,self.h-14,s,11,MUTED)
    def colorbar(self,x,y,w,h,lo,hi,label,fmt='{:.1f}'):
        v=np.linspace(1,0,int(h*SS));bar=np.repeat(ramp(v)[:,None,:],int(w*SS),1);self.paste(bar,int(x*SS),int(y*SS))
        for t in nice_ticks(lo,hi,5):
            yy=y+h*(1-(t-lo)/(hi-lo));self.line(x+w,yy,x+w+4,yy,AXIS);self.text(x+w+7,yy,fmt.format(t),11,INK2,va='m')
        self.text(x,y-10,label,11,INK2)
    def legend(self,x,y,items):
        for k,(label,color) in enumerate(items):
            yy=y+k*20;self.rect(x,yy-5,14,10,color);self.text(x+20,yy,label,12,INK2,va='m')
    def save(self,path):
        out=cv2.resize(self.img,(self.w,self.h),interpolation=cv2.INTER_AREA)
        path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
        if not cv2.imwrite(str(path),out,[cv2.IMWRITE_PNG_COMPRESSION,9]):raise RuntimeError(f'Could not write {path}')
        return path


def nice_ticks(lo,hi,n=5):
    if not np.isfinite([lo,hi]).all() or hi<=lo:return [lo]
    raw=(hi-lo)/max(n,1);mag=10**math.floor(math.log10(raw));step=min((m*mag for m in (1,2,2.5,5,10) if m*mag>=raw),default=10*mag)
    first=math.ceil(lo/step-1e-9)*step;return [round(first+k*step,12) for k in range(int((hi-first)/step+1e-9)+1)]


def tick_label(v,step):
    if v==0:return '0'
    d=max(0,-int(math.floor(math.log10(step)))) if step>0 else 2
    if abs(v)>=1e4 or (abs(v)<1e-3):return f'{v:.0e}'.replace('e-0','e-').replace('e+0','e')
    return f'{v:.{min(d,4)}f}'


class Axes:
    def __init__(self,fig,x,y,w,h,xlim,ylim,xlog=False,ylog=False):
        self.f=fig;self.x,self.y,self.w,self.h=x,y,w,h;self.xlog=xlog;self.ylog=ylog
        self.xl=[math.log10(v) for v in xlim] if xlog else list(map(float,xlim));self.yl=[math.log10(v) for v in ylim] if ylog else list(map(float,ylim))
        if self.xl[1]<=self.xl[0]:self.xl[1]=self.xl[0]+1
        if self.yl[1]<=self.yl[0]:self.yl[1]=self.yl[0]+1
        self.view=fig.img[int(y*SS):int((y+h)*SS),int(x*SS):int((x+w)*SS)]
    def _u(self,v,log,lim):
        v=np.asarray(v,float)
        if log:v=np.log10(np.where(v>0,v,np.nan))
        return (v-lim[0])/(lim[1]-lim[0])
    def px(self,v):return self.x+self._u(v,self.xlog,self.xl)*self.w
    def py(self,v):return self.y+self.h-self._u(v,self.ylog,self.yl)*self.h
    def _local(self,xs,ys):return np.c_[(self.px(xs)-self.x)*SS*16,(self.py(ys)-self.y)*SS*16]
    def decorate(self,xlabel='',ylabel='',title='',xfmt=None,yfmt=None,yticks=None,xticks=None):
        f=self.f
        for t,lab in self._ticks(self.yl,self.ylog,yticks,yfmt):
            yy=float(self.py(t));f.line(self.x,yy,self.x+self.w,yy,GRID);f.text(self.x-7,yy,lab,11,INK2,ha='r',va='m')
        for t,lab in self._ticks(self.xl,self.xlog,xticks,xfmt):
            xx=float(self.px(t));f.line(xx,self.y+self.h,xx,self.y+self.h+4,AXIS);f.text(xx,self.y+self.h+19,lab,11,INK2,ha='c')
        f.line(self.x,self.y+self.h,self.x+self.w,self.y+self.h,AXIS);f.line(self.x,self.y,self.x,self.y+self.h,AXIS)
        if xlabel:f.text(self.x+self.w/2,self.y+self.h+40,xlabel,12,INK2,ha='c')
        if ylabel:f.vtext(self.x-58,self.y+self.h/2,ylabel,12)
        if title:f.text(self.x,self.y-12,title,14,INK,bold=True)
    def _ticks(self,lim,log,given,fmt):
        if log:
            vals=given or [10.**k for k in range(math.ceil(lim[0]-1e-9),math.floor(lim[1]+1e-9)+1)]
            return [(v,fmt(v) if fmt else f'{v:g}') for v in vals]
        vals=given or nice_ticks(*lim);step=(vals[1]-vals[0]) if len(vals)>1 else 1
        return [(v,fmt(v) if fmt else tick_label(v,step)) for v in vals]
    def plot(self,xs,ys,color,width=1.5):
        xs=np.asarray(xs,float);ys=np.asarray(ys,float);ok=np.isfinite(xs)&np.isfinite(ys)&np.isfinite(self.px(xs))&np.isfinite(self.py(ys))
        for run in np.split(np.arange(len(xs)),np.flatnonzero(np.diff(ok.astype(int))!=0)+1):
            run=run[ok[run]]
            if len(run)>1:cv2.polylines(self.view,[np.round(self._local(xs[run],ys[run])).astype(np.int32)],False,color,max(1,int(width*SS)),cv2.LINE_AA,4)
    def scatter(self,xs,ys,color,r=1.6):
        pts=self._local(xs,ys);pts=pts[np.isfinite(pts).all(1)]
        for p in np.round(pts).astype(np.int64):cv2.circle(self.view,(int(p[0]),int(p[1])),int(r*SS*16),color,-1,cv2.LINE_AA,4)
    def bars(self,edges,counts,color,gap=.5):
        for a,b,c in zip(edges[:-1],edges[1:],counts):
            if not c>0:continue
            x0=float(self.px(a))+gap/2;x1=float(self.px(b))-gap/2;y0=float(self.py(c));base=self.y+self.h
            if not np.isfinite(y0):continue
            cv2.rectangle(self.f.img,(int(round(x0*SS)),int(round(max(y0,self.y)*SS))),(max(int(round(x0*SS)),int(round(x1*SS))-1),int(base*SS)-1),color,-1)
    def steps(self,edges,counts,color,width=1.5):
        xs=np.repeat(edges,2)[1:-1];ys=np.repeat(counts,2);self.plot(xs,ys,color,width)
    def vline(self,x,color,label=None,dash=True,above=0):
        xx=float(self.px(x))
        if not self.x<=xx<=self.x+self.w:return
        n=int(self.h//8) if dash else 1;seg=self.h/max(n,1)
        for k in range(n):
            if not dash or k%2==0:self.f.line(xx,self.y+k*seg,xx,self.y+(k+1)*seg if dash else self.y+self.h,color,1.2)
        if label:self.f.text(xx+5,self.y+14+above,label,11,color)
    def note(self,s,k=0,color=INK2,ha='r'):
        self.f.text(self.x+self.w-8 if ha=='r' else self.x+8,self.y+16+k*17,s,11,color,ha=ha)


def draw_table(fig,x,y,col_widths,rows,header=True,size=12,row_h=24):
    for r,row in enumerate(rows):
        yy=y+r*row_h
        if r==0 and header:fig.rect(x,yy,sum(col_widths),row_h,GRID)
        elif r%2==0:fig.rect(x,yy,sum(col_widths),row_h,BAND)
        xx=x
        for c,(cell,w) in enumerate(zip(row,col_widths)):
            s=str(cell)
            while len(s)>3 and text_width(s,size)>w-14:s=s[:-4]+'...'
            fig.text(xx+8 if c==0 else xx+w-8,yy+row_h/2,s,size,INK if (r==0 and header) or c==0 else INK2,ha='l' if c==0 else 'r',va='m',bold=r==0 and header)
            xx+=w
    return y+len(rows)*row_h


# ---------------------------------------------------------------- reading recordings
def open_recording(path):
    from ingestion.recorder_replayer import ReplaySource
    path=Path(path);source=ReplaySource(path);manifest=source.manifest;source.close()
    report={}
    if (path/'capture_report.json').is_file():report=json.loads((path/'capture_report.json').read_text(encoding='utf-8'))
    return {'path':path,'name':path.name,'manifest':manifest,'frames':int(manifest['frame_count']),'report':report}


def iter_frames(rec,keys,select=None):
    """Yield (index, metadata, {key: array}) reading only the named members; select: sorted frame indices or None."""
    base=0;select=None if select is None else np.asarray(sorted(select),np.int64)
    for chunk in rec['manifest']['chunks']:
        n=int(chunk['frames'])
        if select is not None:
            if base>select[-1]:return
            hit=select[(select>=base)&(select<base+n)]
            if not len(hit):base+=n;continue
        with np.load(rec['path']/chunk['file'],allow_pickle=False) as data:
            meta=json.loads(str(data['metadata_json']))
            if len(meta)!=n:raise ValueError(f"{rec['name']}: frame count mismatch inside {chunk['file']}")
            for j,m in enumerate(meta):
                if select is not None and base+j not in hit:continue
                yield base+j,m,{k:data[f'{j}_{k}'] for k in keys if f'{j}_{k}' in data.files}
        base+=n


def _get(d,*path):
    for p in path:
        if not isinstance(d,dict) or p not in d:return np.nan
        d=d[p]
    return np.nan if d is None else d


def scan(rec):
    """One lightweight pass over every frame: timing, transport counters, clock, IMU (deduplicated)."""
    cols={k:[] for k in ('t','dur','cam','bytes','offset','delay','bound','jitter','align','sync','segment')};imu=[]
    for _,m,a in iter_frames(rec,('imu',)):
        d=m.get('diagnostics') or {};rt=d.get('raw_transport') or {}
        for k,v in (('t',m['timestamp']),('dur',m.get('sweep_duration')),('cam',m.get('camera_timestamp')),('bytes',rt.get('bytes')),
                    ('offset',_get(rt,'clock','offset_seconds')),('delay',_get(rt,'clock','best_delay_seconds')),('bound',_get(rt,'clock','error_bound_seconds')),
                    ('jitter',_get(rt,'clock','jitter_seconds')),('align',_get(rt,'clock','host_clock_alignment_seconds')),('sync',d.get('sync_error_seconds')),
                    ('segment',d.get('acquisition_segment',rt.get('segment')))):
            cols[k].append(np.nan if v is None else float(v))
        if 'imu' in a and len(a['imu']):imu.append(a['imu'])
    out={k:np.asarray(v,float) for k,v in cols.items()}
    if imu:
        x=np.concatenate(imu);_,first=np.unique(np.round(x[:,0],6),return_index=True);out['imu']=x[first]
    else:out['imu']=np.empty((0,7))
    t=out['t'];dur=out['dur']
    out['gap']=(t[1:]-(t[:-1]+dur[:-1])) if len(t)>1 else np.empty(0)
    if np.isfinite(out['segment']).all() and len(t)>1:out['gap'][np.diff(out['segment'])!=0]=np.nan  # reconnects are not jitter
    return out


# ---------------------------------------------------------------- point-cloud helpers
def cloud_window(rec,seconds,t_all):
    """Frames whose sweep starts within `seconds` of the first; returns (indices, points list, start times)."""
    t0=t_all[0];idx=np.flatnonzero(t_all-t0<=seconds-1e-9)
    if not len(idx):idx=np.array([0])
    pts=[];ts=[]
    for i,_,a in iter_frames(rec,('lidar_points',),idx):
        p=a.get('lidar_points')
        if p is None:continue
        p=np.asarray(p,np.float32);p=p[np.isfinite(p[:,:3]).all(1)];pts.append(p);ts.append(t_all[i]-t0)
    return idx,pts,np.asarray(ts)


def ceiling_cut(z):
    """Height above which plan/perspective views hide the scan's upper horizontal layers (ceiling), or None.

    A layer is a 0.1 m height bin in the upper half of the extent holding >= 3 % of all returns and >= 3x the
    median upper-half bin; the cut sits one bin below the lowest such layer (rooms with a lowered section have two)."""
    lo,hi=np.percentile(z,[.5,99.5])
    if hi-lo<.5:return None,0.
    edges=np.arange(lo,hi+.1,.1);h,_=np.histogram(z,edges);upper=edges[:-1]>=lo+(hi-lo)/2
    if upper.sum()<3:return None,0.
    spike=upper&(h>=3*np.median(h[upper]))&(h>=.03*len(z))
    if not spike.any():return None,0.
    cut=float(edges[np.flatnonzero(spike)[0]]-.1);return cut,float(np.mean(z>=cut))


def splat(h,w,u,v,values,depth=None,size=2):
    """Paint points into an (h,w) canvas; nearest-first by depth when given, else highest value wins."""
    ok=(u>=0)&(u<w)&(v>=0)&(v<h);u=u[ok].astype(np.int64);v=v[ok].astype(np.int64);values=values[ok]
    key=-values if depth is None else depth[ok];order=np.argsort(key,kind='stable')
    canvas=np.full((h,w),np.nan);best=np.full((h,w),np.inf)
    for dy in range(size):
        for dx in range(size):
            uu=np.clip(u+dx,0,w-1);vv=np.clip(v+dy,0,h-1);flat=vv*w+uu
            _,first=np.unique(flat[order],return_index=True);sel=order[first]
            fl=flat[sel];kk=key[sel];better=kk<best.flat[fl];best.flat[fl[better]]=kk[better];canvas.flat[fl[better]]=values[sel][better]
    return canvas


def paint(canvas,lo,hi,fade=None):
    img=np.full(canvas.shape+(3,),SURFACE,np.uint8);ok=np.isfinite(canvas)
    col=ramp((canvas[ok]-lo)/max(hi-lo,1e-9)).astype(float)
    if fade is not None:a=np.clip(fade[ok],0,.55)[:,None];col=col*(1-a)+np.array(SURFACE,float)*a
    img[ok]=col.astype(np.uint8);return img


# ---------------------------------------------------------------- figures
def _src(rec):
    r=rec['report'];bits=[rec['name'],f"{rec['frames']} frames"]
    if r.get('motion'):bits.append(str(r['motion']))
    if r.get('bringup_uncalibrated'):bits.append('uncalibrated bring-up')
    return ', '.join(bits)


def fig_cloud(rec,scanned,seconds,out):
    t=scanned['t'];idx,pts,ts=cloud_window(rec,seconds,t)
    allp=np.concatenate(pts);span=float(ts[-1]+np.nanmedian(scanned['dur'])) if len(ts) else 0.
    cut,ceil_share=ceiling_cut(allp[:,2]);x,y,z=allp[:,0],allp[:,1],allp[:,2]
    xlo,xhi=np.percentile(x,[.5,99.5]);ylo,yhi=np.percentile(y,[.5,99.5]);pad=.3;xlo-=pad;xhi+=pad;ylo-=pad;yhi+=pad
    zlo,zhi=np.percentile(z,[1,99]);zlo=float(np.floor(zlo*10)/10);zhi=float(np.ceil(zhi*10)/10)
    stages=[('1 sweep',1)];sweep=float(np.nanmedian(scanned['dur'])) if np.isfinite(scanned['dur']).any() else .1
    for label,sec in (('1 s',1.),('10 s',10.)):
        n=int(np.sum(ts<sec-1e-9))
        if 1<n<len(pts):stages.append((label,n))
    stages.append((f'{span:.0f} s (all {len(pts)} sweeps)',len(pts)))
    W=1680;panel=(W-56-120-(len(stages)-1)*16)/len(stages);aspect=(yhi-ylo)/(xhi-xlo);ph=min(panel*aspect,560.)
    pw=ph/aspect if panel*aspect>560 else panel
    fig=Figure(W,int(ph+232));fig.header('Unitree L2: real point cloud accumulated while the rig stands still (plan view, LiDAR frame)',
        'The non-repetitive scan pattern fills the room over time (stationary bench accumulation, no motion compensation needed or applied). Colour = height; lowest return per pixel on top.')
    stats=[]
    for k,(label,n) in enumerate(stages):
        P=np.concatenate(pts[:n])
        X0=28+k*(pw+16);Y0=110;w=int(pw*SS);h=int(ph*SS)
        u=(P[:,0]-xlo)/(xhi-xlo)*w;v=(yhi-P[:,1])/(yhi-ylo)*h
        canvas=splat(h,w,u,v,P[:,2].astype(float),depth=P[:,2].astype(float),size=2);fig.paste(paint(canvas,zlo,zhi),int(X0*SS),int(Y0*SS))
        fig.rect(X0,Y0,pw,ph,AXIS,fill=False);fig.text(X0,Y0-10,label,14,INK,bold=True)
        ox=X0+(0-xlo)/(xhi-xlo)*pw;oy=Y0+(yhi-0)/(yhi-ylo)*ph
        cv2.drawMarker(fig.img,(int(ox*SS),int(oy*SS)),INK,cv2.MARKER_CROSS,int(12*SS),max(1,SS))
        cells=len(np.unique(np.floor(P[:,:2]/.05).astype(np.int64)@np.array([1,1<<21])))
        fig.text(X0,Y0+ph+20,f'{n} sweep{"s" if n>1 else ""}, {len(P):,} returns',11,INK2);fig.text(X0,Y0+ph+37,f'{cells:,} occupied 5 cm plan cells',11,INK2)
        stats.append((label,n,len(P),cells))
    # scale bar (1 m) in the last panel
    X0=28+(len(stages)-1)*(pw+16);m=pw/(xhi-xlo);fig.line(X0+pw-16-m,Y0+ph-16,X0+pw-16,Y0+ph-16,INK,2);fig.text(X0+pw-16-m/2,Y0+ph-24,'1 m',11,INK,ha='c')
    fig.colorbar(W-112,Y0+30,14,min(ph-50,260),zlo,zhi,'height z [m]')
    note='+ = L2 origin. All returns shown (dark rings = upper horizontal layers, likely ceiling); walls and furniture lie below them. Plan clipped to the 0.5-99.5 % extent (+0.3 m).'
    fig.text(28,Y0+ph+64,note,11,INK2)
    fig.footer(f"Source: {_src(rec)}; first {span:.0f} s. Raw returns as recorded (self-mask off, not deskewed). No ground truth: this shows coverage, not accuracy.")
    path=fig.save(out/'fig1a_l2_accumulation_plan.png')
    return path,{'stages':stats,'span':span,'sweeps':len(pts),'returns':int(len(allp)),'cut':cut,'ceil_share':ceil_share,'extent':(float(xhi-xlo),float(yhi-ylo)),'z':(zlo,zhi),'pts':pts,'ts':ts,'sweep':sweep}


def fig_perspective(rec,c,out,azimuth_deg=135.,elevation_deg=35.,name='fig1b_l2_perspective_coverage.png'):
    pts=c['pts'];ts=c['ts'];allp=np.concatenate(pts);cut=c['cut']
    P=allp[np.ones(len(allp),bool) if cut is None else allp[:,2]<cut][:,:3].astype(float)
    zlo,zhi=np.percentile(P[:,2],[1,99]);zlo=float(np.floor(zlo*10)/10);zhi=float(np.ceil(zhi*10)/10)
    lo=np.percentile(P,1,axis=0);hi=np.percentile(P,99,axis=0);P=P[((P>=lo-.3)&(P<=hi+.3)).all(1)]
    centre=(lo+hi)/2;ext=float(np.max(hi[:2]-lo[:2]));az=math.radians(azimuth_deg);el=math.radians(elevation_deg)
    eye=centre+1.25*ext*np.array([math.cos(el)*math.cos(az),math.cos(el)*math.sin(az),math.sin(el)])
    f=centre-eye;f/=np.linalg.norm(f);r=np.cross(f,[0,0,1.]);r/=np.linalg.norm(r);u=np.cross(r,f)
    Q=P-eye;Z=Q@f;X=(Q@r)/Z;Y=(Q@u)/Z;ok=Z>.1;X,Y,Z,Pz=X[ok],Y[ok],Z[ok],P[ok,2]
    W=1680;H=820;fig=Figure(W,H)
    fig.header('Unitree L2: the same stationary accumulation in perspective, and how coverage grows with time',
        'Left: all returns of the window'+(f' below z = {cut:.2f} m (upper layers, likely ceiling, {c["ceil_share"]:.0%} of returns, hidden)' if cut is not None else '')+'; colour = height, farther points fade (depth cue only). Right: occupied 5 cm voxels vs time.')
    pw,ph=980,640;X0,Y0=28,110;w=int(pw*SS);h=int(ph*SS)
    xl,xh=np.percentile(X,[.5,99.5]);yl,yh=np.percentile(Y,[.5,99.5]);s=min(w*.94/(xh-xl),h*.94/(yh-yl))
    uu=(X-(xl+xh)/2)*s+w/2;vv=h/2-(Y-(yl+yh)/2)*s
    canvas=splat(h,w,uu,vv,Pz,depth=Z,size=2);dc=splat(h,w,uu,vv,Z,depth=Z,size=2)
    fade=(dc-np.nanpercentile(dc,5))/max(np.nanpercentile(dc,95)-np.nanpercentile(dc,5),1e-9)*.5
    fig.paste(paint(canvas,zlo,zhi,fade),int(X0*SS),int(Y0*SS));fig.rect(X0,Y0,pw,ph,AXIS,fill=False)
    o=np.zeros(3)-eye;oz=o@f
    if oz>.1:
        ox=X0+((o@r)/oz-(xl+xh)/2)*s/SS+pw/2;oy=Y0+ph/2-((o@u)/oz-(yl+yh)/2)*s/SS
        cv2.drawMarker(fig.img,(int(ox*SS),int(oy*SS)),INK,cv2.MARKER_CROSS,int(14*SS),max(1,SS));fig.text(ox+10,oy-8,'L2',12,INK,bold=True)
    fig.colorbar(X0+pw+20,Y0+30,14,240,zlo,zhi,'height z [m]')
    # coverage curve
    keys=[];times=[]
    for p,t in zip(pts,ts):keys.append(np.unique(np.floor(p[:,:3]/.05).astype(np.int64)@np.array([1,1<<20,1<<40])));times.append(t+c['sweep'])
    checkpoints=np.unique(np.clip(np.round(np.geomspace(1,len(keys),40)).astype(int),1,len(keys)));seen=np.empty(0,np.int64);last=0;curve=[]
    for n in checkpoints:seen=np.union1d(seen,np.concatenate(keys[last:n]));last=n;curve.append((times[n-1],len(seen)))
    curve=np.array(curve,float);total=curve[-1,1]
    ax=Axes(fig,X0+pw+170,Y0+40,W-(X0+pw+170)-40,ph-120,(max(curve[0,0]*.8,1e-3),curve[-1,0]*1.25),(0,100),xlog=True)
    ax.decorate('accumulation time [s] (log scale)','occupied 5 cm voxels [% of final]','Coverage vs time',yfmt=lambda v:f'{v:.0f}')
    pct=curve[:,1]/total*100;ax.plot(curve[:,0],pct,SERIES[0],2);ax.scatter(curve[:,0],pct,SERIES[0],2.5)
    for tt in (1.,10.):
        if curve[0,0]<tt<curve[-1,0]:
            v=float(np.interp(math.log10(tt),np.log10(curve[:,0]),pct));ax.scatter([tt],[v],INK,3.5);fig.text(float(ax.px(tt))+8,float(ax.py(v))+16,f'{tt:.0f} s: {v:.0f} %',11,INK)
    fig.text(ax.x,ax.y+ax.h+62,f'Final: {int(total):,} voxels after {curve[-1,0]:.0f} s; first sweep {pct[0]:.1f} %.',11,INK2)
    fig.text(ax.x,ax.y+ax.h+80,'Voxel count includes the ceiling and range outliers.',11,INK2)
    fig.footer(f"Source: {_src(rec)}; first {c['span']:.0f} s, LiDAR frame, raw returns (self-mask off). Coverage of one stationary viewpoint, not a map; no accuracy claim.")
    return fig.save(out/name),{'curve':curve,'total':int(total)}


def fig_rgbd(rec,scanned,c,out,frame_index=None):
    n=len(c['pts']);target=n//2 if frame_index is None else int(frame_index)
    if not 0<=target<rec['frames']:raise SystemExit(f"--rgbd-frame {target} outside 0..{rec['frames']-1}")
    frame=None
    for i,m,a in iter_frames(rec,('rgb','depth','lidar_points'),[target]):frame=(i,m,a)
    if frame is None or 'rgb' not in frame[2] or 'depth' not in frame[2]:
        frame=None
        for i,m,a in iter_frames(rec,('rgb','depth','lidar_points')):
            if 'rgb' in a and 'depth' in a:frame=(i,m,a);break
    if frame is None:return None,{}
    i,m,a=frame;rgb=np.asarray(a['rgb'],np.uint8);depth=np.asarray(a['depth'],float);lp=a.get('lidar_points')
    fmt=str((m.get('diagnostics') or {}).get('camera_color_format','RGB')).upper();bgr=rgb[...,::-1].copy() if fmt!='BGR' else rgb
    H0,W0=rgb.shape[:2];W=1680;pw=(W-56-24-100)/3;ph=pw*H0/W0;Y0=118;fig=Figure(W,int(Y0+max(ph+60,min(pw,ph+40)+46)+56))
    fig.header('Gemini 336L colour and depth, and the L2 sweep of the same frame, each in its own coordinate frame',
        'No LiDAR-to-camera projection: the camera-LiDAR extrinsic calibration has not been measured yet, so the sensors are shown side by side, not fused.')
    def put(img,X0,title):
        img=cv2.resize(img,(int(pw*SS),int(ph*SS)),interpolation=cv2.INTER_AREA if img.shape[1]>pw*SS else cv2.INTER_NEAREST)
        fig.paste(img,int(X0*SS),int(Y0*SS));fig.rect(X0,Y0,pw,ph,AXIS,fill=False);fig.text(X0,Y0-10,title,14,INK,bold=True)
    X1=28+pw+24;X2=X1+pw+100
    put(bgr,28,f'RGB {W0}x{H0} (frame {i})')
    valid=np.isfinite(depth)&(depth>0);dlo,dhi=(np.percentile(depth[valid],[1,99]) if valid.any() else (0,1));dlo=float(np.floor(dlo*2)/2);dhi=float(max(np.ceil(dhi*2)/2,dlo+.5))
    dimg=np.full(depth.shape+(3,),NODATA,np.uint8);dimg[valid]=ramp((depth[valid]-dlo)/max(dhi-dlo,1e-9))
    put(dimg,X1,f'Depth (valid on {valid.mean():.0%} of pixels)')
    fig.colorbar(X1+pw+12,Y0+30,12,ph-50,dlo,dhi,'depth [m]');fig.rect(X1,Y0+ph+12,14,10,NODATA);fig.text(X1+20,Y0+ph+17,'grey = no depth',11,INK2,va='m')
    stats={'frame':int(i),'valid':float(valid.mean()),'depth_p':(float(np.percentile(depth[valid],50)) if valid.any() else float('nan'))}
    if lp is not None and len(lp):
        P=np.asarray(lp,float)
        allp=np.concatenate(c['pts']);xlo,xhi=np.percentile(allp[:,0],[.5,99.5]);ylo,yhi=np.percentile(allp[:,1],[.5,99.5])
        side=max(xhi-xlo,yhi-ylo)+.6;cx=(xlo+xhi)/2;cy=(ylo+yhi)/2;sq=min(pw,ph+40);w=int(sq*SS)
        u=(P[:,0]-(cx-side/2))/side*w;v=((cy+side/2)-P[:,1])/side*w;canvas=splat(w,w,u,v,P[:,2],depth=P[:,2],size=3)
        fig.paste(paint(canvas,*c['z']),int(X2*SS),int(Y0*SS));fig.rect(X2,Y0,sq,sq,AXIS,fill=False);fig.text(X2,Y0-10,'L2 sweep, plan view, LiDAR frame',14,INK,bold=True)
        ox=X2+(0-(cx-side/2))/side*sq;oy=Y0+((cy+side/2)-0)/side*sq;cv2.drawMarker(fig.img,(int(ox*SS),int(oy*SS)),INK,cv2.MARKER_CROSS,int(12*SS),max(1,SS))
        fig.text(X2,Y0+sq+20,f'{len(P):,} returns in one {float(m.get("sweep_duration") or 0)*1e3:.0f} ms sweep, + = L2 (colours as fig. 1a)',11,INK2)
        dt=(m.get('camera_timestamp') or np.nan)-m['timestamp'];stats['cam_minus_sweep_ms']=float(dt*1e3) if np.isfinite(dt) else float('nan')
        fig.text(X2,Y0+sq+38,f'camera time - sweep start: {dt*1e3:.0f} ms (host clock)' if np.isfinite(dt) else 'camera time not recorded',11,INK2)
    fig.text(28,Y0+ph+44,'Depth uses factory intrinsics as reported by the camera (bring-up, not calibrated); colour is the JPEG-compressed transport stream.',11,INK2)
    fig.footer(f"Source: {_src(rec)}; frame {i}. Not a registration or accuracy result: LiDAR and camera are deliberately not overlaid.")
    return fig.save(out/'fig2_rgb_depth_lidar_side_by_side.png'),stats


def per_second(s):
    t=s['t'];t0=t[0];b=np.floor(t-t0).astype(int);n=b.max()+1 if len(b) else 0
    rate=np.bincount(b,minlength=n).astype(float)
    if len(rate)>2:rate[-1]=np.nan  # last bin is partial
    by=s['bytes'];bw=np.full(n,np.nan)
    if np.isfinite(by).sum()>2:
        last=np.full(n,np.nan);lt=np.full(n,np.nan)
        for k in range(len(t)):
            if np.isfinite(by[k]):last[b[k]]=by[k];lt[b[k]]=t[k]
        d=np.diff(last);dt=np.diff(lt);ok=(d>=0)&(dt>.2);bw[1:][ok]=d[ok]/dt[ok]/1e6
    return np.arange(n)+.5,rate,bw


def binned(t,v,width,fn):
    ok=np.isfinite(t)&np.isfinite(v)
    if not ok.any():return np.empty(0),np.empty(0)
    b=np.floor(t[ok]/width).astype(int);out=[];c=[]
    for k in np.unique(b):out.append(fn(v[ok][b==k]));c.append((k+.5)*width)
    return np.array(c),np.array(out)


def fig_transport(recs,scans,out):
    W=1680;panels=[('frames per 1 s bin','Frame rate (one frame = one L2 sweep + nearest camera frame)','11.95 Hz gives 11 or 12 per bin'),
        ('MB/s','Transport bandwidth received on the laptop (raw_transport.bytes)','thin: 1 s; thick: 10 s mean'),
        ('ms','Clock mapping node->host: offset change since the first frame','per frame'),
        ('ms','Clock agreement bound (half the best recent ping round trip)','per frame'),
        ('ms','Camera-to-sweep pairing error |sync_error_seconds|','thin: 10 s max; thick: 10 s median')]
    ph=150;gap=88;fig=Figure(W,130+len(panels)*(ph+gap)+40)
    fig.header('Raw-sensor transport over Wi-Fi: rates, bandwidth and clock agreement through each run',
        'From the per-frame transport diagnostics stored in each recording (Pi 5 capture node -> laptop, authenticated TCP over Wi-Fi). Stationary rig.')
    tmax=max(float(s['t'][-1]-s['t'][0]) for s in scans)+1;stats=[];series=[]
    for s in scans:
        tc,rate,bw=per_second(s);t=s['t']-s['t'][0];fin=np.isfinite(s['offset'])
        off=(s['offset']-s['offset'][fin][0])*1e3 if fin.any() else s['offset'];sync=np.abs(s['sync'])*1e3
        bw10=binned(tc,bw,10.,np.mean);s10=(binned(t,sync,10.,np.median),binned(t,sync,10.,np.max))
        series.append({'rate':(tc,rate),'bw':(tc,bw),'bw10':bw10,'off':(t,off),'bound':(t,s['bound']*1e3),'sync':s10})
        def q(v,fn):v=np.asarray(v,float);v=v[np.isfinite(v)];return float(fn(v)) if len(v) else float('nan')
        stats.append({'rate_med':q(rate,np.median),'rate_min':q(rate,np.min),'rate_max':q(rate,np.max),'bw_med':q(bw,np.median),'bw_mean':q(bw,np.mean),
            'off_range':q(off,np.ptp),'bound_med':q(s['bound']*1e3,np.median),'bound_p95':q(s['bound']*1e3,lambda v:np.percentile(v,95)),'bound_max':q(s['bound']*1e3,np.max),
            'jitter_med':q(s['jitter']*1e3,np.median),'sync_med':q(sync,np.median),'sync_p95':q(sync,lambda v:np.percentile(v,95))})
    for k,(ylabel,title,how) in enumerate(panels):
        Y0=130+k*(ph+gap)+30;key=['rate','bw','off','bound','sync'][k]
        vals=[np.asarray(se[key][1] if key!='sync' else se[key][1][1],float) for se in series];allv=np.concatenate([v[np.isfinite(v)] for v in vals])
        if not len(allv):fig.text(110,Y0+ph/2,title+': not recorded in these recordings',13,INK2);continue
        if k==2:lo,hi=float(allv.min()),float(allv.max());pad=max((hi-lo)*.1,.5);lo-=pad;hi+=pad
        else:lo,hi=0.,max(float(allv.max())*1.12,1e-3)
        ax=Axes(fig,110,Y0,W-110-290,ph,(0,tmax),(lo,hi));ax.decorate('time since first sweep [s]' if k==len(panels)-1 else '',ylabel,title)
        for j,se in reversed(list(enumerate(series))):
            col=SERIES[j%len(SERIES)]
            if key=='bw':ax.plot(*se['bw'],col,.8);ax.plot(*se['bw10'],col,2.4)
            elif key=='sync':ax.plot(*se['sync'][1],col,.9);ax.plot(*se['sync'][0],col,2.4)
            else:ax.plot(*se[key],col,1.2 if key!='off' else 1.6)
        lx=W-262;fig.text(lx,Y0+ph-4,how,11,MUTED)
        if k==0:fig.legend(lx,Y0+8,[(rec['name'],SERIES[j%len(SERIES)]) for j,rec in enumerate(recs)])
        y0=Y0+(16+20*len(recs) if k==0 else 12)
        for j,st in enumerate(stats):
            msg=[f"median {st['rate_med']:.1f} Hz (bins {st['rate_min']:.0f}-{st['rate_max']:.0f})",f"mean {st['bw_mean']:.2f} MB/s",f"range {st['off_range']:.2f} ms",
                 f"median {st['bound_med']:.2f}, p95 {st['bound_p95']:.1f}, max {st['bound_max']:.1f}",f"median {st['sync_med']:.1f}, p95 {st['sync_p95']:.1f} ms"][k]
            fig.rect(lx,y0+j*18-4,8,8,SERIES[j%len(SERIES)]);fig.text(lx+14,y0+j*18,msg,11,INK2,va='m')
    fig.footer('Wi-Fi, stationary bench. The clock bound limits the node-to-host time mapping only; it is not a camera-LiDAR synchronisation or calibration result.')
    return fig.save(out/'fig3a_transport_timeseries.png'),stats


def fig_summary(reports,out):
    if not reports:return None,[]
    def ms(v):return '' if v is None else f'{v*1e3:.2f}'
    rows=[['metric']+[p.stem.replace('capture_report_','') for p,_ in reports]]
    spec=[('label / motion',lambda r:f"{r.get('motion','?')}"),('duration [s]',lambda r:f"{r.get('elapsed_s',0):.0f}"),('frames written',lambda r:f"{r.get('frames','')}"),
          ('frame rate [Hz]',lambda r:f"{r.get('frames_hz',0):.2f}"),('recorder dropped frames',lambda r:f"{_get(r,'recorder','dropped_frames')}"),
          ('IMU / cloud / camera rate, last 5 s [Hz]',lambda r:'/'.join(f"{_get(r,'transport','rates_hz',k):.1f}" for k in ('imu','cloud','camera'))),
          ('cloud / camera missing',lambda r:f"{_get(r,'transport','cloud_missing')} / {_get(r,'transport','camera_missing')}"),
          ('IMU sequence breaks / missing',lambda r:f"{_get(r,'transport','imu_sequence_breaks')} / {_get(r,'transport','imu_missing_samples')}"),
          ('stalls / protocol errors / reconnects',lambda r:f"{_get(r,'transport','stalls')} / {_get(r,'transport','protocol_errors')} / {max(int(_get(r,'transport','segments') or 1)-1,0) if np.isfinite(float(_get(r,'transport','segments'))) else '?'}"),
          ('bandwidth, last 5 s [MB/s]',lambda r:f"{_get(r,'transport','bandwidth_bytes_per_second')/1e6:.2f}"),
          ('bytes / duration [MB/s] (derived)',lambda r:f"{_get(r,'transport','bytes')/r['elapsed_s']/1e6:.2f}"),
          ('bytes received [GB]',lambda r:f"{_get(r,'transport','bytes')/1e9:.2f}"),
          ('clock error bound, at end [ms]',lambda r:ms(_get(r,'transport','clock','error_bound_seconds'))),
          ('clock ping jitter, at end [ms]',lambda r:ms(_get(r,'transport','clock','jitter_seconds'))),
          ('clock exchanges',lambda r:f"{_get(r,'transport','clock','exchanges')}"),
          ('arrival latency p95 IMU/cloud/cam [ms]',lambda r:'/'.join(f"{_get(r,'transport','arrival_latency_ms',k,'p95'):.0f}" for k in ('imu','cloud','camera'))),
          ('capture node',lambda r:f"{_get(r,'transport','node_identity','host')}")]
    for name,fn in spec:
        row=[name]
        for _,r in reports:
            try:row.append(fn(r))
            except (TypeError,ValueError,KeyError):row.append('n/a')
        rows.append([c.replace('nan','n/a') for c in row])
    title='Capture-report summary (laptop receiver, Pi 5 capture node over Wi-Fi)';sub='Values copied from the capture_report JSON named in each column header (one derived row); nothing here is re-measured.'
    cw=[400]+[260]*len(reports);W=int(max(56+sum(cw),text_width(title,20,True)+60,text_width(sub,13)+60));fig=Figure(W,100+len(rows)*26+70)
    fig.header(title,sub)
    draw_table(fig,28,100,cw,rows,row_h=26)
    fig.footer('Source: the reports named above (in '+reports[0][0].parent.as_posix()+'). Stationary bench runs; not a moving or wired-transport result.')
    return fig.save(out/'fig3b_transport_summary_table.png'),rows


def fig_imu(recs,scans,bias,out):
    W=1680;ph=210;fig=Figure(W,130+2*(ph+92)+40+(len(recs)+2)*24+40)
    fig.header('IMU (L2 built-in) while the rig stands still: noise distribution and biases',
        'Normalised histograms per axis (duplicates across frames removed by timestamp). Biases are suggestions for live.imu_accel_bias, not a calibration.')
    names=['gyro x','gyro y','gyro z','accel x','accel y','accel z'];pw=(W-110-2*90-40)/3;stats=[]
    data=[s['imu'] for s in scans]
    for c in range(6):
        r,k=divmod(c,3);X0=110+k*(pw+90);Y0=140+r*(ph+92);vals=[d[:,1+c] for d in data if len(d)]
        if not vals:continue
        allv=np.concatenate(vals);lo,hi=np.percentile(allv,[.2,99.8])
        if c==5:lo=min(lo,G-.05);hi=max(hi,G+.05) if abs(np.median(allv)-G)<1 else hi
        if hi<=lo:hi=lo+1e-6
        edges=np.linspace(lo,hi,61);dens=[np.histogram(v,edges,density=True)[0] for v in vals];top=max(float(d.max()) for d in dens)*1.15
        unit='rad/s' if c<3 else 'm/s^2';ax=Axes(fig,X0,Y0,pw,ph,(lo,hi),(0,top))
        ax.decorate(f'{names[c]} [{unit}]','density' if k==0 else '',names[c],yfmt=lambda v:'' if v else '0')
        for j,d in enumerate(dens):ax.steps(edges,d,SERIES[j%len(SERIES)],1.8)
        if c==5 and lo<=G<=hi:ax.vline(G,INK2,'g = 9.807')
        for j,v in enumerate(vals):
            msg=f'mean {np.mean(v):+.4g}, std {np.std(v):.2g}';ax.note(msg,j,INK2);fig.rect(X0+pw-8-text_width(msg,11)-12,Y0+16+j*17-8,6,6,SERIES[j%len(SERIES)])
    fig.legend(W-250,96,[(rec['name'],SERIES[j%len(SERIES)]) for j,rec in enumerate(recs)])
    rows=[['source','samples','rate [Hz]','gyro mean x,y,z [mrad/s]','accel mean x,y,z [m/s^2]','|accel| [m/s^2]','accel bias (gravity method) [m/s^2]']]
    for rec,d in zip(recs,data):
        if not len(d):rows.append([rec['name'],'0','','','','','']);continue
        g=d[:,1:4].mean(0);a=d[:,4:7].mean(0);n=np.linalg.norm(a);b=a-G*a/n;span=d[-1,0]-d[0,0]
        rows.append([rec['name'],f'{len(d):,}',f'{(len(d)-1)/span:.1f}' if span>0 else '',', '.join(f'{v*1e3:+.3f}' for v in g),', '.join(f'{v:+.3f}' for v in a),f'{n:.3f}',', '.join(f'{v:+.3f}' for v in b)])
        stats.append({'name':rec['name'],'samples':int(len(d)),'gyro_mean':g.tolist(),'accel_mean':a.tolist(),'norm':float(n),'bias':b.tolist(),'gyro_std':d[:,1:4].std(0).tolist(),'accel_std':d[:,4:7].std(0).tolist()})
    if bias:
        p,r=bias;rows.append([p.name+' (report)',f"{sum(x.get('samples',0) for x in r.get('recordings',[])):,}",'',', '.join(f'{v*1e3:+.3f}' for v in r.get('gyro_bias_rad_s',[])),
            ', '.join(f"{v:+.3f}" for v in (r.get('recordings') or [{}])[0].get('accel_mean_m_s2',[])),f"{(r.get('recordings') or [{}])[0].get('accel_norm_m_s2',float('nan')):.3f}",', '.join(f'{v:+.3f}' for v in r.get('accel_bias_m_s2',[]))])
    Y=140+2*(ph+92)-10;draw_table(fig,28,Y,[300,110,100,280,280,150,W-56-1220],rows,size=11,row_h=24)
    fig.text(28,Y+len(rows)*24+20,'One upright orientation: the z bias cannot be separated from a z scale error; x/y accel biases are gravity-direction estimates only.',11,INK2)
    fig.footer('Stationary bench recordings over Wi-Fi; the spread may include bench vibration. Factory IMU-to-LiDAR extrinsic (Unitree SDK2 spec), not measured.')
    return fig.save(out/'fig4_imu_still_stats.png'),stats


def fig_gaps(recs,scans,out):
    W=1680;H=800;fig=Figure(W,H);g0=[s['gap'][np.isfinite(s['gap'])] for s in scans];worst=-min([float(g.min()) for g in g0 if len(g)]+[0.])*1e3
    fig.header(f'Engineering finding: real L2 sweeps do not abut exactly; consecutive sweeps overlap by up to {worst:.2f} ms' if worst>0 else 'Sweep start gaps of the real L2 stream (no overlaps in these recordings)',
        'Gap = next sweep start - (this sweep start + sweep duration), host clock. Negative gaps made the IMU deskewer reject real data until the 2 ms overlap tolerance.')
    gaps=[s['gap'][np.isfinite(s['gap'])]*1e3 for s in scans];allg=np.concatenate([g for g in gaps if len(g)]) if any(len(g) for g in gaps) else np.zeros(1)
    lo=min(float(allg.min()),-DESKEW_TOLERANCE_S*1e3)-.5;hi=float(allg.max())+.5;edges=np.arange(math.floor(lo*10)/10,hi+.1,.1)
    counts=[np.histogram(g,edges)[0] for g in gaps];top=max(float(c.max()) for c in counts) if counts else 1
    ax=Axes(fig,110,130,860,440,(edges[0],edges[-1]),(.8,top*2.5),ylog=True)
    ax.decorate('sweep start gap [ms]','sweeps per 0.1 ms bin (log)','Distribution',yfmt=lambda v:f'{v:g}')
    for j,c in enumerate(counts):ax.steps(edges,np.where(c>0,c,.8),SERIES[j%len(SERIES)],1.8)
    ax.vline(0,INK,'0: old deskewer rejected any gap below this',above=0);ax.vline(-DESKEW_TOLERANCE_S*1e3,INK2,'-2 ms: tolerance after the fix',above=18)
    ax2=Axes(fig,1100,130,W-1100-40,440,(0,max(float(s['t'][-1]-s['t'][0]) for s in scans)+1),(edges[0],edges[-1]))
    ax2.decorate('time since first sweep [s]','gap [ms]','Gap over time',yfmt=lambda v:f'{v:g}')
    stats=[]
    for j,(s,g) in enumerate(zip(scans,gaps)):
        tt=(s['t'][1:]-s['t'][0])[np.isfinite(s['gap'])];ax2.scatter(tt,g,SERIES[j%len(SERIES)],1.1)
        if len(g):stats.append({'name':recs[j]['name'],'n':int(len(g)),'neg':int(np.sum(g< -OLD_DESKEW_LIMIT_S*1e3)),'min':float(g.min()),'max':float(g.max()),'p50':float(np.median(g)),
            'p1':float(np.percentile(g,1)),'p99':float(np.percentile(g,99)),'beyond_tol':int(np.sum(g< -DESKEW_TOLERANCE_S*1e3)),'dur':float(np.nanmedian(s['dur'])*1e3)})
    fig.line(ax2.x,float(ax2.py(0)),ax2.x+ax2.w,float(ax2.py(0)),INK2,1)
    fig.legend(W-330,150,[(r['name'],SERIES[j%len(SERIES)]) for j,r in enumerate(recs)])
    rows=[['recording','gaps','overlaps (< 0)','min / median / max [ms]','p1 / p99 [ms]','beyond -2 ms','median sweep [ms]']]
    for st in stats:rows.append([st['name'],f"{st['n']:,}",f"{st['neg']:,} ({st['neg']/st['n']:.0%})",f"{st['min']:+.2f} / {st['p50']:+.2f} / {st['max']:+.2f}",f"{st['p1']:+.2f} / {st['p99']:+.2f}",str(st['beyond_tol']),f"{st['dur']:.1f}"])
    draw_table(fig,110,640,[260,110,170,270,190,140,180],rows,size=11,row_h=24)
    fig.footer('Host-clock timestamps of real L2 sweeps (node clock mapped to the laptop), stationary. The fix is unit-tested (tests/test_sensing_mapping.py), not yet on a moving rig.')
    return fig.save(out/'fig5_sweep_start_gap.png'),stats


# ---------------------------------------------------------------- index
def write_index(out,args,recs,results,elapsed,r1):
    def has(k):return k in results and results[k][0] is not None
    lines=[]
    def rec_line(r):
        rp=r['report'];return f"`{r['name']}` ({r['frames']} frames, {rp.get('elapsed_s',float('nan')):.0f} s, motion: {rp.get('motion','not recorded')}, label: {rp.get('label','not recorded')})"
    lines+=['# Submission figures','',f"Generated {datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%d %H:%M UTC')} by `tools/submission_figures.py` in {elapsed:.0f} s. Re-run:",'',
            '```',' '.join(['python tools/submission_figures.py']+[f'--recording {r["path"].as_posix()}' for r in recs]+[f'--reports {Path(args.reports).as_posix()}',f'--output {Path(args.output).as_posix()}']
                +([f'--cloud-recording {Path(args.cloud_recording).as_posix()}'] if args.cloud_recording else [])+([f'--rgbd-frame {args.rgbd_frame}'] if args.rgbd_frame is not None else [])
                +([f'--accumulate-seconds {args.accumulate_seconds:g}'] if args.accumulate_seconds!=60 else [])),'```','',
            'Recordings: '+'; '.join(rec_line(r) for r in recs)+'.','',
            '**Scope of all figures.** Real devices (Unitree L2, Orbbec Gemini 336L on USB 2 at 640x400, L2 built-in IMU) streamed from a '
            'Raspberry Pi 5 capture node to the laptop over Wi-Fi, rig standing still on a bench. Nothing here is calibrated (camera-LiDAR extrinsic, '
            'camera intrinsics beyond factory values, IMU), nothing is deskewed or masked, and nothing is compared with ground truth: these figures show '
            'that the sensors and the transport work and what the raw data look like, not accuracy, mapping quality or moving-robot behaviour.','']
    if has('cloud'):
        c=results['cloud'][1];st=c['stages'];lines+=['## fig1a_l2_accumulation_plan.png','',
            f"**What:** plan view (LiDAR frame, colour = height) of the real L2 returns from `{r1['name']}` after "+', '.join(f"{n} sweep{'s' if n>1 else ''} ({lab.split(' (')[0]}; {p:,} returns, {cells:,} occupied 5 cm plan cells)" for lab,n,p,cells in st)+
            ". It shows how the L2's non-repetitive scan pattern densifies a stationary view over time; all returns are drawn, lowest per pixel on top.",'',
            f"**Does NOT show:** a map or SLAM result (the rig did not move; this is one viewpoint accumulated in the sensor frame), any accuracy, deskewing or self-masking (raw returns; the rig's own body may appear near the origin), or areas the L2 cannot see from this spot.",'']
    if has('persp'):
        p=results['persp'][1];c=results['cloud'][1];cv=p['curve'];v1=float(np.interp(0,np.log10(cv[:,0]),cv[:,1]/p['total']*100)) if cv[0,0]<=1<=cv[-1,0] else float('nan')
        lines+=['## fig1b_l2_perspective_coverage.png','',
            f"**What:** the same accumulation of `{r1['name']}` in perspective (colour = height; fading with distance is a depth cue only"+(f"; returns above z = {c['cut']:.2f} m, {c['ceil_share']:.0%} of them, likely ceiling, hidden so the room is visible" if c['cut'] is not None else '')+f"), and the share of the final {p['total']:,} occupied 5 cm voxels reached vs accumulation time"+(f" ({v1:.0f} % after 1 s)" if np.isfinite(v1) else '')+'.','',
            '**Does NOT show:** coverage of a whole room or building (one stationary viewpoint), map quality, or accuracy; the voxel count includes ceiling returns and range outliers.','']
    if has('rgbd'):
        s=results['rgbd'][1];lines+=['## fig2_rgb_depth_lidar_side_by_side.png','',
            f"**What:** one real Gemini 336L colour frame and its depth map (frame {s.get('frame')} of `{r1['name']}`, {s.get('valid',0):.0%} of pixels with depth, median {s.get('depth_p',float('nan')):.2f} m), next to the L2 sweep of the same frame drawn in the LiDAR's own frame.",'',
            '**Does NOT show:** LiDAR points projected into the image or any camera-LiDAR fusion: the extrinsic calibration has not been measured, so the panels are deliberately separate. Depth uses factory intrinsics (bring-up), not a calibration; no depth accuracy is claimed.','']
    if has('transport'):
        st=results['transport'][1];lines+=['## fig3a_transport_timeseries.png','',
            '**What:** per-second frame rate, laptop-side transport bandwidth, node-to-host clock-offset change, clock error bound (half the best recent ping round trip), and camera-to-sweep pairing error, from the per-frame diagnostics in '+
            '; '.join(f"`{r['name']}` (median {s['rate_med']:.1f} Hz, mean {s['bw_mean']:.2f} MB/s, offset drift range {s['off_range']:.2f} ms, bound median {s['bound_med']:.2f} ms / max {s['bound_max']:.2f} ms, pairing p95 {s['sync_p95']:.1f} ms)" for r,s in zip(recs,st))+'.','',
            '**Does NOT show:** wired-transport, moving-robot or multi-hour behaviour; hardware time sync (none: hardware_sync is false); the clock bound limits the node-to-host mapping only and is not a camera-LiDAR synchronisation or calibration result.','']
    if has('summary'):
        lines+=['## fig3b_transport_summary_table.png','',
            '**What:** a table of the headline numbers copied from '+', '.join(f'`{p.as_posix()}`' for p,_ in results['summary_src'])+' (duration, rates, drops, missing data, stalls, bandwidth, clock bound, arrival latency).','',
            '**Does NOT show:** anything re-measured: values are copied from the reports as written by the capture tool (one row, bytes / duration, is derived from two copied values). '
            'Rates, bandwidth and clock values in the reports are the receiver last-5-s or end-of-run snapshots, so they differ from the run averages in fig3a. The same stationary Wi-Fi limits apply.','']
    if has('imu'):
        st=results['imu'][1];lines+=['## fig4_imu_still_stats.png','',
            '**What:** per-axis histograms of the L2 built-in IMU while still, and mean/bias per recording: '+'; '.join(f"`{s['name']}` {s['samples']:,} samples, |accel| {s['norm']:.3f} m/s^2 (gravity 9.807), z accel bias {s['bias'][2]:+.3f} m/s^2, gyro std {max(s['gyro_std'])*1e3:.1f} mrad/s max axis" for s in st)+
            ('; plus the row from `'+results['imu_src'].as_posix()+'`' if results.get('imu_src') else '')+'.','',
            '**Does NOT show:** an IMU calibration (one orientation cannot separate a z bias from a z scale error), temperature behaviour, or noise while moving; the spread may include bench vibration.','']
    if has('gaps'):
        st=results['gaps'][1];lines+=['## fig5_sweep_start_gap.png','',
            '**What:** the start gap between consecutive real L2 sweeps (next start minus this start plus sweep duration, host clock): '+
            '; '.join(f"`{s['name']}` {s['n']:,} gaps, {s['neg']:,} negative ({s['neg']/s['n']:.0%}), min {s['min']:+.2f} ms, max {s['max']:+.2f} ms, {s['beyond_tol']} beyond the -2 ms tolerance" for s in st)+
            '. Negative gaps (overlaps) made the IMU deskewer raise "Overlapping sweeps" on real data; it now accepts overlaps up to 2 ms (`slam_and_twin/imu_deskew.py`, `OVERLAP_TOLERANCE_SECONDS`).','',
            '**Does NOT show:** that deskewing is accurate, or behaviour on a moving rig; the gaps are timestamp jitter of a stationary stream mapped onto the laptop clock.','']
    other=sorted(p.name for p in Path(args.reports).rglob('*.json') if not p.name.startswith(('capture_report','imu_bias'))) if Path(args.reports).is_dir() else []
    if other:lines+=['## Other hardware evidence (not plotted)','','Reports in `'+Path(args.reports).as_posix()+'` without a figure: '+', '.join(f'`{n}`' for n in other)+'.','']
    (out/'INDEX.md').write_text('\n'.join(lines),encoding='utf-8',newline='\n')
    return out/'INDEX.md'


def main(argv=None):
    import time
    p=argparse.ArgumentParser(description=__doc__.split('\n')[0])
    p.add_argument('--recording',action='append',required=True,type=Path,help='raw recording directory (repeat for several)')
    p.add_argument('--reports',type=Path,default=ROOT/'reports/hardware',help='hardware report folder (capture_report*.json, imu_bias*.json)')
    p.add_argument('--output',type=Path,default=ROOT/'reports/submission/figures')
    p.add_argument('--cloud-recording',type=Path,help='recording used for the point-cloud and RGB-D figures (default: the first)')
    p.add_argument('--accumulate-seconds',type=float,default=60.,help='point-cloud accumulation window from the first sweep')
    p.add_argument('--rgbd-frame',type=int,help='frame index of the RGB-D figure (default: middle of the accumulation window)')
    args=p.parse_args(argv);start=time.time();out=Path(args.output);out.mkdir(parents=True,exist_ok=True)
    recs=[open_recording(r) for r in args.recording]
    for r in recs:
        if r['frames']<2:raise SystemExit(f"{r['name']}: needs at least 2 frames")
    scans=[];results={}
    for r in recs:print(f"scanning {r['name']} ({r['frames']} frames)",flush=True);scans.append(scan(r))
    known=[r['path'].resolve() for r in recs];k=0
    if args.cloud_recording is not None:
        if Path(args.cloud_recording).resolve() in known:k=known.index(Path(args.cloud_recording).resolve())
        else:recs.append(open_recording(args.cloud_recording));scans.append(scan(recs[-1]));k=len(recs)-1
    results['cloud']=fig_cloud(recs[k],scans[k],args.accumulate_seconds,out);print('wrote',results['cloud'][0].name,flush=True)
    results['persp']=fig_perspective(recs[k],results['cloud'][1],out);print('wrote',results['persp'][0].name,flush=True)
    results['rgbd']=fig_rgbd(recs[k],scans[k],results['cloud'][1],out,args.rgbd_frame);print('wrote',results['rgbd'][0] and results['rgbd'][0].name,flush=True)
    results['transport']=fig_transport(recs,scans,out);print('wrote',results['transport'][0].name,flush=True)
    reps=[];folder=Path(args.reports)
    if folder.is_dir():
        for q in sorted(folder.rglob('capture_report*.json')):
            try:reps.append((q,json.loads(q.read_text(encoding='utf-8'))))
            except (OSError,ValueError):pass
    results['summary']=fig_summary(reps,out);results['summary_src']=reps
    bias=None
    if folder.is_dir():
        for q in sorted(folder.rglob('imu_bias*.json')):
            try:bias=(q,json.loads(q.read_text(encoding='utf-8')));break
            except (OSError,ValueError):pass
    results['imu_src']=bias[0] if bias else None
    results['imu']=fig_imu(recs,scans,bias,out);results['gaps']=fig_gaps(recs,scans,out)
    index=write_index(out,args,recs,results,time.time()-start,recs[k])
    pngs=sorted(out.glob('*.png'));print(f"wrote {len(pngs)} figures ({sum(q.stat().st_size for q in pngs)/1e6:.1f} MB) and {index} in {time.time()-start:.0f} s")
    return 0


if __name__=='__main__':raise SystemExit(main())
