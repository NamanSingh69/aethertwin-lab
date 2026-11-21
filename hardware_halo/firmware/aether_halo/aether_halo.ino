/* Aether Halo advisory display. ESP32 DevKit V1 / ESP32-WROOM-32, or ESP32-S3 DevKitC (S3 build).
 * No motors, relays, servo outputs, sensor configuration or firmware update API.
 * LED: GPIO18, WS2812B GRB,24 pixels. OLED optional: SSD1306 128x64 @0x3C,
 * SDA21/SCL22 (ESP32) or SDA8/SCL9 (ESP32-S3, which has no GPIO22). Host link on Serial: UART0 (the
 * DevKit's USB port, or the S3 board's "UART" port), or the S3's native USB port when built with
 * CDCOnBoot=cdc (build_firmware.py --board esp32s3-usb). Compiled for Arduino-ESP32 3.3.12.
 */
#include <Arduino.h>
#include <Wire.h>
#include "halo_protocol.h"
#define HALO_LED_GPIO 18
#if CONFIG_IDF_TARGET_ESP32S3
#define HALO_OLED_SDA 8
#define HALO_OLED_SCL 9
#define HALO_PROFILE "ESP32_S3_DEVKITC"
#else
#define HALO_OLED_SDA 21
#define HALO_OLED_SCL 22
#define HALO_PROFILE "ESP32_DEVKIT_V1"
#endif
#ifndef HALO_ENABLE_OLED
#define HALO_ENABLE_OLED 1
#endif
static const uint32_t LINK_WATCHDOG_MS=250;
static const uint32_t HEARTBEAT_MS=1000;
static const char* IDENTITY="AETHER_HALO 1 PIXELS=24 PROFILE=" HALO_PROFILE;
static bool paired=false,havePacket=false,ledReady=false,oledReady=false,linkExpired=false,ageSaturated=false,sequenceSession=false;
static uint32_t lastPacketMs=0,lastRenderMs=0,lastOledMs=0,lastHeartbeatMs=0,accepted=0;
static HaloPacket packet={HALO_DISCONNECTED,0,-32768,0,64,65535,0};
static HaloReceiver receiver;
static rmt_data_t symbols[24*24+1];
static uint8_t oled[1024];

static void oledCommand(uint8_t command) {
  Wire.beginTransmission(0x3C);Wire.write(0);Wire.write(command);Wire.endTransmission();
}
static void oledSetup() {
#if HALO_ENABLE_OLED
  // A missing or hung OLED must not stall the loop: bound every I2C transaction.
  Wire.begin(HALO_OLED_SDA,HALO_OLED_SCL);Wire.setClock(400000);Wire.setTimeOut(20);
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
static void acceptHello(uint32_t now) {
  if(!ledReady){Serial.println("AETHER_HALO_ERROR RMT_INIT");return;}
  Serial.println(IDENTITY);
  // A repeated HELLO starts a new sequence session; while packets are still fresh it keeps the
  // displayed state and link watchdog, so a host re-identifying does not flash the watchdog colour.
  bool fresh=paired&&havePacket&&!linkExpired&&(uint32_t)(now-lastPacketMs)<LINK_WATCHDOG_MS;
  paired=true;sequenceSession=false;
  if(!fresh){havePacket=false;linkExpired=true;ageSaturated=true;}
}
void setup() {
  Serial.begin(115200);haloReceiverInit(receiver);ledReady=rmtInit(HALO_LED_GPIO,RMT_TX_MODE,RMT_MEM_NUM_BLOCKS_1,10000000);
  oledSetup();showLeds(false);oledRender(false);
  enableLoopWDT();  // A hung loop resets the board (task watchdog, core default timeout).
}
void loop() {
  uint32_t now=millis();
  while(Serial.available()) {
    HaloPacket candidate;int event=haloReceive(receiver,(uint8_t)Serial.read(),millis(),paired,candidate);
    if(event==HALO_EVENT_HELLO)acceptHello(now);
    else if(event==HALO_EVENT_PACKET&&(!sequenceSession||!havePacket||haloNewer(candidate.sequence,packet.sequence))) {
      packet=candidate;havePacket=true;sequenceSession=true;lastPacketMs=now;linkExpired=false;ageSaturated=packet.age==65535;accepted++;
    }
  }
  if(paired&&(uint32_t)(now-lastHeartbeatMs)>=HEARTBEAT_MS) {
    // Lets the host notice a hung or reset board even while its writes still succeed.
    Serial.printf("AETHER_HALO_ALIVE %lu %lu %lu %lu\n",(unsigned long)now,(unsigned long)accepted,(unsigned long)receiver.rejected,(unsigned long)receiver.timeouts);
    lastHeartbeatMs=now;
  }
  if(paired&&(!havePacket||(uint32_t)(now-lastPacketMs)>=LINK_WATCHDOG_MS))linkExpired=true;
  bool watchdog=paired&&linkExpired; // Latch until a new valid packet, even across millis wrap.
  if((uint32_t)(now-lastRenderMs)>=33){showLeds(watchdog);lastRenderMs=now;}
  if((uint32_t)(now-lastOledMs)>=250){oledRender(watchdog);lastOledMs=now;}
  delay(1);
}
