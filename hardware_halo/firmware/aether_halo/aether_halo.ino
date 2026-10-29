/* Aether Halo advisory display. ESP32 DevKit V1 / ESP32-WROOM-32.
 * No motors, relays, servo outputs, sensor configuration or firmware update API.
 * LED: GPIO18, WS2812B GRB,24 pixels. OLED optional: SSD1306 128x64 @0x3C,
 * SDA21/SCL22. Compiled for Arduino-ESP32 3.3.12, generic esp32 board profile.
 */
#include <Arduino.h>
#include <Wire.h>
#include "halo_protocol.h"
#define HALO_LED_GPIO 18
#define HALO_OLED_SDA 21
#define HALO_OLED_SCL 22
#ifndef HALO_ENABLE_OLED
#define HALO_ENABLE_OLED 1
#endif
static const uint32_t LINK_WATCHDOG_MS=250;
static const char* IDENTITY="AETHER_HALO 1 PIXELS=24 PROFILE=ESP32_DEVKIT_V1";
static bool paired=false,havePacket=false,ledReady=false,oledReady=false,linkExpired=false,ageSaturated=false;
static uint32_t lastPacketMs=0,lastRenderMs=0,lastOledMs=0;
static HaloPacket packet={HALO_DISCONNECTED,0,-32768,0,64,65535,0};
static uint8_t receiveBytes[16];static unsigned receiveLength=0;
static char hello[64];static unsigned helloLength=0;
static rmt_data_t symbols[24*24+1];
static uint8_t oled[1024];

