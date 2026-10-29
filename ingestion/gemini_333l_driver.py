"""Gemini 335L capture with SDK-verified mixed-resolution D2C profiles."""
import time
from copy import deepcopy
import numpy as np
import cv2
from .sensor_synchronizer import ClockAligner


def decode_color(data,width,height,format_name):
    """Convert an enumerated SDK color format to the shared RGB uint8 contract."""
    fmt=str(format_name).upper().split('.')[-1];raw=np.asarray(data,dtype=np.uint8)
    if fmt=='RGB':return raw.reshape(height,width,3).copy()
    if fmt=='BGR':return cv2.cvtColor(raw.reshape(height,width,3),cv2.COLOR_BGR2RGB)
    if fmt in ('MJPG','MJPEG'):
        image=cv2.imdecode(raw.reshape(-1),cv2.IMREAD_COLOR)
        if image is None:raise ValueError('Invalid Gemini MJPG color frame')
        if image.shape[:2]!=(height,width):raise ValueError('Decoded color dimensions differ from selected profile')
        return cv2.cvtColor(image,cv2.COLOR_BGR2RGB)
    if fmt in ('YUYV','YUY2'):return cv2.cvtColor(raw.reshape(height,width,2),cv2.COLOR_YUV2RGB_YUY2)
    raise ValueError(f'Unsupported Gemini color format: {format_name}')


def _name(value):return str(getattr(value,'name',value)).split('.')[-1]


def _profile(profile):
    return {'width':int(profile.get_width()),'height':int(profile.get_height()),
            'fps':int(profile.get_fps()),'format':_name(profile.get_format())}


def _key(profile):
    p=_profile(profile);return p['width'],p['height'],p['fps'],p['format']


def _profiles(options):
    if options is None:return []
    return [options.get_stream_profile_by_index(i).as_video_stream_profile() for i in range(options.get_count())]


def _positive_integer(value,name):
    try:integer=int(value)
    except (TypeError,ValueError,OverflowError):raise ValueError(f'{name} must be a positive integer') from None
    if integer<=0 or integer!=value:raise ValueError(f'{name} must be a positive integer')
    return integer


def _clock_fields(frame):
    result={};available={}
    for key,method in [('device_us','get_timestamp_us'),('system_us','get_system_timestamp_us'),('global_us','get_global_timestamp_us')]:
        try:
            value=getattr(frame,method)();value=int(value)
            if value<0 or (key!='device_us' and value==0):raise ValueError('clock is not available')
            result[key]=value;available[key]=True
        except Exception as exc:
            result[key]=None;available[key]=False
            if key=='device_us':raise RuntimeError('Gemini device capture timestamp is unavailable') from exc
    result['available']=available;return result


