#pragma once
#include <stdint.h>
#include <string.h>
#define HALO_PIXELS 24
#define HALO_STALE 4
#define HALO_WATCHDOG 8
#define HALO_DIRECTION 16
#define HALO_DISCONNECTED 32

struct HaloPacket {
  uint8_t flags; uint16_t sequence; int16_t azimuth;
  uint8_t risk,brightness; uint16_t age; uint8_t source;
};
static uint16_t haloCrc(const uint8_t* bytes,unsigned length) {
  uint16_t crc=0xFFFF;
  for(unsigned i=0;i<length;i++) {
    crc^=(uint16_t)bytes[i]<<8;
    for(unsigned bit=0;bit<8;bit++)crc=(crc&0x8000)?(uint16_t)((crc<<1)^0x1021):(uint16_t)(crc<<1);
  }
  return crc;
}
static bool haloNewer(uint16_t value,uint16_t previous) {
  uint16_t delta=(uint16_t)(value-previous);return delta>0&&delta<0x8000;
}
static bool haloDecode(const uint8_t* bytes,HaloPacket& packet) {
  if(bytes[0]!=0xA7||bytes[1]!=0x54||bytes[2]!=1||bytes[13]!=24||(bytes[3]&0xC0)||bytes[12]>4)return false;
  uint16_t expected=(uint16_t)bytes[14]|((uint16_t)bytes[15]<<8);
  if(haloCrc(bytes,14)!=expected)return false;
  int16_t azimuth=(int16_t)((uint16_t)bytes[6]|((uint16_t)bytes[7]<<8));
  if(bytes[3]&HALO_DIRECTION) {if(azimuth < -18000||azimuth>18000)return false;}
  else if(azimuth!=-32768)return false;
  packet.flags=bytes[3];packet.sequence=(uint16_t)bytes[4]|((uint16_t)bytes[5]<<8);packet.azimuth=azimuth;
  packet.risk=bytes[8];packet.brightness=bytes[9];packet.age=(uint16_t)bytes[10]|((uint16_t)bytes[11]<<8);packet.source=bytes[12];return true;
}
/* Serial receive state machine shared by the firmware and the desktop cross-check.
 * Text before pairing is an ASCII HELLO line; binary packets start with A7 54.
 * A partial packet older than HALO_INTERBYTE_TIMEOUT_MS is discarded (a lost
 * byte can never glue two packets together), and a 16-byte block that fails
 * validation is rescanned from its next A7 instead of being dropped whole, so
 * one stray byte costs at most one packet. Sequence acceptance stays with the caller. */
#define HALO_INTERBYTE_TIMEOUT_MS 20
enum HaloEvent {HALO_EVENT_NONE=0,HALO_EVENT_HELLO=1,HALO_EVENT_PACKET=2};
struct HaloReceiver {
  uint8_t bytes[16];unsigned length;char hello[64];unsigned helloLength;uint32_t lastByteMs;
  uint32_t rejected,timeouts,rescans;
};
static void haloReceiverInit(HaloReceiver& r){memset(&r,0,sizeof(r));}
static int haloReceive(HaloReceiver& r,uint8_t byte,uint32_t nowMs,bool paired,HaloPacket& out) {
  if(r.length>0&&(uint32_t)(nowMs-r.lastByteMs)>HALO_INTERBYTE_TIMEOUT_MS){r.length=0;r.timeouts++;}
  r.lastByteMs=nowMs;
  if(r.length==0&&byte!=0xA7) {
    if(byte=='\n') {
      r.hello[r.helloLength]=0;bool hello=strcmp(r.hello,"AETHER_HALO_HELLO 1")==0;r.helloLength=0;
      return hello?HALO_EVENT_HELLO:HALO_EVENT_NONE;
    }
    if(byte!='\r'){
      if(byte>=32&&byte<127&&r.helloLength<sizeof(r.hello)-1)r.hello[r.helloLength++]=(char)byte;else r.helloLength=0;
    }
    return HALO_EVENT_NONE;
  }
  if(!paired){r.length=0;return HALO_EVENT_NONE;}
  if(r.length==1&&byte!=0x54){r.length=byte==0xA7?1:0;return HALO_EVENT_NONE;}
  r.bytes[r.length++]=byte;
  if(r.length<16)return HALO_EVENT_NONE;
  if(haloDecode(r.bytes,out)){r.length=0;return HALO_EVENT_PACKET;}
  r.rejected++;unsigned k=1;
  while(k<16&&!(r.bytes[k]==0xA7&&(k==15||r.bytes[k+1]==0x54)))k++;
  if(k<16)r.rescans++;
  memmove(r.bytes,r.bytes+k,16-k);r.length=16-k;return HALO_EVENT_NONE;
}
static void haloColors(const HaloPacket& packet,bool linkWatchdog,uint8_t colors[24][3]) {
  const uint8_t palette[4][3]={{0,180,100},{255,190,0},{255,70,0},{255,0,30}};
  uint8_t base[3];bool disconnected=(packet.flags&HALO_DISCONNECTED)!=0;
  bool watchdog=linkWatchdog||(packet.flags&HALO_WATCHDOG);bool stale=(packet.flags&HALO_STALE)!=0;
  for(unsigned c=0;c<3;c++)base[c]=palette[packet.flags&3][c];
  if(linkWatchdog){base[0]=255;base[1]=0;base[2]=190;}
  else if(disconnected){base[0]=20;base[1]=80;base[2]=255;}
  else if(watchdog){base[0]=255;base[1]=0;base[2]=190;}
  else if(stale){base[0]=255;base[1]=150;base[2]=0;}
  int center=-1;
  if(packet.flags&HALO_DIRECTION){int angle=packet.azimuth%36000;if(angle<0)angle+=36000;center=((angle*24+18000)/36000)%24;}
  for(int pixel=0;pixel<24;pixel++) {
    unsigned scale=64;
    if(disconnected||watchdog||stale)scale=(pixel%2)?38:255;
    else if(center>=0){int d=(pixel-center+24)%24;if(d>12)d=24-d;scale=d==0?255:(d==1?128:32);}
    if(!disconnected&&!watchdog&&!stale)scale=scale*(128+packet.risk/2)/255;
    for(unsigned c=0;c<3;c++)colors[pixel][c]=(uint8_t)((unsigned long)base[c]*scale*packet.brightness/(255UL*255UL));
  }
}
