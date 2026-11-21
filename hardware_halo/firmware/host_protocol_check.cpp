/* Desktop executable tests the exact firmware header, without Arduino or GPIO. */
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include "aether_halo/halo_protocol.h"
/* --receive "DELAY_MS:HEX DELAY_MS:HEX ...": feeds the firmware receiver with simulated time and
 * prints the accepted HELLOs, packet sequences and rejection counters (sequence filtering is the caller's). */
static int receive(const char* script) {
  HaloReceiver receiver;haloReceiverInit(receiver);bool paired=false;uint32_t now=0;unsigned hellos=0;
  printf("{\"sequences\":[");bool first=true;
  const char* p=script;
  while(*p) {
    while(*p==' ')p++;
    if(!*p)break;
    char* end;unsigned long delay=strtoul(p,&end,10);if(*end!=':')return 1;p=end+1;now+=(uint32_t)delay;
    while(*p&&*p!=' ') {
      if(!p[1])return 1;
      char text[3]={p[0],p[1],0};uint8_t byte=(uint8_t)strtoul(text,0,16);p+=2;
      HaloPacket packet;int event=haloReceive(receiver,byte,now,paired,packet);
      if(event==HALO_EVENT_HELLO){paired=true;hellos++;}
      else if(event==HALO_EVENT_PACKET){printf("%s%u",first?"":",",packet.sequence);first=false;}
    }
  }
  printf("],\"hello\":%u,\"rejected\":%u,\"timeouts\":%u,\"rescans\":%u}\n",hellos,(unsigned)receiver.rejected,(unsigned)receiver.timeouts,(unsigned)receiver.rescans);
  return 0;
}
int main(int argc,char** argv) {
  if(argc==3&&strcmp(argv[1],"--receive")==0)return receive(argv[2]);
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
