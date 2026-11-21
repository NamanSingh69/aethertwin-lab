"""Authenticated, versioned raw-sensor transport between the Pi capture node and the processing host.

Every message is ``[u32 length][header][meta JSON][payload][tag]`` with the
header ``>4sHBBQII`` = magic ``ATRS``, protocol version, type, flags, sequence,
meta length and payload length. Before authentication only HELLO, AUTH,
AUTH_OK and REJECT are accepted; they carry no tag and are size limited.
Authentication is a mutual HMAC-SHA256 challenge over both nonces with a shared
key; each direction then has its own derived key, every message carries an
HMAC-SHA256 tag over header, meta and payload, and the sequence must increase
by exactly one per direction, so injected, replayed, reordered or truncated
messages are rejected. Confidentiality is not provided: the link is a private
sensor network, and a key never crosses it.

Timestamps: the capture node sends its own monotonic clock (``capture_mono``)
next to every raw device timestamp. The processing host maps that clock onto
its own monotonic clock with an NTP-style ping exchange (:class:`PeerClock`),
slewing slowly and declaring a clock jump instead of splicing time.
"""
from __future__ import annotations
from collections import deque
import hashlib,hmac,json,os,secrets,socket,stat,struct,time,zlib
from pathlib import Path
import numpy as np

MAGIC=b'ATRS';VERSION=1;PROTOCOL='aethertwin-raw-sensor'
HEADER=struct.Struct('>4sHBBQII');LENGTH=struct.Struct('>I');TAG_BYTES=32
HELLO,AUTH,AUTH_OK,REJECT=1,2,3,4
IDENTITY,START=10,11
IMU,CLOUD,CAMERA=20,21,22
FAULT,GAP=30,31
PING,PONG=40,41
BYE=50
NAMES={HELLO:'hello',AUTH:'auth',AUTH_OK:'auth_ok',REJECT:'reject',IDENTITY:'identity',START:'start',IMU:'imu',CLOUD:'cloud',
       CAMERA:'camera',FAULT:'fault',GAP:'gap',PING:'ping',PONG:'pong',BYE:'bye'}
PREAUTH=frozenset((HELLO,AUTH,AUTH_OK,REJECT));PREAUTH_MAX=4096
MAX_META=1024*1024;MAX_MESSAGE=64*1024*1024
# IMU rows: device timestamp, capture monotonic, gyro xyz [rad/s], accel xyz (SDK units).
IMU_COLUMNS=8
DEPTH_CODECS=('raw','zlib','png');COLOR_CODECS=('device','raw','png','jpeg')
MIN_KEY_BYTES=32


class TransportError(RuntimeError):
    """Any framing, version, authentication or sequencing violation; the connection is closed."""


class AuthenticationError(TransportError):pass