class Gemini335LDriver:
    def __init__(self,calibration,width=1280,height=800,fps=30,serial=None,timeout_ms=2000,*,
                 color_width=None,color_height=None,color_fps=None,calibration_image_size=None,
                 align_mode='auto',intrinsics_tolerance_px=1.0,distortion_tolerance=1e-4):
        if calibration is None or not calibration.measured:raise ValueError('Live camera fusion requires a measured calibration file')
        if calibration_image_size is None or np.asarray(calibration_image_size).shape!=(2,):
            raise ValueError('Measured calibration requires image_size:[width,height]; pass calibration_image_size')
        self.image_size=tuple(_positive_integer(v,'calibration image size') for v in calibration_image_size)
        source_depth=(_positive_integer(width,'depth width'),_positive_integer(height,'depth height'),_positive_integer(fps,'depth fps'),'Y16')
        source_color=(_positive_integer(self.image_size[0] if color_width is None else color_width,'color width'),
                      _positive_integer(self.image_size[1] if color_height is None else color_height,'color height'),
                      _positive_integer(fps if color_fps is None else color_fps,'color fps'))
        if align_mode not in ('auto','hardware','software'):raise ValueError('align_mode must be auto, hardware or software')
        if not np.isfinite([intrinsics_tolerance_px,distortion_tolerance]).all() or min(intrinsics_tolerance_px,distortion_tolerance)<0:
            raise ValueError('Calibration validation tolerances must be finite and nonnegative')
        try:import pyorbbecsdk as ob
        except ImportError as exc:raise RuntimeError('Gemini live capture requires the optional pyorbbecsdk2 package') from exc
        self.ob=ob;self.calibration=calibration;self.timeout_ms=_positive_integer(timeout_ms,'timeout_ms');self.clock=ClockAligner()
        self.intrinsics_tolerance_px=float(intrinsics_tolerance_px);self.distortion_tolerance=float(distortion_tolerance)
        self.pipeline=None;self.context=None;self.align_filter=None;self._start_attempted=False;self._pending=None
        self.last_metadata={'camera_available':False};self.last_clock_metadata={};self.previous_color_us=None
        self.profiles={};self.profile_metadata={'requested_source_depth':dict(zip(('width','height','fps','format'),source_depth)),
            'requested_source_color':dict(zip(('width','height','fps'),source_color)),'measured_image_size':list(self.image_size)}
        try:
            self.context=ob.Context();devices=self.context.query_devices()
            if devices.get_count()==0:raise RuntimeError('No Orbbec camera is connected')
            device=devices.get_device_by_serial_number(serial) if serial else devices.get_device_by_index(0)
            self.pipeline=ob.Pipeline(device)
            colors=_profiles(self.pipeline.get_stream_profile_list(ob.OBSensorType.COLOR_SENSOR))
            depths=_profiles(self.pipeline.get_stream_profile_list(ob.OBSensorType.DEPTH_SENSOR))
            self.profiles={'color':[_profile(p) for p in colors],'depth':[_profile(p) for p in depths]}
            self.profile_metadata['available']=self.profiles
            formats=('RGB','BGR','MJPG','YUYV')
            color_candidates=[p for p in colors if _key(p)[:3]==source_color and _key(p)[3] in formats]
            color_candidates.sort(key=lambda p:formats.index(_key(p)[3]))
            if not color_candidates:raise ValueError(f'Requested color profile {source_color} unavailable. Supported: {self.profiles["color"]}')
            if not any(_key(p)==source_depth for p in depths):raise ValueError(f'Requested source depth profile {source_depth} unavailable. Supported: {self.profiles["depth"]}')
            if not callable(getattr(self.pipeline,'get_d2c_depth_profile_list',None)):
                raise RuntimeError('SDK D2C pairing API unavailable; cannot verify a supported color/depth pair')
            modes=('hardware','software') if align_mode=='auto' else (align_mode,)
            selected=None;pair_checks=[]
            for mode in modes:
                enum=getattr(ob.OBAlignMode,'HW_MODE' if mode=='hardware' else 'SW_MODE',None)
                if enum is None:
                    pair_checks.append({'mode':mode,'error':'SDK align mode unavailable'});continue
                for color in color_candidates:
                    try:compatible=_profiles(self.pipeline.get_d2c_depth_profile_list(color,enum))
                    except Exception as exc:
                        pair_checks.append({'mode':mode,'color':_profile(color),'error':str(exc)});continue
                    pair_checks.append({'mode':mode,'color':_profile(color),'depth':[_profile(p) for p in compatible]})
                    match=next((p for p in compatible if _key(p)==source_depth),None)
                    if match is not None:selected=(mode,enum,color,match);break
                if selected is not None:break
            self.profile_metadata['pair_checks']=pair_checks
            if selected is None:raise ValueError(f'Unsupported D2C color/depth pair for exact source depth {source_depth}; SDK checks: {pair_checks}')
            mode,enum,color,depth=selected
            self.profile_metadata.update(alignment=mode,selected_source_color=_profile(color),selected_source_depth=_profile(depth))
            config=ob.Config();config.enable_stream(color);config.enable_stream(depth)
            if mode=='hardware':
                config.set_align_mode(enum)
                # SDK geometric D2C scaling updates output profile metadata;
                # measured K is never resized or replaced by this adapter.
                config.set_depth_scale_require(True)
            else:
                config.set_align_mode(ob.OBAlignMode.DISABLE)
                self.align_filter=ob.AlignFilter(align_to_stream=ob.OBStreamType.COLOR_STREAM)
                self.align_filter.set_align_to_stream_profile(color)
                self.align_filter.set_match_target_resolution(True)
            self.pipeline.enable_frame_sync();self._start_attempted=True;self.pipeline.start(config)
            # Validate the actual first delivered aligned profiles before init
            # succeeds. Retain that observation for the first consumer read.
            self._pending=self._acquire()
        except BaseException as exc:
            self.last_metadata={**self.last_metadata,'camera_available':False,'acquisition_error':str(exc)}
            self._close_after_error(exc);raise

    def _validate_output(self,frame,name):
        width,height=int(frame.get_width()),int(frame.get_height())
        if (width,height)!=self.image_size:
            raise ValueError(f'Aligned {name} shape {(width,height)} differs from measured calibration image_size {self.image_size}; K will not be rescaled')
        profile=frame.get_stream_profile().as_video_stream_profile()
        intrinsic=profile.get_intrinsic();distortion=profile.get_distortion()
        if (int(intrinsic.width),int(intrinsic.height))!=(width,height) or (_key(profile)[0],_key(profile)[1])!=(width,height):
            raise ValueError(f'Aligned {name} profile/intrinsic dimensions differ from delivered image')
        K=np.array([[intrinsic.fx,0,intrinsic.cx],[0,intrinsic.fy,intrinsic.cy],[0,0,1.]],float)
        if not np.isfinite(K).all() or min(K[0,0],K[1,1])<=0:raise ValueError(f'Aligned {name} has invalid SDK intrinsics')
        error=float(np.max(np.abs(K-self.calibration.K)))
        if error>self.intrinsics_tolerance_px:raise ValueError(f'Aligned {name} intrinsics mismatch measured calibration ({error:.6g}px > {self.intrinsics_tolerance_px:g}px); recalibrate this profile')
        model=_name(distortion.model)
        if model not in ('NONE','BROWN_CONRADY','BROWN_CONRADY_K6'):
            raise ValueError(f'Aligned {name} distortion model {model} is incompatible with the OpenCV Brown calibration contract')
        higher=np.array([distortion.k4,distortion.k5,distortion.k6],float)
        if np.any(np.abs(higher)>self.distortion_tolerance):raise ValueError(f'Aligned {name} rational distortion needs unsupported higher coefficients; calibration has five coefficients')
        coefficients=np.array([distortion.k1,distortion.k2,distortion.p1,distortion.p2,distortion.k3],float)
        if model=='NONE' and np.max(np.abs(coefficients))>self.distortion_tolerance:raise ValueError(f'Aligned {name} reports inconsistent NONE distortion')
        distortion_error=float(np.max(np.abs(coefficients-self.calibration.distortion)))
        if not np.isfinite(coefficients).all() or not np.isfinite(higher).all() or distortion_error>self.distortion_tolerance:
            raise ValueError(f'Aligned {name} distortion mismatch measured calibration; recalibrate this output profile')
        return _profile(profile),{'K':K.tolist(),'distortion':coefficients.tolist(),'distortion_model':model,
            'max_intrinsics_error_px':error,'max_distortion_error':distortion_error}

    def _acquire(self):
        frames=self.pipeline.wait_for_frames(self.timeout_ms)
        if frames is None:raise TimeoutError('Gemini RGB-D acquisition timed out')
        color=frames.get_color_frame();depth=frames.get_depth_frame()
        if color is None or depth is None:raise RuntimeError('Incomplete Gemini RGB-D frameset')
        source_times={'color':_clock_fields(color),'depth':_clock_fields(depth)}
        if self.align_filter is not None:
            aligned=self.align_filter.process(frames)
            if aligned is None:raise RuntimeError('SDK software D2C produced no aligned frameset')
            frames=aligned.as_frame_set();color=frames.get_color_frame();depth=frames.get_depth_frame()
            if color is None or depth is None:raise RuntimeError('Incomplete SDK software-aligned Gemini frameset')
        color_profile,color_validation=self._validate_output(color,'color')
        depth_profile,depth_validation=self._validate_output(depth,'depth')
        if depth_profile['format']!='Y16':raise ValueError('Aligned Gemini depth must be Y16')
        format_name=_name(color.get_format())
        rgb=decode_color(color.get_data(),color.get_width(),color.get_height(),format_name)
        raw=np.frombuffer(depth.get_data(),np.uint16).reshape(depth.get_height(),depth.get_width())
        scale=float(depth.get_depth_scale())
        if not np.isfinite(scale) or scale<=0:raise ValueError('Invalid Gemini depth unit scale')
        depth_m=raw.astype(np.float32)*scale*.001
        output_times={'color':_clock_fields(color),'depth':_clock_fields(depth)}
        color_us=output_times['color']['device_us'];depth_us=output_times['depth']['device_us']
        stamp=float(color_us)*1e-6;timestamp=self.clock.update(stamp,time.monotonic())
        interval=None if self.previous_color_us is None else (color_us-self.previous_color_us)*1e-6
        self.previous_color_us=color_us
        self.profile_metadata['aligned_output']={'color':color_profile,'depth':depth_profile}
        self.last_metadata={'raw_timestamp':stamp,'camera_device_us':color_us,'camera_depth_device_us':depth_us,
            'clock_fit_residual_seconds':self.clock.residual,'color_depth_delta_seconds':(color_us-depth_us)*1e-6,
            'color_format':format_name,'hardware_trigger_claimed':False,'camera_available':True,
            'camera_profiles':deepcopy(self.profile_metadata),'camera_timestamps':{'source':source_times,'aligned_output':output_times,
                'observed_color_interval_seconds':interval},
            'camera_calibration_validation':{'calibration_id':self.calibration.calibration_id,'measured':True,
                'image_size':list(self.image_size),'intrinsics_tolerance_px':self.intrinsics_tolerance_px,
                'distortion_tolerance':self.distortion_tolerance,'color':color_validation,'depth':depth_validation}}
        self.last_clock_metadata={'metadata':self.last_metadata}
        return timestamp,rgb,depth_m,self.calibration

    def read(self):
        if self.pipeline is None:raise RuntimeError('Gemini camera is closed')
        if self._pending is not None:result=self._pending;self._pending=None;return result
        try:return self._acquire()
        except BaseException as exc:
            self.last_metadata={**self.last_metadata,'camera_available':False,'acquisition_error':str(exc)}
            self.last_clock_metadata={'metadata':self.last_metadata};self._close_after_error(exc);raise

    def _close_after_error(self,error):
        try:self.close()
        except Exception as cleanup:error.add_note(f'Gemini cleanup also failed: {cleanup}')

    def close(self):
        pipeline=self.pipeline;self.pipeline=None;self._pending=None
        try:
            if pipeline is not None and self._start_attempted:pipeline.stop()
        finally:self.align_filter=None;self.context=None


# The historical filename is retained to match the supplied repository tree.
Gemini333LDriver=Gemini335LDriver
