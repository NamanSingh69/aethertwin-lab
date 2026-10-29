#pragma once
#include <stdint.h>
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
