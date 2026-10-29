"""Bounded background recording; overruns are visible and never stall capture."""
from collections import deque
import json
import threading
from .bounded_acquisition import frame_bytes


class AsyncRecorder:
    def __init__(self,recorder,max_frames=8,max_bytes=64*1024*1024,required=True):
        if min(max_frames,max_bytes)<1:raise ValueError('Recording budgets must be positive')
        self.recorder=recorder;self.max_frames=int(max_frames);self.max_bytes=int(max_bytes);self.required=bool(required)
        self._cv=threading.Condition();self._pending=deque();self._bytes=0;self._inflight_bytes=0
        self._closed=False;self._error=None;self.written=0;self.dropped=0;self.peak_bytes=0
        self._thread=threading.Thread(target=self._run,name='raw-frame-recorder',daemon=False);self._thread.start()

    def write(self,frame):
        size=frame_bytes(frame)
        with self._cv:
            if self._closed:raise RuntimeError('Recording is closed')
            if self._error is not None:self.dropped+=1;return False
            used=self._bytes+self._inflight_bytes
            count=len(self._pending)+int(self._inflight_bytes>0)
            if size+used>self.max_bytes or count>=self.max_frames:
                self._error=RuntimeError('Recording queue budget exhausted; raw recording is incomplete')
                self.dropped+=1;return False
            self._pending.append((size,frame));self._bytes+=size;self.peak_bytes=max(self.peak_bytes,self._bytes+self._inflight_bytes)
            self._cv.notify();return True

    def _run(self):
        try:
            while True:
                with self._cv:
                    while not self._pending and not self._closed:self._cv.wait(.2)
                    if not self._pending:break
                    size,frame=self._pending.popleft();self._bytes-=size;self._inflight_bytes=size
                try:self.recorder.write(frame);self.written+=1
                finally:
                    with self._cv:self._inflight_bytes=0
        except BaseException as exc:
            with self._cv:
                self._error=exc;self.dropped+=len(self._pending);self._pending.clear();self._bytes=0
        finally:
            try:self.recorder.close()
            except BaseException as exc:
                with self._cv:self._error=exc
            path=getattr(self.recorder,'path',None)
            if path is not None:
                try:
                    report=self.status();report['schema_version']=1
                    temporary=path/'recording_status.json.tmp'
                    temporary.write_text(json.dumps(report,indent=2),encoding='utf-8')
                    temporary.replace(path/'recording_status.json')
                except BaseException as exc:
                    with self._cv:self._error=exc

    def status(self):
        with self._cv:return {'written_frames':self.written,'dropped_frames':self.dropped,
            'queued_frames':len(self._pending),'queued_bytes':self._bytes,'inflight_bytes':self._inflight_bytes,
            'capacity_frames':self.max_frames,'capacity_bytes':self.max_bytes,'peak_bytes':self.peak_bytes,
            'complete':self._error is None,'error':None if self._error is None else str(self._error)[:512],
            'recorder_chunk_budget_bytes':getattr(self.recorder,'max_buffer_bytes',None)}

    def close(self):
        with self._cv:self._closed=True;self._cv.notify_all()
        self._thread.join(timeout=30)
        if self._thread.is_alive():raise RuntimeError('Raw recorder failed to stop')
        if self._error is not None and self.required:raise RuntimeError('Raw recording is incomplete') from self._error