def generate_key(path):
    """Write a new random shared key (hex) readable only by the owner; refuse to overwrite."""
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    descriptor=os.open(path,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
    with os.fdopen(descriptor,'w',encoding='ascii') as stream:stream.write(secrets.token_hex(MIN_KEY_BYTES)+'\n')
    return path


def load_key(path):
    path=Path(path)
    if os.name=='posix' and path.stat().st_mode&(stat.S_IRWXG|stat.S_IRWXO):
        raise PermissionError(f'Capture key {path} must not be readable by group or others (chmod 600)')
    text=path.read_text(encoding='ascii').strip()
    try:key=bytes.fromhex(text)
    except ValueError:raise ValueError('Capture key must be hex encoded') from None
    if len(key)<MIN_KEY_BYTES:raise ValueError(f'Capture key must hold at least {MIN_KEY_BYTES} random bytes')
    return key


def _mac(key,label,*parts):return hmac.new(key,label+b''.join(parts),hashlib.sha256).digest()


def derive_keys(key,server_nonce,client_nonce):
    """Directional session keys; both depend on both nonces so a session cannot be replayed."""
    return _mac(key,b'aethertwin s2c',server_nonce,client_nonce),_mac(key,b'aethertwin c2s',server_nonce,client_nonce)


def server_proof(key,server_nonce,client_nonce):return _mac(key,b'aethertwin server',client_nonce,server_nonce)
def client_proof(key,server_nonce,client_nonce):return _mac(key,b'aethertwin client',server_nonce,client_nonce)


def _json(value):
    def default(item):
        if isinstance(item,np.ndarray):return item.tolist()
        if isinstance(item,np.generic):return item.item()
        if isinstance(item,(set,frozenset,tuple)):return list(item)
        raise TypeError(f'Not JSON serialisable: {type(item).__name__}')
    return json.dumps(value,separators=(',',':'),default=default,allow_nan=False).encode('utf-8')


class Channel:
    """One end of a framed connection. Not thread safe: use one sender and one receiver thread."""
    def __init__(self,sock,*,max_message=MAX_MESSAGE):
        self.sock=sock;self.max_message=int(max_message)
        self.send_key=None;self.recv_key=None;self.send_seq=0;self.recv_seq=0
        self.bytes_sent=0;self.bytes_received=0;self.messages_sent=0;self.messages_received=0
        self.deadline=None  # monotonic time bounding every read (handshakes), or None
    def authenticate(self,send_key,recv_key):
        self.send_key=send_key;self.recv_key=recv_key;self.send_seq=0;self.recv_seq=0
    def encode(self,kind,meta=None,payload=b''):
        meta_bytes=b'' if meta is None else _json(meta);payload=memoryview(payload).cast('B') if payload else b''
        if len(meta_bytes)>MAX_META:raise TransportError('Message metadata exceeds the limit')
        authenticated=self.send_key is not None
        if not authenticated and kind not in PREAUTH:raise TransportError(f'{NAMES.get(kind,kind)} requires an authenticated channel')
        self.send_seq+=1
        header=HEADER.pack(MAGIC,VERSION,kind,0,self.send_seq,len(meta_bytes),len(payload))
        size=len(header)+len(meta_bytes)+len(payload)+(TAG_BYTES if authenticated else 0)
        if size>self.max_message:raise TransportError('Message exceeds the transport size limit')
        parts=[LENGTH.pack(size),header,meta_bytes,payload]
        if authenticated:
            mac=hmac.new(self.send_key,header,hashlib.sha256);mac.update(meta_bytes);mac.update(payload);parts.append(mac.digest())
        return parts
    def send(self,kind,meta=None,payload=b''):
        parts=self.encode(kind,meta,payload)
        # sendall per part avoids copying multi-megabyte payloads into one buffer.
        for part in parts:
            if len(part):self.sock.sendall(part)
        size=sum(len(p) for p in parts);self.bytes_sent+=size;self.messages_sent+=1;return size
    def _read(self,count,idle_ok=False):
        buffer=bytearray(count);view=memoryview(buffer);read=0
        while read<count:
            if self.deadline is not None:
                remaining=self.deadline-time.monotonic()
                if remaining<=0:raise TransportError('Handshake deadline exceeded')
                self.sock.settimeout(remaining)
            try:received=self.sock.recv_into(view[read:],count-read)
            except (TimeoutError,socket.timeout):
                # Idle between messages is the caller's decision; a stall inside one desynchronises framing.
                if idle_ok and read==0:raise
                raise TransportError('Peer stalled in the middle of a message') from None
            if not received:raise ConnectionError('Peer closed the raw-sensor connection')
            read+=received
        return buffer
    def receive(self):
        size=LENGTH.unpack(self._read(LENGTH.size,idle_ok=True))[0]
        limit=self.max_message if self.recv_key is not None else PREAUTH_MAX
        if size<HEADER.size or size>limit:raise TransportError(f'Invalid message length {size}')
        data=self._read(size);view=memoryview(data)
        magic,version,kind,flags,seq,meta_len,payload_len=HEADER.unpack_from(data)
        if magic!=MAGIC:raise TransportError('Not an AetherTwin raw-sensor message')
        if version!=VERSION:raise TransportError(f'Unsupported raw-sensor protocol version {version}; this host speaks {VERSION}')
        authenticated=self.recv_key is not None;tag=TAG_BYTES if authenticated else 0
        if HEADER.size+meta_len+payload_len+tag!=size or meta_len>MAX_META:raise TransportError('Inconsistent message lengths')
        if authenticated:
            body=view[:size-TAG_BYTES]
            if not hmac.compare_digest(hmac.new(self.recv_key,body,hashlib.sha256).digest(),bytes(view[size-TAG_BYTES:])):
                raise AuthenticationError('Message authentication failed; the connection is closed')
        elif kind not in PREAUTH:raise TransportError(f'{NAMES.get(kind,kind)} received before authentication')
        if seq!=self.recv_seq+1:raise TransportError(f'Sequence {seq} after {self.recv_seq}: replayed, reordered or missing message')
        self.recv_seq=seq;self.bytes_received+=size+LENGTH.size;self.messages_received+=1
        try:meta=json.loads(bytes(view[HEADER.size:HEADER.size+meta_len]).decode('utf-8')) if meta_len else {}
        except (ValueError,RecursionError,UnicodeDecodeError) as exc:raise TransportError(f'Malformed message metadata: {type(exc).__name__}') from None
        if not isinstance(meta,dict):raise TransportError('Message metadata must be an object')
        payload=view[HEADER.size+meta_len:HEADER.size+meta_len+payload_len]
        return kind,meta,payload


def server_handshake(channel,key,identity_fn,timeout=5.):
    """Capture-node side. Returns the client's AUTH metadata; raises on any failure."""
    nonce=secrets.token_bytes(32);channel.sock.settimeout(timeout);channel.deadline=time.monotonic()+timeout
    channel.send(HELLO,{'protocol':PROTOCOL,'version':VERSION,'nonce':nonce.hex()})
    kind,meta,_=channel.receive()
    if kind!=AUTH:raise TransportError('Expected AUTH')
    try:client_nonce=bytes.fromhex(str(meta.get('nonce','')));proof=bytes.fromhex(str(meta.get('proof','')))
    except ValueError:client_nonce=proof=b''
    if meta.get('protocol')!=PROTOCOL or meta.get('version')!=VERSION or len(client_nonce)!=32 or not hmac.compare_digest(proof,client_proof(key,nonce,client_nonce)):
        try:channel.send(REJECT,{'reason':'authentication failed'})
        except OSError:pass
        raise AuthenticationError('Client failed authentication')
    channel.send(AUTH_OK,{'proof':server_proof(key,nonce,client_nonce).hex()})
    s2c,c2s=derive_keys(key,nonce,client_nonce);channel.authenticate(s2c,c2s);channel.deadline=None
    channel.send(IDENTITY,identity_fn());return meta


def client_handshake(channel,key,timeout=5.):
    """Processing-host side. Verifies the server too, so a stray device cannot pose as the capture node."""
    channel.sock.settimeout(timeout);channel.deadline=time.monotonic()+timeout;kind,meta,_=channel.receive()
    if kind!=HELLO:raise TransportError('Expected HELLO')
    if meta.get('protocol')!=PROTOCOL:raise TransportError('Peer is not an AetherTwin capture node')
    if meta.get('version')!=VERSION:raise TransportError(f'Capture node speaks protocol version {meta.get("version")}; this host speaks {VERSION}')
    try:nonce=bytes.fromhex(str(meta.get('nonce','')))
    except ValueError:nonce=b''
    if len(nonce)!=32:raise TransportError('Invalid server nonce')
    client_nonce=secrets.token_bytes(32)
    channel.send(AUTH,{'protocol':PROTOCOL,'version':VERSION,'nonce':client_nonce.hex(),'proof':client_proof(key,nonce,client_nonce).hex()})
    kind,meta,_=channel.receive()
    if kind==REJECT:raise AuthenticationError(f'Capture node rejected authentication: {meta.get("reason")}')
    try:proof=bytes.fromhex(str(meta.get('proof','')))
    except ValueError:proof=b''
    if kind!=AUTH_OK or not hmac.compare_digest(proof,server_proof(key,nonce,client_nonce)):
        raise AuthenticationError('Capture node failed authentication (wrong key or impostor)')
    s2c,c2s=derive_keys(key,nonce,client_nonce);channel.authenticate(c2s,s2c)
    kind,identity,_=channel.receive();channel.deadline=None
    if kind!=IDENTITY:raise TransportError('Expected IDENTITY')
    return identity


class PeerClock:
    """Maps the capture node's monotonic clock onto this host's monotonic clock.

    Each ping gives offset = ((t2-t1)+(t3-t4))/2 (node minus host) and delay
    = (t4-t1)-(t3-t2). The target offset is the one with the smallest delay
    among recent samples; its error is bounded by half that delay. The applied
    offset slews toward the target at most ``slew`` seconds per second, so the
    mapping stays continuous and monotonic. A disagreement above ``jump`` is a
    clock jump: the caller starts a new segment and :meth:`anchor` re-anchors.
    """
    def __init__(self,window=8,slew=1e-4,jump=.005):
        self.samples=deque(maxlen=int(window));self.slew=float(slew);self.jump=float(jump)
        self.applied=None;self.applied_at=None;self.utc=None;self.count=0
    def add(self,t1,t2,t3,t4,node_utc_minus_mono=None,host_utc_minus_mono=None):
        if not t1<=t4 or not t2<=t3:raise TransportError('Clock exchange timestamps are not ordered')
        offset=((t2-t1)+(t3-t4))/2;delay=(t4-t1)-(t3-t2)
        self.samples.append((max(delay,0.),offset));self.count+=1
        if node_utc_minus_mono is not None and host_utc_minus_mono is not None:
            self.utc=offset+float(node_utc_minus_mono)-float(host_utc_minus_mono)
        return offset,delay
    def target(self):
        samples=tuple(self.samples)  # the receiver thread appends while other threads read
        if not samples:raise TransportError('No clock exchange yet')
        # Lowest delay; on ties the most recent exchange.
        return min(reversed(samples),key=lambda sample:sample[0])[1]
    def anchor(self,now=None):
        self.applied=self.target();self.applied_at=time.monotonic() if now is None else now;return self.applied
    def error(self):
        """Target minus applied offset; above ``jump`` means the mapping can no longer be slewed honestly."""
        return 0. if self.applied is None else self.target()-self.applied
    def update(self,now=None):
        now=time.monotonic() if now is None else now
        if self.applied is None:return self.anchor(now)
        step=self.slew*max(now-self.applied_at,0.);error=self.target()-self.applied
        self.applied+=max(-step,min(step,error));self.applied_at=now;return self.applied
    def to_host(self,node_time):
        if self.applied is None:raise TransportError('Clock mapping is not anchored')
        return float(node_time)-self.applied
    def status(self):
        samples=tuple(self.samples)
        best=min(reversed(samples),key=lambda sample:sample[0]) if samples else (None,None)
        offsets=[o for _,o in samples]
        return {'offset_seconds':self.applied,'target_offset_seconds':best[1],'best_delay_seconds':best[0],
                'error_bound_seconds':None if best[0] is None else best[0]/2,'residual_seconds':self.error() if samples else None,
                'jitter_seconds':float(np.std(offsets)) if len(offsets)>1 else None,'exchanges':self.count,
                'utc_offset_seconds':self.utc,
                'utc_offset_note':'node UTC minus host UTC via the ping exchange; near zero only when both hosts are time-synchronised (chrony)'}


_PERF_TO_MONO=None


def _calibrate_perf_to_mono():
    """Offset from perf_counter to monotonic, taken at a monotonic tick edge (sub-millisecond)."""
    start=time.monotonic();deadline=time.perf_counter()+.1
    while time.perf_counter()<deadline:
        value=time.monotonic()
        if value!=start:return value-time.perf_counter()
    return time.monotonic()-time.perf_counter()


def host_monotonic():
    """This host's monotonic clock at high resolution.

    Python 3.12 on Windows implements ``time.monotonic`` with GetTickCount64
    (15.6 ms steps), which would quantise the ping exchange. There the
    QueryPerformanceCounter-based ``perf_counter`` is shifted onto the monotonic
    epoch once; elsewhere ``time.monotonic`` is already fine grained.
    """
    global _PERF_TO_MONO
    if time.get_clock_info('monotonic').resolution<=1e-4:return time.monotonic()
    if _PERF_TO_MONO is None:_PERF_TO_MONO=_calibrate_perf_to_mono()
    return time.perf_counter()+_PERF_TO_MONO


def host_clock_alignment():
    """Current difference between ``host_monotonic`` and ``time.monotonic`` (bounded by the monotonic step)."""
    return host_monotonic()-time.monotonic()


def utc_minus_mono():return time.time()-time.monotonic()


# Payload codecs. Depth is always lossless; colour is lossless unless JPEG is chosen.
def encode_depth(raw,codec,level=1):
    raw=np.ascontiguousarray(raw,dtype='<u2')
    if raw.ndim!=2:raise ValueError('Depth must be a 2-D uint16 image')
    if codec=='raw':return raw.tobytes()
    if codec=='zlib':
        # Byte planes (high bytes compress far better on their own) then deflate.
        planes=raw.view(np.uint8).reshape(raw.shape[0],raw.shape[1],2).transpose(2,0,1)
        return zlib.compress(np.ascontiguousarray(planes).tobytes(),level)
    if codec=='png':
        import cv2
        ok,data=cv2.imencode('.png',raw.astype(np.uint16),[cv2.IMWRITE_PNG_COMPRESSION,int(level)])
        if not ok:raise ValueError('PNG depth encoding failed')
        return data.tobytes()
    raise ValueError(f'Unknown depth codec {codec}; choose from {DEPTH_CODECS}')


def decode_depth(data,codec,shape):
    height,width=(int(v) for v in shape);count=height*width
    if codec=='raw':
        if len(data)!=count*2:raise TransportError('Raw depth size mismatch')
        return np.frombuffer(data,'<u2').reshape(height,width).astype(np.uint16)
    if codec=='zlib':
        plain=zlib.decompressobj();body=plain.decompress(data,count*2+1)
        if len(body)!=count*2 or plain.unconsumed_tail or not plain.eof:raise TransportError('zlib depth size mismatch')
        planes=np.frombuffer(body,np.uint8).reshape(2,height,width)
        return np.ascontiguousarray(planes.transpose(1,2,0)).view('<u2').reshape(height,width).astype(np.uint16)
    if codec=='png':
        import cv2
        image=cv2.imdecode(np.frombuffer(data,np.uint8),cv2.IMREAD_UNCHANGED)
        if image is None or image.dtype!=np.uint16 or image.shape!=(height,width):raise TransportError('PNG depth decode failed')
        return image
    raise TransportError(f'Unknown depth codec {codec}')


def encode_color(rgb,codec,quality=90):
    import cv2
    rgb=np.ascontiguousarray(rgb,dtype=np.uint8)
    if codec=='raw':return rgb.tobytes()
    flag=[cv2.IMWRITE_JPEG_QUALITY,int(quality)] if codec=='jpeg' else [cv2.IMWRITE_PNG_COMPRESSION,1]
    ok,data=cv2.imencode('.jpg' if codec=='jpeg' else '.png',cv2.cvtColor(rgb,cv2.COLOR_RGB2BGR),flag)
    if not ok:raise ValueError(f'{codec} colour encoding failed')
    return data.tobytes()


def decode_color(data,codec,shape,device_format=None):
    import cv2
    height,width=(int(v) for v in shape)
    if codec=='device':
        from .gemini_333l_driver import decode_color as device_decode
        return device_decode(np.frombuffer(data,np.uint8),width,height,device_format)
    if codec=='raw':
        if len(data)!=height*width*3:raise TransportError('Raw colour size mismatch')
        return np.frombuffer(data,np.uint8).reshape(height,width,3).copy()
    if codec in ('png','jpeg'):
        image=cv2.imdecode(np.frombuffer(data,np.uint8),cv2.IMREAD_COLOR)
        if image is None or image.shape[:2]!=(height,width):raise TransportError(f'{codec} colour decode failed')
        return cv2.cvtColor(image,cv2.COLOR_BGR2RGB)
    raise TransportError(f'Unknown colour codec {codec}')


def file_identity(path):
    data=Path(path).read_bytes();return {'path_name':Path(path).name,'sha256':hashlib.sha256(data).hexdigest(),'bytes':len(data)}


def connect(host,port,timeout=5.):
    sock=socket.create_connection((host,int(port)),timeout=timeout)
    sock.setsockopt(socket.IPPROTO_TCP,socket.TCP_NODELAY,1)
    return sock
