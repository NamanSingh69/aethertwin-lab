"""Deterministic rigid-body lab and timestamped CPU ray-cast sensor model.

Ground truth is exposed ONLY by ``last_ground_truth``. Runtime sensor frames do
not contain labels, object identities or dynamic world geometry. LiDAR rays are
cast in each object's local scene at the individual acquisition timestamp, using
interpolated physics poses; camera observations use the sweep-end pose.
"""
from __future__ import annotations
from dataclasses import dataclass
import numpy as np
import mujoco
import open3d as o3d
from scipy.spatial.transform import Rotation, Slerp
from core.contracts import CameraCalibration, MultiModalSensorFrame, GroundTruthFrame, MaterialCorrespondences, IMUSample, SensorCapabilities
from core.se3_math import so3_exp, transform_points, inverse


@dataclass
class _Surface:
    mesh: object
    semantic: int
    instance: int
    color: np.ndarray
    body: str | None = None
    permeable: bool = False


def _box(center,size):
    m=o3d.geometry.TriangleMesh.create_box(*np.asarray(size,dtype=float))
    m.translate(np.asarray(center)-np.asarray(size)/2); return m


def _cylinder(center,radius,height,axis="z"):
    m=o3d.geometry.TriangleMesh.create_cylinder(radius,height,resolution=12)
    if axis=="x": m.rotate(Rotation.from_euler("y",np.pi/2).as_matrix(),center=(0,0,0))
    if axis=="y": m.rotate(Rotation.from_euler("x",np.pi/2).as_matrix(),center=(0,0,0))
    m.translate(center); return m


