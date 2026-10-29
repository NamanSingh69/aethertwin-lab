"""Bounded latest-only 30 Hz display publisher; physical serial is opt-in."""
from collections import deque
import copy
import threading,time
from .protocol import HELLO,IDENTITY,encode_packet
from .state import halo_state


class HaloController:
    def __init__(self,config=None,*,serial_factory=None):
        config=config or {}
        if not isinstance(config.get('enabled',False),bool):raise ValueError('Halo enabled must be boolean')
        self.enabled=config.get('enabled',False);self.port=config.get('port')
        self.hz=float(config.get('publish_hz',30));self.baudrate=int(config.get('baudrate',115200))
        self.handshake_timeout=float(config.get('handshake_timeout_seconds',2.));self.serial_factory=serial_factory
        self.options={key:config.get(key,value) for key,value in {'stale_after_ms':1000,'watchdog_ms':2000,'brightness':.25}.items()}
        if not 1<=self.hz<=60 or self.baudrate!=115200 or not 0<self.handshake_timeout<=30:raise ValueError('Invalid Halo transport rate/profile')
        if self.enabled and (not isinstance(self.port,str) or not self.port.strip()):raise ValueError('Enabled Halo needs one explicitly configured serial port')
        halo_state(None,**self.options)
        self._snapshot=None;self._lock=threading.Lock();self._stop=threading.Event();self._serial=None
        self._updates=deque(maxlen=128);self._last_identity=None;self._sequence=0;self._sent=0;self._ticks=0
        self._connected=False;self._compatible=False;self._error=None;self._virtual=halo_state(None,**self.options)
        self._thread=threading.Thread(target=self._run,name='aether-halo-status',daemon=False);self._thread.start()
        self._transport_thread=None
        if self.enabled:
            self._transport_thread=threading.Thread(target=self._run_transport,name='aether-halo-serial',daemon=False);self._transport_thread.start()
    def publish(self,snapshot):
        identity=(getattr(snapshot,'frame_id',None),getattr(snapshot,'processed_at_unix',None))
        if isinstance(snapshot,dict):identity=(snapshot.get('frame_id'),snapshot.get('processed_at_unix'))
        with self._lock:
            self._snapshot=snapshot
            if identity!=self._last_identity:self._updates.append(time.monotonic());self._last_identity=identity
    def _handshake(self):
        factory=self.serial_factory
        if factory is None:
            try:from serial import Serial
            except ImportError as error:raise RuntimeError('Optional pyserial is not installed; physical Halo is disconnected') from error
            factory=Serial
        transport=factory(port=self.port,baudrate=self.baudrate,timeout=0,write_timeout=.1)
        try:
            deadline=time.monotonic()+self.handshake_timeout;next_hello=0.;buffer=bytearray()
            while time.monotonic()<deadline and not self._stop.is_set():
                if time.monotonic()>=next_hello:transport.write(HELLO);next_hello=time.monotonic()+.25
                data=transport.read(128)
                if data:
                    buffer.extend(data)
                    if len(buffer)>1024:raise RuntimeError('Halo handshake exceeded bounded response size')
                    while b'\n' in buffer:
                        line,_,remaining=buffer.partition(b'\n');buffer=bytearray(remaining)
                        if line.rstrip(b'\r')==IDENTITY:self._compatible=True;self._connected=True;return transport
                self._stop.wait(.01)
            raise RuntimeError('Configured serial device did not identify as the compatible 24-pixel Halo')
        except BaseException:transport.close();raise
    def _run_transport(self):
        try:
            if self.enabled:
                try:self._serial=self._handshake()
                except Exception as error:self._error=f'{type(error).__name__}: {error}'
            deadline=time.monotonic();seen_ticks=-1;ticked_at=time.monotonic()
            while not self._stop.is_set():
                with self._lock:state=copy.deepcopy(self._virtual);ticks=self._ticks
                if ticks!=seen_ticks:seen_ticks=ticks;ticked_at=time.monotonic()
                # A stalled status worker must not keep refreshing the device's link watchdog with a frozen state.
                fresh=time.monotonic()-ticked_at<=2/self.hz+.05
                if self._serial is not None and fresh:
                    try:
                        packet=encode_packet(state,self._sequence)
                        written=self._serial.write(packet)
                        if written!=len(packet):raise RuntimeError('Incomplete Halo serial write')
                        self._sequence=(self._sequence+1)&0xFFFF;self._sent+=1
                    except Exception as error:
                        self._error=f'{type(error).__name__}: {error}';self._connected=False
                        try:self._serial.close()
                        except Exception:pass
                        self._serial=None
                deadline+=1/self.hz;self._stop.wait(max(0,deadline-time.monotonic()))
                if deadline<time.monotonic()-1/self.hz:deadline=time.monotonic()
        finally:
            if self._serial is not None:self._serial.close();self._serial=None
            self._connected=False
    def _run(self):
        deadline=time.monotonic()
        while not self._stop.is_set():
            with self._lock:snapshot=self._snapshot
            try:state=halo_state(snapshot,**self.options);error=None
            except Exception as failure:
                # A malformed snapshot must show as disconnected, never keep the last (possibly clear) state alive.
                error=f'Invalid Halo snapshot: {type(failure).__name__}: {failure}'
                state={**halo_state(None,**self.options),'reason':'invalid_snapshot'}
            with self._lock:
                self._virtual=state;self._ticks+=1
                if error:self._error=error
            deadline+=1/self.hz;self._stop.wait(max(0,deadline-time.monotonic()))
            if deadline<time.monotonic()-1/self.hz:deadline=time.monotonic()
    def status(self):
        with self._lock:
            updates=list(self._updates);virtual=copy.deepcopy(self._virtual)
        source_hz=(len(updates)-1)/(updates[-1]-updates[0]) if len(updates)>1 and updates[-1]>updates[0] and time.monotonic()-updates[-1]<self.options['watchdog_ms']/1000 else 0.
        return {'enabled':self.enabled,'connected':self._connected,'compatible':self._compatible,'port':self.port if self.enabled else None,
            'status_hz':self.hz,'source_update_hz':source_hz,'packets_sent':self._sent,'status_ticks':self._ticks,
            'next_sequence':self._sequence,'last_error':self._error,'virtual':virtual,'hardware_verified':False}
    def close(self):
        self._stop.set();self._thread.join(timeout=max(1,self.handshake_timeout+.2))
        if self._transport_thread:self._transport_thread.join(timeout=max(1,self.handshake_timeout+.2))
        if self._thread.is_alive():raise RuntimeError('Halo status worker did not stop')
        if self._transport_thread and self._transport_thread.is_alive():raise RuntimeError('Halo serial worker did not stop')
