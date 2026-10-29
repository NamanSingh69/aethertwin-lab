"""Aether Halo v1: exactly 16 bytes, explicit receiver identity, no actuation."""
import math,struct

MAGIC=b'\xA7\x54'
VERSION=1
PIXELS=24
HELLO=b'AETHER_HALO_HELLO 1\n'
IDENTITY=b'AETHER_HALO 1 PIXELS=24 PROFILE=ESP32_DEVKIT_V1'
SOURCES={'unspecified':0,'sim':1,'replay':2,'live':3,'test':4}
SOURCE_NAMES={value:key for key,value in SOURCES.items()}
SEVERITIES={'clear':0,'caution':1,'warning':2,'critical':3}
STALE=4;WATCHDOG=8;DIRECTION_VALID=16;DISCONNECTED=32


def crc16(data):
    """CRC-16/CCITT-FALSE, polynomial 0x1021, initial 0xFFFF, no reflection."""
    value=0xFFFF
    for byte in data:
        value^=byte<<8
        for _ in range(8):value=((value<<1)^0x1021)&0xFFFF if value&0x8000 else (value<<1)&0xFFFF
    return value


def sequence_newer(candidate,previous):
    delta=(int(candidate)-int(previous))&0xFFFF
    return 0<delta<0x8000


def encode_packet(state,sequence):
    severity=SEVERITIES[state['severity']]
    flags=severity|(STALE if state['stale'] else 0)|(WATCHDOG if state['watchdog'] else 0)|(DISCONNECTED if state['disconnected'] else 0)
    azimuth=state.get('azimuth_degrees')
    if azimuth is None:azimuth=-32768
    else:
        if not math.isfinite(azimuth):raise ValueError('Halo direction must be finite')
        flags|=DIRECTION_VALID;azimuth=int(round(((azimuth+180)%360-180)*100))
    age=state.get('source_age_ms');age=65535 if age is None else min(65535,max(0,int(round(age))))
    body=struct.pack('<2sBBHhBBHBB',MAGIC,VERSION,flags,int(sequence)&0xFFFF,azimuth,
        int(state['risk_scale']),int(state['brightness']),age,SOURCES.get(state['source'],0),PIXELS)
    return body+struct.pack('<H',crc16(body))


def decode_packet(packet):
    if len(packet)!=16:raise ValueError('Halo packet must be exactly 16 bytes')
    magic,version,flags,sequence,azimuth,risk,brightness,age,source,pixels=struct.unpack('<2sBBHhBBHBB',packet[:14])
    if magic!=MAGIC or version!=VERSION or pixels!=PIXELS or flags&0xC0 or source not in SOURCE_NAMES:
        raise ValueError('Unsupported Halo packet header')
    if crc16(packet[:14])!=struct.unpack('<H',packet[14:])[0]:raise ValueError('Halo CRC mismatch')
    valid=bool(flags&DIRECTION_VALID)
    if valid and not -18000<=azimuth<=18000:raise ValueError('Halo azimuth outside range')
    if not valid and azimuth!=-32768:raise ValueError('Invalid missing-direction marker')
    return {'sequence':sequence,'severity':tuple(SEVERITIES)[flags&3],'stale':bool(flags&STALE),
        'watchdog':bool(flags&WATCHDOG),'disconnected':bool(flags&DISCONNECTED),
        'azimuth_degrees':azimuth/100 if valid else None,'risk_scale':risk,'brightness':brightness,
        'source_age_ms':age,'source':SOURCE_NAMES[source],'pixels':pixels}


class ReceiverState:
    """Firmware-equivalent sequence/CRC/link watchdog, independently testable."""
    def __init__(self,watchdog_ms=250):
        self.watchdog_ms=watchdog_ms;self.last_sequence=None;self.last_received_ms=None;self.packet=None;self._expired=True
    def accept(self,packet,now_ms):
        state=decode_packet(packet)
        if self.last_sequence is not None and not sequence_newer(state['sequence'],self.last_sequence):return False
        self.packet=state;self.last_sequence=state['sequence'];self.last_received_ms=float(now_ms);self._expired=False;return True
    def status(self,now_ms):
        elapsed=None if self.last_received_ms is None else (now_ms-self.last_received_ms)%2**32
        if elapsed is None or elapsed>=self.watchdog_ms:self._expired=True
        return {'connected':self.last_received_ms is not None and not self._expired,'watchdog':self._expired,'packet':self.packet}