class SyntheticLabSimulator:
    def __init__(self,seed=0,config=None,mobile=False):
        cfg=(config or {}).get("simulation",config or {})
        self.seed=int(seed); self.rng=np.random.default_rng(seed); self.mobile=bool(mobile)
        self.include_s2=bool(cfg.get('include_s2',False))
        self.dt=float(cfg.get("dt",.1)); self.point_count=int(cfg.get("lidar_points",2500))
        self.width=int(cfg.get("camera_width",cfg.get("width",160))); self.height=int(cfg.get("camera_height",cfg.get("height",100)))
        self.noise=float(cfg.get("range_noise",cfg.get("noise_std",.008))); self.frame_id=0; self.time=0.
        self.phase=float(self.rng.uniform(-.5,.5)); self.last_ground_truth=None;self._previous_history=None;self.closed=False
        self._surfaces=[]; self._scenes=[]
        def add(mesh,c,instance,body=None,perm=False):
            self._surfaces.append(_Surface(mesh,c,instance,self.rng.uniform(.18,.88,3),body,perm))
        add(_box([0,0,-.06],[12,10,.12]),0,0)
        for c,s in [([0,5,1.75],[12,.12,3.5]),([0,-5,1.75],[12,.12,3.5]),
                    ([-6,0,1.75],[.12,10,3.5]),([6,0,1.75],[.12,10,3.5]),([0,0,3.5],[12,10,.12])]: add(_box(c,s),1,1)
        bench_shift=self.rng.uniform(-.35,.35,2)
        hazard_pos=np.array([1.5,-.4,.35])+np.r_[self.rng.uniform(-.3,.3,2),0]
        hazard_size=np.array([.65,.65,.7])*self.rng.uniform(.85,1.15)
        for x,y in [(-3+bench_shift[0],1+bench_shift[1]),(3+bench_shift[0],1+bench_shift[1])]:
            add(_box([x,y,.8],[2.2,1.2,.12]),1,2)
            for dx in [-.9,.9]:
                for dy in [-.45,.45]: add(_box([x+dx,y+dy,.4],[.12,.12,.8]),1,2)
        add(_box([-4.8,3,1.0],[.7,1.1,2.0]),1,3)
        add(_cylinder([0,2.5,1.9],.025,3.2,"x"),5,5)
        add(_box(hazard_pos,hazard_size),4,4)
        for i in range(6):
            m=o3d.geometry.TriangleMesh.create_sphere(.25,resolution=8)
            m.translate([-2.0+.18*np.cos(i*2),-.1+.18*np.sin(i*2),.65+.12*i])
            add(m,6,6,perm=True)
        for b,inst in [("human",10),("human2",11)]:
            add(_cylinder([0,0,.85],.22,1.35),2,inst,b)
            m=o3d.geometry.TriangleMesh.create_sphere(.19,resolution=10);m.translate([0,0,1.65]);add(m,2,inst,b)
        add(_box([0,0,.3],[.9,.65,.6]),3,12,"cart")
        # Commissioning excludes dynamic bodies and the newly placed hazard.
        for surf in self._surfaces:
            scene=o3d.t.geometry.RaycastingScene()
            scene.add_triangles(o3d.t.geometry.TriangleMesh.from_legacy(surf.mesh))
            self._scenes.append(scene)
        xml=f'''<mujoco><option timestep="0.005" gravity="0 0 -9.80665" integrator="RK4"/><worldbody>
        <geom type="plane" size="6 5 .1" friction="1 .1 .01"/>
        <body name="human" mocap="true" pos="-1 -1 0"><geom type="capsule" size=".22 .65" pos="0 0 .85"/></body>
        <body name="human2" mocap="true" pos="1 1 0"><geom type="capsule" size=".22 .65" pos="0 0 .85"/></body>
        <body name="cart" pos="2 -2 .03"><freejoint/><geom type="box" size=".45 .325 .3" pos="0 0 .3" mass="15" friction=".05 .01 .001"/></body>
        <geom type="box" pos="{hazard_pos[0]} {hazard_pos[1]} {hazard_pos[2]}" size="{hazard_size[0]/2} {hazard_size[1]/2} {hazard_size[2]/2}"/>
        <geom type="box" pos="{-3+bench_shift[0]} {1+bench_shift[1]} .8" size="1.1 .6 .06"/><geom type="box" pos="{3+bench_shift[0]} {1+bench_shift[1]} .8" size="1.1 .6 .06"/>
        </worldbody></mujoco>'''
        self.model=mujoco.MjModel.from_xml_string(xml); self.data=mujoco.MjData(self.model)
        mujoco.mj_forward(self.model,self.data)
        fx=self.width/(2*np.tan(np.deg2rad(90)/2)); fy=fx
        K=np.array([[fx,0,(self.width-1)/2],[0,fy,(self.height-1)/2],[0,0,1.]])
        # Optical x-right -> lidar -y; optical y-down -> lidar -z; optical z -> lidar x.
        T=np.eye(4);T[:3,:3]=np.array([[0,0,1],[-1,0,0],[0,-1,0]])
        self.calibration=CameraCalibration(K,T)

    def sensor_pose(self,t):
        T=np.eye(4);yaw=np.pi/2
        T[:3,3]=[0,-4,1.6]
        if self.mobile:
            T[:3,3]+=[.35*np.sin(.3*t),.15*np.sin(.5*t),0]
            yaw+=.15*np.sin(.4*t)
        T[:3,:3]=Rotation.from_euler("z",yaw).as_matrix();return T

    def _physics_interval(self,t0,t1):
        times=[];poses={name:[] for name in ("human","human2","cart")}
        while self.data.time<t1-1e-8:
            t=float(self.data.time)
            self.data.mocap_pos[0]=[-1.6+1.2*np.sin(.7*t+self.phase),-.5+.7*np.cos(.5*t),0]
            self.data.mocap_pos[1]=[1.7*np.cos(.4*t+.8+self.phase),1.8+1.3*np.sin(.4*t),0]
            target=np.array([2*np.cos(.25*t+self.phase),-2+np.sin(.4*t),.03])
            self.data.xfrc_applied[:]=0
            bid=self.model.body("cart").id
            velocity=self.data.qvel[:3]
            self.data.xfrc_applied[bid,:3]=40*(target-self.data.xpos[bid])-12*velocity
            times.append(t)
            for name in poses:
                b=self.data.body(name);poses[name].append(np.r_[b.xpos.copy(),b.xquat.copy()])
            mujoco.mj_step(self.model,self.data)
        times.append(float(self.data.time))
        for name in poses:
            b=self.data.body(name);poses[name].append(np.r_[b.xpos.copy(),b.xquat.copy()])
        return np.array(times),{k:np.array(v) for k,v in poses.items()}

    @staticmethod
    def _body_transforms(name,times,history,query):
        a=history[name];p=np.column_stack([np.interp(query,times,a[:,i]) for i in range(3)])
        q=a[:,[4,5,6,3]]
        rotations=Slerp(times,Rotation.from_quat(q))(np.clip(query,times[0],times[-1])).as_matrix()
        return rotations,p

    def _cast(self,origins,directions,qtimes,history,commissioning=False):
        n=len(directions);hit_dist=np.full(n,np.inf);hit_surface=np.full(n,-1,np.int32)
        through_dist=np.full(n,np.inf);through_surface=np.full(n,-1,np.int32)
        body_cache={}
        for j,(surf,scene) in enumerate(zip(self._surfaces,self._scenes)):
            if commissioning and (surf.body is not None or surf.semantic==4):continue
            if surf.body:
                if surf.body not in body_cache:body_cache[surf.body]=self._body_transforms(surf.body,*history,qtimes)
                R,p=body_cache[surf.body]
                o=np.einsum('nji,nj->ni',R,origins-p);d=np.einsum('nji,nj->ni',R,directions)
            else:o=origins;d=directions
            ray=np.ascontiguousarray(np.c_[o,d],dtype=np.float32)
            distance=scene.cast_rays(o3d.core.Tensor(ray))['t_hit'].numpy().astype(float)
            valid=(distance>.12)&(distance<30)
            if surf.permeable:
                mask=valid&(distance<through_dist);through_dist[mask]=distance[mask];through_surface[mask]=j
            else:
                mask=valid&(distance<hit_dist);hit_dist[mask]=distance[mask];hit_surface[mask]=j
        return hit_dist,hit_surface,through_dist,through_surface

    def _acquire(self,t0,t1,history,pose_override=None,commissioning=False):
        n=self.point_count
        # A non-repetitive hemispherical pattern, with deterministic time jitter.
        u=(np.arange(n)*.6180339887498949+self.frame_id*.17)%1
        az=2*np.pi*u;el=np.arcsin(-.65+1.45*((np.arange(n)*.41421356237)%1))
        dl=np.c_[np.cos(el)*np.cos(az),np.cos(el)*np.sin(az),np.sin(el)]
        times=np.linspace(t0,t1,n,endpoint=False)
        Ts=np.stack([pose_override if pose_override is not None else self.sensor_pose(t) for t in times])
        origins=Ts[:,:3,3];dirs=np.einsum('nij,nj->ni',Ts[:,:3,:3],dl)
        d,s,pd,ps=self._cast(origins,dirs,times,history,commissioning)
        valid=np.isfinite(d);ids=np.flatnonzero(valid);penetrate=(pd<d)&np.isfinite(pd)
        first=np.flatnonzero(penetrate)
        rays=np.r_[first,ids];distance=np.r_[pd[first],d[ids]];surfaces=np.r_[ps[first],s[ids]]
        counts=np.r_[np.full(len(first),2),1+penetrate[ids].astype(int)]
        returns=np.r_[np.ones(len(first)),counts[len(first):]]
        # Simulator truth follows the exact mesh material point. Observation
        # range noise must not perturb that independent physical correspondence.
        clean_distance=distance.copy()
        noise=self.rng.normal(0,self.noise,len(rays));distance=np.maximum(.13,distance+noise)
        local=dl[rays]*distance[:,None]
        intensity=np.array([np.mean(self._surfaces[k].color) for k in surfaces])
        intensity=np.clip(intensity/(1+.015*distance**2)+self.rng.normal(0,.015,len(rays)),0,1)
        points=np.c_[local,intensity,returns,counts,times[rays]-t0].astype(np.float32)
        labels=np.array([self._surfaces[k].semantic for k in surfaces],np.int64)
        instances=np.array([self._surfaces[k].instance for k in surfaces],np.int64)
        xyz_world=origins[rays]+dirs[rays]*clean_distance[:,None]
        previous_world=xyz_world.copy();flow=np.zeros_like(xyz_world);flow_valid=np.ones(len(rays),bool)
        capture_times=times[rays]
        correspondence_valid=np.full(len(rays),self._previous_history is not None,bool)
        for name in history[1]:
            mask=np.array([self._surfaces[k].body==name for k in surfaces])
            if not mask.any():continue
            query=times[rays[mask]]
            R,p=self._body_transforms(name,*history,query)
            if self._previous_history is None:
                flow_valid[mask]=False
            else:
                prevR,prevp=self._body_transforms(name,*self._previous_history,query-self.dt)
                material=np.einsum('nji,nj->ni',R,xyz_world[mask]-p)
                previous_world[mask]=np.einsum('nij,nj->ni',prevR,material)+prevp
                flow[mask]=xyz_world[mask]-previous_world[mask]
        end_pose=pose_override if pose_override is not None else self.sensor_pose(t1)
        v,uimg=np.indices((self.height,self.width));K=self.calibration.K
        camdir=np.c_[((uimg-K[0,2])/K[0,0]).ravel(),((v-K[1,2])/K[1,1]).ravel(),np.ones(uimg.size)]
        Tcam=end_pose@self.calibration.T_lidar_camera
        camera_dirs=camdir@Tcam[:3,:3].T;camera_o=np.broadcast_to(Tcam[:3,3],camera_dirs.shape)
        cd,cs,cp,cps=self._cast(camera_o,camera_dirs,np.full(len(camdir),t1),history,commissioning)
        cm=cp<cd;cd[cm]=cp[cm];cs[cm]=cps[cm]
        valid_camera=np.isfinite(cd)&(cd>=.2)&(cd<=6)
        depth=np.where(valid_camera,cd,0).reshape(self.height,self.width).astype(np.float32)
        rgb=np.zeros((len(camdir),3),np.uint8);camera_sem=np.full(len(camdir),-1,np.int64)
        camera_instance=np.full(len(camdir),-1,np.int64);camera_flow=np.zeros((len(camdir),3),np.float32)
        camera_flow_valid=valid_camera.copy()
        for j,surf in enumerate(self._surfaces):
            m=(cs==j)&valid_camera;rgb[m]=np.uint8(np.clip(surf.color*255,0,255));camera_sem[m]=surf.semantic;camera_instance[m]=surf.instance
        camera_world=camera_o+camera_dirs*np.where(valid_camera,cd,0)[:,None]
        camera_world[~valid_camera]=0
        camera_previous_world=camera_world.copy()
        for name in history[1]:
            moving_surfaces=[i for i,surf in enumerate(self._surfaces) if surf.body==name]
            mask=np.isin(cs,moving_surfaces)&valid_camera
            if not mask.any():continue
            if self._previous_history is None:
                camera_flow_valid[mask]=False
                continue
            R,p=self._body_transforms(name,*history,np.array([t1]))
            prevR,prevp=self._body_transforms(name,*self._previous_history,np.array([t1-self.dt]))
            material=(camera_world[mask]-p[0])@R[0]
            camera_previous_world[mask]=material@prevR[0].T+prevp[0]
            camera_flow[mask]=(camera_world[mask]-camera_previous_world[mask]).astype(np.float32)
        rgb=rgb.reshape(self.height,self.width,3)
        # High-rate IMU specific force in LiDAR/body coordinates.
        imu=[];eps=1e-3
        for t in np.arange(t0,t1+.002,.005):
            T=pose_override if pose_override is not None else self.sensor_pose(t)
            if self.mobile and pose_override is None:
                acc=np.array([-.35*.3**2*np.sin(.3*t),-.15*.5**2*np.sin(.5*t),0])
                omega=np.array([0,0,.15*.4*np.cos(.4*t)])
            else:acc=np.zeros(3);omega=np.zeros(3)
            imu.append(IMUSample(float(t),omega,T[:3,:3].T@(acc+np.array([0,0,9.80665]))))
        # The active rig is 3D LiDAR + RGB-D. Auxiliary S2 simulation is opt-in.
        ring=None
        if self.include_s2:
            angle=np.linspace(-np.pi,np.pi,180,endpoint=False)
            ring_dirs=np.c_[np.cos(angle),np.sin(angle),np.zeros(len(angle))]@end_pose[:3,:3].T
            rd,_,rp,_=self._cast(np.broadcast_to(end_pose[:3,3],ring_dirs.shape),ring_dirs,np.full(len(angle),t1),history,commissioning)
            rd=np.minimum(rd,rp);rv=np.isfinite(rd)
            ring=((ring_dirs[rv]*rd[rv,None])@end_pose[:3,:3]).astype(np.float32)
        frame=MultiModalSensorFrame(self.frame_id,t0,points,tuple(imu),rgb=rgb,depth=depth,
            calibration=self.calibration,camera_timestamp=t1,beam_ids=rays,return_metadata_valid=True,
            capabilities=SensorCapabilities("synthetic-lidar",True,True,True,True),
            sensor_pose_world=(pose_override if pose_override is not None else self.sensor_pose(0)),
            rplidar_s2_points=ring,rplidar_timestamp=t1 if self.include_s2 else None,sweep_duration=self.dt,
            raw_timestamps={"lidar":t0,"camera":t1,**({'rplidar':t1} if self.include_s2 else {})},diagnostics={"synthetic":True,"camera_resolution":[self.width,self.height],
                'active_sensor_profile':'lidar_rgbd_s2' if self.include_s2 else 'lidar_rgbd',
                'pose_prior_valid':pose_override is not None,'pose_prior_source':'commissioning station calibration' if pose_override is not None else 'initial pose only'})
        self.last_ground_truth=GroundTruthFrame(labels,flow.astype(np.float32),instances,
            flow_valid,end_pose,camera_sem.reshape(self.height,self.width),
            camera_flow.reshape(self.height,self.width,3),camera_instance.reshape(self.height,self.width),camera_flow_valid.reshape(self.height,self.width),
            correspondences=MaterialCorrespondences(xyz_world,previous_world,capture_times,capture_times-self.dt,correspondence_valid),
            camera_correspondences=MaterialCorrespondences(camera_world,camera_previous_world,
                np.full(len(camera_world),t1),np.full(len(camera_world),t1-self.dt),
                valid_camera&(self._previous_history is not None)))
        return frame

    def next_frame(self):
        if self.closed:raise RuntimeError("Simulator is closed")
        t0=self.time;t1=t0+self.dt;history=self._physics_interval(t0,t1)
        frame=self._acquire(t0,t1,history);self._previous_history=history;self.time=t1;self.frame_id+=1;return frame

    def commissioning_frames(self):
        if self.closed:raise RuntimeError("Simulator is closed")
        history=self._physics_interval(self.time,self.time+self.dt)
        frames=[]
        for x,y,yaw in [(0,-4,np.pi/2),(-4,0,0),(0,4,-np.pi/2),(4,0,np.pi)]:
            T=np.eye(4);T[:3,:3]=Rotation.from_euler('z',yaw).as_matrix();T[:3,3]=[x,y,1.6]
            frames.append(self._acquire(self.time,self.time+self.dt,history,T,True))
        self.time+=self.dt;self._previous_history=history
        return frames

    def close(self):
        self._scenes.clear();self._surfaces.clear();self.data=None;self.model=None;self._previous_history=None;self.closed=True

    def __iter__(self):return self
    def __next__(self):return self.next_frame()
