"""SDK-shaped fixtures validate profile negotiation without claiming device tests."""
import sys
import types
import numpy as np
import pytest
from core.contracts import CameraCalibration,IMUSample,MultiModalSensorFrame
from ingestion.gemini_333l_driver import Gemini335LDriver
from ingestion.sensor_synchronizer import SensorSynchronizer


def fake_sdk(monkeypatch,*,color_size=(8,6),depth_size=(6,4),compatible=('hardware',),
             depth_output_size=None,intrinsic_error=0.,distortion_error=0.,missing=None,
             api_available=True,start_error=False,fail_after_first=False,pair_fps=30,color_fps=30):
    """Model actual SDK source profiles separately from delivered aligned frames."""
    color_K=np.array([[400.,0,(color_size[0]-1)/2],[0,401.,(color_size[1]-1)/2],[0,0,1.]])
    calls=types.SimpleNamespace(pipelines=[],align_filters=[],pair_queries=[])
    class Profile:
        def __init__(self,width,height,fmt,fps=30,K=None):
            self.width=width;self.height=height;self.fmt=fmt;self.fps=fps;self.K=color_K.copy() if K is None else K.copy()
        def as_video_stream_profile(self):return self
        def get_width(self):return self.width
        def get_height(self):return self.height
        def get_fps(self):return self.fps
        def get_format(self):return self.fmt
        def get_intrinsic(self):return types.SimpleNamespace(width=self.width,height=self.height,fx=self.K[0,0],fy=self.K[1,1],cx=self.K[0,2],cy=self.K[1,2])
        def get_distortion(self):return types.SimpleNamespace(model='BROWN_CONRADY',k1=distortion_error,k2=0.,p1=0.,p2=0.,k3=0.,k4=0.,k5=0.,k6=0.)
    class Options:
        def __init__(self,profiles):self.profiles=profiles
        def get_count(self):return len(self.profiles)
        def get_stream_profile_by_index(self,index):return self.profiles[index]
    color_profile=Profile(*color_size,'RGB',fps=color_fps);source_depth=Profile(*depth_size,'Y16')
    paired_depth=Profile(*depth_size,'Y16',pair_fps)
    out_K=color_K.copy();out_K[0,0]+=intrinsic_error
    delivered_depth=Profile(*(depth_output_size or color_size),'Y16',K=out_K)
    delivered_color=Profile(*color_size,'RGB',fps=color_fps,K=out_K)
    class DeviceList:
        def get_count(self):return 1
        def get_device_by_index(self,index):return 'fake-device'
        def get_device_by_serial_number(self,serial):return 'fake-device'
    class Context:
        def query_devices(self):return DeviceList()
    class Config:
        def __init__(self):self.streams=[];self.align=None;self.scale=False
        def enable_stream(self,profile):self.streams.append(profile)
        def set_align_mode(self,mode):self.align=mode
        def set_depth_scale_require(self,enable):self.scale=enable
    class Frame:
        def __init__(self,profile,color,index):self.profile=profile;self.color=color;self.index=index
        def get_stream_profile(self):return self.profile
        def get_width(self):return self.profile.width
        def get_height(self):return self.profile.height
        def get_format(self):return self.profile.fmt
        def get_data(self):
            return np.full((self.get_height(),self.get_width(),3),100,np.uint8) if self.color else np.full((self.get_height(),self.get_width()),1500,np.uint16)
        def get_depth_scale(self):return 1.
        def get_timestamp_us(self):return 123456000+self.index*33333-(0 if self.color else 500)
        def get_system_timestamp_us(self):return 1000000000+self.get_timestamp_us()
        def get_global_timestamp_us(self):raise RuntimeError('global sensor clock unavailable')
    class Frames:
        def __init__(self,index,aligned):self.index=index;self.aligned=aligned
        def get_color_frame(self):return None if missing=='color' else Frame(delivered_color,True,self.index)
        def get_depth_frame(self):return None if missing=='depth' else Frame(delivered_depth if self.aligned else source_depth,False,self.index)
        def as_frame_set(self):return self
    class Pipeline:
        def __init__(self,device):self.config=None;self.stops=0;self.waits=0;self.synced=False;calls.pipelines.append(self)
        def get_stream_profile_list(self,sensor):return Options([color_profile] if sensor=='color' else [source_depth])
        def get_d2c_depth_profile_list(self,color,mode):
            calls.pair_queries.append((color,mode));return Options([paired_depth] if mode in compatible else [])
        def enable_frame_sync(self):self.synced=True
        def start(self,config):
            self.config=config
            if start_error:raise RuntimeError('USB startup failed')
        def wait_for_frames(self,timeout):
            self.waits+=1
            if fail_after_first and self.waits>1:return None
            return Frames(self.waits,self.config.align=='hardware')
        def stop(self):self.stops+=1
    if not api_available:del Pipeline.get_d2c_depth_profile_list
    class AlignFilter:
        def __init__(self,align_to_stream):self.target=None;self.match=False;self.processed=0;calls.align_filters.append(self)
        def set_align_to_stream_profile(self,profile):self.target=profile
        def set_match_target_resolution(self,enable):self.match=enable
        def process(self,frames):self.processed+=1;return Frames(frames.index,True)
    sdk=types.SimpleNamespace(Context=Context,Config=Config,Pipeline=Pipeline,AlignFilter=AlignFilter,
        OBFormat=types.SimpleNamespace(RGB='RGB',BGR='BGR',MJPG='MJPG',YUYV='YUYV',Y16='Y16'),
        OBAlignMode=types.SimpleNamespace(HW_MODE='hardware',SW_MODE='software',DISABLE='disabled'),
        OBSensorType=types.SimpleNamespace(COLOR_SENSOR='color',DEPTH_SENSOR='depth'),OBStreamType=types.SimpleNamespace(COLOR_STREAM='color'))
    monkeypatch.setitem(sys.modules,'pyorbbecsdk',sdk)
    calibration=CameraCalibration(color_K,np.eye(4),calibration_id='measured-fixture',measured=True)
    kwargs={'width':depth_size[0],'height':depth_size[1],'fps':30,'calibration_image_size':color_size}
    return calibration,kwargs,calls


