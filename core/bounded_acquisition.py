"""Continuous acquisition with explicit byte/time budgets and retained IMU gaps."""
from __future__ import annotations
from collections import deque
from dataclasses import replace
import threading
import time
import numpy as np


def frame_bytes(frame):
    total=sum(value.nbytes for value in vars(frame).values() if isinstance(value,np.ndarray))
    return total+len(frame.imu_samples)*7*8+2048


class BufferedAcquisition:
    """Drain the source independently of inference; drops never invent IMU data.

    This adapter is intended for live input. Replay remains lossless and demand
    driven. Acquisition timestamps and original frames stay unchanged; an optional
    raw-frame sink receives each acquired frame before presentation coalescing.
    """
    def __init__(self,source,max_frames=2,max_bytes=64*1024*1024,max_age_seconds=.5,
                 imu_history_seconds=5.,max_imu_samples=8192,raw_sink=None):
        if min(max_frames,max_bytes)<1 or max_imu_samples<2 or min(max_age_seconds,imu_history_seconds)<=0:
            raise ValueError('Acquisition budgets must be positive')
        self.source=source;self.max_frames=int(max_frames);self.max_bytes=int(max_bytes)
        self.max_age=float(max_age_seconds);self.imu_seconds=float(imu_history_seconds);self.max_imu=int(max_imu_samples)
        self.raw_sink=raw_sink;self._items=deque();self._imu=deque();self._bytes=0
        self._cv=threading.Condition();self._stop=threading.Event();self._thread=None
        self._terminal=False;self._error=None;self._delivered_end=None;self._acquired_end=None
        self.dropped=0;self.acquired=0;self.peak_bytes=0;self.peak_frames=0;self.imu_dropped=0

    def start(self):
        if self._thread is not None:return self
        self._thread=threading.Thread(target=self._run,name='sensor-acquisition',daemon=False)
        self._thread.start();return self

    def _run(self):
        try:
            iterator=iter(self.source)
            while not self._stop.is_set():
                try:frame=next(iterator)
                except StopIteration:break
                now=time.monotonic();size=frame_bytes(frame);end=frame.timestamp+frame.sweep_duration
                if size>self.max_bytes:raise MemoryError('One sensor frame exceeds the configured acquisition byte budget')
                if self._acquired_end is not None and end<=self._acquired_end:
                    raise ValueError('Acquisition clock did not advance; relocalization/restart required')
                self._acquired_end=end
                if self.raw_sink is not None:self.raw_sink.write(frame)
                with self._cv:
                    for sample in frame.imu_samples:
                        if not self._imu or sample.timestamp>self._imu[-1].timestamp:self._imu.append(sample)
                    cutoff=end-self.imu_seconds
                    # Keep one bracketing sample on the older side.
                    while len(self._imu)>2 and (self._imu[1].timestamp<cutoff or len(self._imu)>self.max_imu):
                        self._imu.popleft();self.imu_dropped+=1
                    while self._items and (len(self._items)>=self.max_frames or self._bytes+size>self.max_bytes or now-self._items[0][0]>self.max_age):
                        _,old_size,_=self._items.popleft();self._bytes-=old_size;self.dropped+=1
                    self._items.append((now,size,frame));self._bytes+=size;self.acquired+=1
                    self.peak_bytes=max(self.peak_bytes,self._bytes);self.peak_frames=max(self.peak_frames,len(self._items));self._cv.notify_all()
        except BaseException as exc:
            with self._cv:self._error=exc
        finally:
            with self._cv:self._terminal=True;self._cv.notify_all()

    def __iter__(self):return self

    def __next__(self):
        self.start()
        with self._cv:
            while not self._items and not self._terminal and not self._stop.is_set():self._cv.wait(.2)
            if self._error is not None:raise RuntimeError('Continuous sensor acquisition failed') from self._error
            if not self._items:raise StopIteration
            queued_at,size,frame=self._items.popleft();self._bytes-=size
            start=frame.timestamp if self._delivered_end is None else min(frame.timestamp,self._delivered_end)
            end=frame.timestamp+frame.sweep_duration
            history=list(self._imu);selected=[sample for sample in history if start<=sample.timestamp<=end+.2]
            before=[sample for sample in history if sample.timestamp<start]
            if before:selected.insert(0,before[-1])
            self._delivered_end=end
            metrics=self._status_unlocked()
        return replace(frame,imu_samples=tuple(selected) if selected else frame.imu_samples,
                       diagnostics={**frame.diagnostics,'acquisition_queue':metrics,
                                    'acquisition_age_ms':1000*(time.monotonic()-queued_at),
                                    'acquired_at_monotonic':queued_at})

    def _status_unlocked(self):
        return {'acquired':self.acquired,'dropped_frames':self.dropped,'depth':len(self._items),
                'bytes':self._bytes,'capacity_frames':self.max_frames,'capacity_bytes':self.max_bytes,
                'peak_frames':self.peak_frames,'peak_bytes':self.peak_bytes,'imu_samples':len(self._imu),
                'imu_history_seconds':self.imu_seconds,'discarded_old_imu_samples':self.imu_dropped,
                'error':None if self._error is None else str(self._error)}

    def status(self):
        with self._cv:return self._status_unlocked()

    def close(self):
        self._stop.set()
        try:self.source.close()
        finally:
            with self._cv:self._cv.notify_all()
            if self._thread:
                self._thread.join(timeout=5)
                if self._thread.is_alive():raise RuntimeError('Sensor acquisition failed to stop')
