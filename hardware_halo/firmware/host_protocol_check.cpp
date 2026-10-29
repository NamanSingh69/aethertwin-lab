/* Desktop executable tests the exact firmware header, without Arduino or GPIO. */
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include "aether_halo/halo_protocol.h"
int main(int argc,char** argv) {
  if((argc!=2&&argc!=3)||strlen(argv[1])!=32)return 1;
  uint8_t bytes[16];
  for(unsigned i=0;i<16;i++){char text[3]={argv[1][2*i],argv[1][2*i+1],0};bytes[i]=(uint8_t)strtoul(text,0,16);}
  HaloPacket packet;if(!haloDecode(bytes,packet))return 2;
  uint8_t colors[24][3];haloColors(packet,argc==3,colors);
  printf("{\"sequence\":%u,\"azimuth\":%d,\"crc_reference\":%u,\"wrap\":%s,\"reverse\":%s,\"pixels\":[",packet.sequence,packet.azimuth,
    haloCrc((const uint8_t*)"123456789",9),haloNewer(0,65535)?"true":"false",haloNewer(65535,0)?"true":"false");
  for(unsigned i=0;i<24;i++)printf("%s[%u,%u,%u]",i?",":"",colors[i][0],colors[i][1],colors[i][2]);
  printf("]}\n");return 0;
}