def test_mixed_1920_color_1280_depth_keeps_requested_source_and_validates_aligned_output(monkeypatch):
    calibration,kwargs,calls=fake_sdk(monkeypatch,color_size=(1920,1080),depth_size=(1280,800))
    before=calibration.K.copy();camera=Gemini335LDriver(calibration,**kwargs)
    pipeline=calls.pipelines[0];assert pipeline.waits==1 and pipeline.config.scale and pipeline.synced
    timestamp,rgb,depth,returned_calibration=camera.read()
    assert pipeline.waits==1 and rgb.shape==(1080,1920,3) and depth.shape==(1080,1920)
    np.testing.assert_allclose(depth,1.5);np.testing.assert_array_equal(calibration.K,before)
    assert returned_calibration is calibration
    profiles=camera.last_metadata['camera_profiles']
    assert profiles['selected_source_depth']=={'width':1280,'height':800,'fps':30,'format':'Y16'}
    assert profiles['selected_source_color']['width']==1920 and profiles['aligned_output']['depth']['height']==1080
    assert camera.last_metadata['camera_calibration_validation']['depth']['max_intrinsics_error_px']==0
    times=camera.last_metadata['camera_timestamps']
    assert times['source']['depth']['device_us']==times['aligned_output']['depth']['device_us']
    assert times['source']['color']['available']=={'device_us':True,'system_us':True,'global_us':False}
    assert times['source']['color']['global_us'] is None
    camera.close();camera.close();assert pipeline.stops==1 and camera.pipeline is None and camera.context is None


def test_sdk_supported_software_pair_uses_geometric_align_filter(monkeypatch):
    calibration,kwargs,calls=fake_sdk(monkeypatch,compatible=('software',))
    camera=Gemini335LDriver(calibration,**kwargs)
    pipeline=calls.pipelines[0];filter=calls.align_filters[0]
    assert pipeline.config.align=='disabled' and filter.match and filter.processed==1
    assert filter.target.get_width()==8 and camera.last_metadata['camera_profiles']['alignment']=='software'
    assert [mode for _,mode in calls.pair_queries]==['hardware','software']
    _,rgb,depth,_=camera.read();assert rgb.shape[:2]==depth.shape==(6,8)
    camera.close()


@pytest.mark.parametrize('pair_fps', [30,60])
def test_unapproved_or_wrong_fps_pair_fails_without_changing_requested_depth(monkeypatch,pair_fps):
    calibration,kwargs,calls=fake_sdk(monkeypatch,compatible=() if pair_fps==30 else ('hardware','software'),pair_fps=pair_fps)
    with pytest.raises(ValueError,match='Unsupported D2C'):
        Gemini335LDriver(calibration,**kwargs)
    assert calls.pipelines[0].config is None and calls.pipelines[0].stops==0