static void oledCommand(uint8_t command) {
  Wire.beginTransmission(0x3C);Wire.write(0);Wire.write(command);Wire.endTransmission();
}
static void oledSetup() {
#if HALO_ENABLE_OLED
  Wire.begin(HALO_OLED_SDA,HALO_OLED_SCL);Wire.setClock(400000);
  Wire.beginTransmission(0x3C);oledReady=Wire.endTransmission()==0;
  if(!oledReady)return;
  const uint8_t commands[]={0xAE,0xD5,0x80,0xA8,0x3F,0xD3,0,0x40,0x8D,0x14,0x20,0,0xA1,0xC8,0xDA,0x12,0x81,0x7F,0xD9,0xF1,0xDB,0x40,0xA4,0xA6,0xAF};
  for(unsigned i=0;i<sizeof(commands);i++)oledCommand(commands[i]);
#endif
}
static void oledText(unsigned row,const char* text) {
  // Small built-in 5x7 status alphabet; five vertical bitmap columns per glyph.
  static const char alphabet[]="0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ -.";
  static const uint8_t glyphs[][5]={
    {62,81,73,69,62},{0,66,127,64,0},{98,81,73,73,70},{34,65,73,73,54},{24,20,18,127,16},
    {39,69,69,69,57},{60,74,73,73,48},{1,113,9,5,3},{54,73,73,73,54},{6,73,73,41,30},
    {126,17,17,17,126},{127,73,73,73,54},{62,65,65,65,34},{127,65,65,34,28},{127,73,73,73,65},
    {127,9,9,9,1},{62,65,73,73,122},{127,8,8,8,127},{0,65,127,65,0},{32,64,65,63,1},
    {127,8,20,34,65},{127,64,64,64,64},{127,2,12,2,127},{127,4,8,16,127},{62,65,65,65,62},
    {127,9,9,9,6},{62,65,81,33,94},{127,9,25,41,70},{38,73,73,73,50},{1,1,127,1,1},
    {63,64,64,64,63},{31,32,64,32,31},{63,64,56,64,63},{99,20,8,20,99},{3,4,120,4,3},
    {97,81,73,69,67},{0,0,0,0,0},{8,8,8,8,8},{0,96,96,0,0}};
  for(unsigned column=0;text[column]&&column<21;column++) {
    const char* found=strchr(alphabet,text[column]);unsigned glyph=found?(unsigned)(found-alphabet):36;
    for(unsigned x=0;x<5;x++)oled[row*128+column*6+x]=glyphs[glyph][x];
  }
}
static void oledRender(bool watchdog) {
  if(!oledReady)return;
  memset(oled,0,sizeof(oled));oledText(0,"AETHER HALO");
  const char* sources[]={"UNSPECIFIED","SIMULATION","REPLAY","LIVE","TEST"};oledText(1,sources[packet.source]);
  const char* levels[]={"CLEAR","CAUTION","WARNING","CRITICAL"};
  oledText(2,!paired?"DISCONNECTED":((watchdog||(packet.flags&HALO_WATCHDOG))?"WATCHDOG":((packet.flags&HALO_DISCONNECTED)?"NO SOURCE":((packet.flags&HALO_STALE)?"STALE SOURCE":levels[packet.flags&3]))));
  uint32_t shownAge=(uint32_t)packet.age+(havePacket?(uint32_t)(millis()-lastPacketMs):0);
  if(shownAge>65535)ageSaturated=true;
  if(ageSaturated)shownAge=65535;
  char text[24];snprintf(text,sizeof(text),"AGE %u MS",(unsigned)shownAge);oledText(4,text);
  if(packet.flags&HALO_DIRECTION)snprintf(text,sizeof(text),"AZ %d DEG",packet.azimuth/100);else snprintf(text,sizeof(text),"AZ UNAVAILABLE");oledText(5,text);
  oledText(7,"NOT HW VALIDATED");
  oledCommand(0x21);oledCommand(0);oledCommand(127);oledCommand(0x22);oledCommand(0);oledCommand(7);
  for(unsigned start=0;start<sizeof(oled);start+=16){Wire.beginTransmission(0x3C);Wire.write(0x40);Wire.write(oled+start,16);Wire.endTransmission();}
}
static void showLeds(bool watchdog) {
  if(!ledReady)return;
  uint8_t colors[24][3];haloColors(packet,watchdog,colors);unsigned index=0;
  for(unsigned pixel=0;pixel<24;pixel++)for(unsigned c=0;c<3;c++) {
    uint8_t value=colors[pixel][c==0?1:(c==1?0:2)]; // WS2812B wire order GRB.
    for(int bit=7;bit>=0;bit--){bool one=value&(1<<bit);symbols[index].level0=1;symbols[index].duration0=one?8:4;symbols[index].level1=0;symbols[index].duration1=one?5:9;index++;}
  }
  symbols[index].level0=0;symbols[index].duration0=3000;symbols[index].level1=0;symbols[index].duration1=1;
  if(!rmtWrite(HALO_LED_GPIO,symbols,index+1,10))Serial.println("AETHER_HALO_ERROR RMT_WRITE");
}
static void acceptHello() {
  hello[helloLength]=0;
  if(strcmp(hello,"AETHER_HALO_HELLO 1")==0) {
    if(ledReady){Serial.println(IDENTITY);paired=true;havePacket=false;receiveLength=0;linkExpired=true;ageSaturated=true;}
    else Serial.println("AETHER_HALO_ERROR RMT_INIT");
  }
  helloLength=0;
}
void setup() {
  Serial.begin(115200);ledReady=rmtInit(HALO_LED_GPIO,RMT_TX_MODE,RMT_MEM_NUM_BLOCKS_1,10000000);
  oledSetup();showLeds(false);oledRender(false);
}
void loop() {
  uint32_t now=millis();
  while(Serial.available()) {
    uint8_t byte=(uint8_t)Serial.read();
    if(receiveLength==0&&byte!=0xA7) {
      if(byte=='\n')acceptHello();else if(byte!='\r'){
        if(byte>=32&&byte<127&&helloLength<sizeof(hello)-1)hello[helloLength++]=(char)byte;else helloLength=0;
      }
      continue;
    }
    if(!paired){receiveLength=0;continue;}
    if(receiveLength==1&&byte!=0x54){receiveLength=byte==0xA7?1:0;continue;}
    receiveBytes[receiveLength++]=byte;
    if(receiveLength==16) {
      HaloPacket candidate;
      if(haloDecode(receiveBytes,candidate)&&(!havePacket||haloNewer(candidate.sequence,packet.sequence))) {
        packet=candidate;havePacket=true;lastPacketMs=now;linkExpired=false;ageSaturated=packet.age==65535;
      }
      receiveLength=0;
    }
  }
  if(paired&&(!havePacket||(uint32_t)(now-lastPacketMs)>=LINK_WATCHDOG_MS))linkExpired=true;
  bool watchdog=paired&&linkExpired; // Latch until a new valid packet, even across millis wrap.
  if((uint32_t)(now-lastRenderMs)>=33){showLeds(watchdog);lastRenderMs=now;}
  if((uint32_t)(now-lastOledMs)>=250){oledRender(watchdog);lastOledMs=now;}
  delay(1);
}
