#include <Arduino.h>
#include <RadioLib.h>
#include "settings.h"
#include "debug.h"

STM32WLx radio = new STM32WLx_Module();

static const uint32_t rfswitch_pins[] = {RADIOLIB_NC, RADIOLIB_NC, RADIOLIB_NC, RADIOLIB_NC, RADIOLIB_NC};
static const Module::RfSwitchMode_t rfswitch_table[] = {
  {STM32WLx::MODE_IDLE,{}},
  {STM32WLx::MODE_RX,{}},
  {STM32WLx::MODE_TX_LP,{}},
  {STM32WLx::MODE_TX_HP,{}},
  END_OF_MODE_TABLE,
};

int transmissionState = RADIOLIB_ERR_NONE; // variable containing transmission state
volatile bool transmittedFlag = false; // flag set true when transmission finished

// function gets called when transmission finsihed
void setFlag(void) {
  transmittedFlag = true;
}

void finishTransmission() {
  if (transmissionState == RADIOLIB_ERR_NONE) {
    DEBUG_PRINTLN("Transmission successful!");
  } else {
    DEBUG_PRINTLN("Transmission failed, code: " + String(transmissionState));
  }

  radio.finishTransmit();
}

void SetupRadio() {
  // set imaginary rfswitch table as the ReSonde uses no RF switch
  radio.setRfSwitchTable(rfswitch_pins, rfswitch_table);

  // initialize radio
  int state = radio.begin(FREQ, BW, SF, CR, SW, TX_PWR, PL, 3.3, false);
  if(state != RADIOLIB_ERR_NONE) {
    DEBUG_PRINTLN("Radio init failed, code: " + String(state));
    while(true);
  }

  // set the function that will be called when transmission is finished
  radio.setDio1Action(setFlag);
}

extern struct __attribute__((packed)) Packet {
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
} packet;

void startTX() {
  // start transmission
  transmissionState = radio.startTransmit((uint8_t*)&packet, sizeof(packet));
  if (transmissionState == RADIOLIB_ERR_NONE) {
    DEBUG_PRINTLN("Transmission started...");    
  } else {
    DEBUG_PRINTLN("Transmission failed to start, code: " + String(transmissionState));
  }
}