"""Opt-in GPU load that holds a laptop GPU out of its idle power state between live frames (AIDE2 C46).

Why it exists: at the live pace (one frame every ~0.2 s) the RTX 4070 laptop GPU stays in P8 (210 MHz SM, 405 MHz
memory) and the camera CNN takes ~58 ms instead of ~3.2 ms back to back. The driver raises the clocks only under
sustained load: a tiny op (a 1-element add or a 64x64 matmul) every 2-20 ms changed nothing (still P8, ~58 ms). Only
a real load of ~38% GPU utilisation held P4/P0 (2048x2048 matmul every 10 ms: CNN ~10 ms, +8 W; 3072x3072 every
20 ms: CNN ~4.2 ms, +18 to +20 W, measured on that laptop). So this is a power burner, not a free keep-alive.
The preferred fix is the owner's power setting (Windows power plan or NVIDIA "prefer maximum performance"); use
this only where that cannot be changed. Off (0) by default; tests/test_aide2_c46_gpu_keepalive.py.
"""
import math,threading,time,warnings
import torch

THREAD_NAME='aethertwin-gpu-keepalive'


class GpuKeepAlive:
    """A daemon thread that launches `op` every period_ms (a matmul on its own CUDA stream unless `op` is given).
    The caller never waits on it: start() and stop() return within one period plus one launch."""
    def __init__(self,period_ms,matmul=3072,device='cuda',op=None):
        if isinstance(period_ms,bool) or not isinstance(period_ms,(int,float)) or not math.isfinite(period_ms) or period_ms<=0:
            raise ValueError('perception.gpu_keepalive_ms must be a positive finite number of milliseconds')
        if isinstance(matmul,bool) or not isinstance(matmul,int) or matmul<1:raise ValueError('perception.gpu_keepalive_matmul must be a positive integer')
        self.period=float(period_ms)/1000;self.matmul=matmul;self.device=device;self.op=op
        self.stream=None;self.launches=0;self.status='configured';self._thread=None;self._stop=threading.Event();self._lock=threading.Lock()

    @staticmethod
    def from_config(config):
        """The configured keep-alive, not started; None when off (0) or when no CUDA perception device (warns once)."""
        perception=config.get('perception') or {};period=perception.get('gpu_keepalive_ms',0)
        if isinstance(period,bool) or not isinstance(period,(int,float)) or not math.isfinite(period) or period<0:
            raise ValueError('perception.gpu_keepalive_ms must be 0 (off) or a positive finite number of milliseconds')
        if period==0:return None
        device=torch.device(config.get('device','cpu'))
        if device.type!='cuda' or not torch.cuda.is_available():
            warnings.warn(f'perception.gpu_keepalive_ms={period} needs a CUDA perception device (device {device}, '
                          f'CUDA available {torch.cuda.is_available()}); keep-alive not started',RuntimeWarning,stacklevel=2)
            return None
        return GpuKeepAlive(period,perception.get('gpu_keepalive_matmul',3072),device)

    @property
    def running(self):return self._thread is not None and self._thread.is_alive()

    def start(self):
        with self._lock:
            if self.running:return self
            op=self.op
            if op is None:
                if self.stream is None:
                    self.stream=torch.cuda.Stream(device=self.device)
                    with torch.cuda.stream(self.stream):self._x=torch.rand(self.matmul,self.matmul,device=self.device)/self.matmul
                def op():
                    with torch.cuda.stream(self.stream):self._x=torch.tanh(self._x@self._x)
            self._stop=threading.Event();self.status='running'
            self._thread=threading.Thread(target=self._run,args=(op,self._stop),name=THREAD_NAME,daemon=True);self._thread.start()
        return self

    def _run(self,op,stop):
        try:
            if self.op is None:torch.cuda.set_device(self.stream.device)  # a new thread has no current CUDA context
            with torch.inference_mode():
                while not stop.is_set():
                    time.sleep(self.period)  # high-resolution on Windows (Python >= 3.11); Event.wait ticks at ~15.6 ms
                    if stop.is_set():break
                    op();self.launches+=1
            self.status='stopped'
        except Exception as exc:self.status=f'failed: {type(exc).__name__}: {exc}'[:300]

    def stop(self,timeout=2.):
        with self._lock:
            thread=self._thread;self._stop.set()
            if thread is not None:thread.join(timeout);self._thread=None if not thread.is_alive() else thread
            if self.stream is not None and self._thread is None:self.stream.synchronize()