def test_missing_d2c_pairing_api_fails_explicitly(monkeypatch):
    calibration,kwargs,calls=fake_sdk(monkeypatch,api_available=False)
    with pytest.raises(RuntimeError,match='D2C pairing API unavailable'):Gemini335LDriver(calibration,**kwargs)
    assert calls.pipelines[0].config is None


@pytest.mark.parametrize('failure,match',[({'intrinsic_error':3.},'intrinsics mismatch'),
    ({'distortion_error':.01},'distortion mismatch'),({'depth_output_size':(7,6)},'shape'),
    ({'missing':'depth'},'Incomplete')])
def test_aligned_calibration_or_modality_mismatch_stops_started_pipeline(monkeypatch,failure,match):
    calibration,kwargs,calls=fake_sdk(monkeypatch,**failure)
    original=calibration.K.copy()
    with pytest.raises((ValueError,RuntimeError),match=match):Gemini335LDriver(calibration,**kwargs)
    np.testing.assert_array_equal(calibration.K,original)
    assert calls.pipelines[0].stops==1


def test_start_failure_and_later_acquisition_failure_cleanup(monkeypatch):
    calibration,kwargs,calls=fake_sdk(monkeypatch,start_error=True)
    with pytest.raises(RuntimeError,match='USB startup failed'):Gemini335LDriver(calibration,**kwargs)
    assert calls.pipelines[0].stops==1
    calibration,kwargs,calls=fake_sdk(monkeypatch,fail_after_first=True)
    camera=Gemini335LDriver(calibration,**kwargs);camera.read()
    with pytest.raises(TimeoutError):camera.read()
    assert camera.pipeline is None and calls.pipelines[0].stops==1
    assert camera.last_metadata['camera_available'] is False


def test_profile_timestamp_and_validation_metadata_survive_synchronizer(monkeypatch):
    calibration,kwargs,calls=fake_sdk(monkeypatch)
    camera=Gemini335LDriver(calibration,**kwargs);item=camera.read();timestamp=item[0]
    sync=SensorSynchronizer()
    imu=(IMUSample(timestamp-.1,[0,0,0],[0,0,9.80665]),IMUSample(timestamp,[0,0,0],[0,0,9.80665]))
    sync.push_lidar(MultiModalSensorFrame(0,timestamp-.1,np.empty((0,7)),imu))
    sync.push_camera(*item,**camera.last_clock_metadata)
    camera.profile_metadata['selected_source_depth']['width']=999
    frame=sync.synchronize();assert frame is not None
    assert frame.diagnostics['camera_profiles']['selected_source_depth']['width']==6
    assert frame.diagnostics['camera_available'] is True
    assert frame.diagnostics['camera_calibration_validation']['calibration_id']=='measured-fixture'
    assert frame.diagnostics['camera_timestamps']['source']['depth']['available']['device_us'] is True
    with pytest.raises(TypeError):frame.diagnostics['camera_profiles']['alignment']='changed'
    camera.close()


def test_live_calibration_requires_explicit_image_dimensions(monkeypatch):
    calibration,kwargs,calls=fake_sdk(monkeypatch)
    kwargs.pop('calibration_image_size')
    with pytest.raises(ValueError,match='image_size'):Gemini335LDriver(calibration,**kwargs)
    assert not calls.pipelines


def test_color_fps_is_independent_of_requested_depth_cadence(monkeypatch):
    calibration,kwargs,calls=fake_sdk(monkeypatch,color_fps=15)
    camera=Gemini335LDriver(calibration,**kwargs,color_fps=15)
    assert camera.last_metadata['camera_profiles']['selected_source_color']['fps']==15
    assert camera.last_metadata['camera_profiles']['selected_source_depth']['fps']==30
    camera.close()


def test_explicit_hardware_mode_does_not_fall_back_to_software(monkeypatch):
    calibration,kwargs,calls=fake_sdk(monkeypatch,compatible=('software',))
    with pytest.raises(ValueError,match='Unsupported D2C'):Gemini335LDriver(calibration,**kwargs,align_mode='hardware')
    assert all(mode=='hardware' for _,mode in calls.pair_queries)
    assert not calls.align_filters


def test_unsupported_source_depth_profile_is_not_replaced(monkeypatch):
    calibration,kwargs,calls=fake_sdk(monkeypatch)
    kwargs['fps']=60
    with pytest.raises(ValueError,match='source depth profile'):Gemini335LDriver(calibration,**kwargs,color_fps=30)
    assert calls.pipelines[0].config is None
