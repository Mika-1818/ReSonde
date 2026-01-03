#include <Arduino.h>
#include "debug.h"
#include "settings.h"
#include "Radio.h"
#include <SparkFun_u-blox_GNSS_v3.h>
#include <SoftwareSerial.h>
#include "sensors.h"

SoftwareSerial SerialGNSS(PA3, PA2); // Serial for Max M10S
SFE_UBLOX_GNSS_SERIAL GNSS;

bool fullPacket = false;

struct __attribute__((packed)) Packet {
  uint16_t SN;
  uint16_t counter;
  uint32_t time;
  int32_t lat;
  int32_t lon;
  int32_t alt;
  int16_t vSpeed;
  int16_t eSpeed;
  int16_t nSpeed;
  uint8_t sats;
  int16_t temp;
  uint8_t rh;
  uint8_t battery;
} packet;                                   // Main packet to be transmitted

void panic(){
  while(true){
    DEBUG_PRINTLN("Fatal Error! Restarting in 1 Second...");
    delay(1000);
    NVIC_SystemReset();
  }
}

void initPacket(){
  packet.SN = SERIAL_NUMBER;
}

void fillPacket(){
  packet.counter++;
  DEBUG_PRINTLN("Filling GPS stuff... ");
  packet.time = GNSS.getUnixEpoch();
  packet.lat = GNSS.getLatitude();
  packet.lon = GNSS.getLongitude();
  packet.alt = GNSS.getAltitudeMSL();
  packet.vSpeed = round(GNSS.getNedDownVel() / -10);
  packet.eSpeed = round(GNSS.getNedEastVel() / 10); // Getting speed in cm/s
  packet.nSpeed = round(GNSS.getNedNorthVel() / 10);
  packet.sats = GNSS.getSIV();
  DEBUG_PRINTLN("Filling temperature");
  packet.temp = getFormattedTemperature(); // Get temperature from sensors library
  DEBUG_PRINTLN("Filling humidity");
  packet.rh = getHumidityFormatted(packet.temp); // Get humidity from sensors library and using previously determined temperature for compensation
  DEBUG_PRINTLN("Filling battery voltage");
  packet.battery = getFormattedBattVoltage(); // Get battery voltage from sensors library
  fullPacket = true;
}

void setup() {
  /*
  pinMode(PA10, OUTPUT);
  pinMode(PA11, OUTPUT);
  digitalWrite(PA10, HIGH);
  digitalWrite(PA11, HIGH); 
  */

  DEBUG_BEGIN(115200);

  // Setting up the Max M10S GNSS module
  DEBUG_PRINTLN("Attempting to start GNSS...");
  SerialGNSS.begin(9600);

  if (GNSS.begin(SerialGNSS)) {
    DEBUG_PRINTLN("GNSS started successfully!");
  } else {
    DEBUG_PRINTLN("GNSS failed to start. ReSonde cannot work without GNSS. Going into panic loop.");
    panic();
  }
  
  GNSS.setVal32(UBLOX_CFG_UART1_BAUDRATE, 38400, VAL_LAYER_RAM_BBR);
  GNSS.saveConfiguration();
  GNSS.end();

  SerialGNSS.flush();
  SerialGNSS.end();
  SerialGNSS.begin(38400);

  if (GNSS.begin(SerialGNSS)) {
    DEBUG_PRINTLN("GNSS started with higher baud rate successfully!");
  } else {
    DEBUG_PRINTLN("GNSS failed to start. ReSonde cannot work without GNSS. Going into panic loop.");
    panic();
  }

  GNSS.setUART1Output(COM_TYPE_UBX);
  GNSS.setNavigationFrequency(TX_RATE);
  GNSS.setAutoPVT(true);
  GNSS.setDynamicModel(DYN_MODEL_AIRBORNE1g);
  GNSS.saveConfiguration();

  SetupTemperature();
  SetupFrequencyMeasurement();
  // Setting up the STM32WL Radio
  SetupRadio();

  initPacket();
}

void loop() {
  if(transmittedFlag) {
    transmittedFlag = false;
    finishTransmission();
    DEBUG_PRINTLN("Transmission finished");
  }
  
  if (GNSS.getPVT()) {
    DEBUG_PRINTLN("Got a GNSS packet!");
    fillPacket();
  }

  if(fullPacket) {
    fullPacket = false;
    DEBUG_PRINTLN("Attempting to send packet...");
    startTX();
  }
}