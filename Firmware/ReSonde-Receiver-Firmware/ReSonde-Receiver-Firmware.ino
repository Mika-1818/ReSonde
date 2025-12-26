#include <RadioLib.h>


HardwareSerial SerialDebug(PB7, PB6);

STM32WLx radio = new STM32WLx_Module();


static const uint32_t rfswitch_pins[] = { RADIOLIB_NC, RADIOLIB_NC, RADIOLIB_NC, RADIOLIB_NC, RADIOLIB_NC };
static const Module::RfSwitchMode_t rfswitch_table[] = {
  { STM32WLx::MODE_IDLE, { LOW, LOW, LOW } },
  { STM32WLx::MODE_RX, { HIGH, HIGH, LOW } },
  { STM32WLx::MODE_TX_LP, { HIGH, HIGH, HIGH } },
  { STM32WLx::MODE_TX_HP, { HIGH, LOW, HIGH } },
  END_OF_MODE_TABLE,
};

volatile bool receivedFlag = false;

void setFlag(void) {
  receivedFlag = true;
}


struct __attribute__((packed)) Packet {
  char SerialNumber[11] = "NaN";
  uint16_t PacketNumber = 0;
  uint8_t hour = 0;
  uint8_t minute = 0;
  uint8_t second = 0;
  int32_t lat = 0;
  int32_t lon = 0;
  int32_t alt = 0;
  int32_t vSpeed = 0;
  int32_t eSpeed = 0;
  int32_t nSpeed = 0;
  uint8_t sats = 0;
  int16_t temp = 0;
  uint8_t rh = 0;
  uint8_t battery = 0;
} packet;

void printPacket() {
  SerialDebug.print(packet.SerialNumber);
  SerialDebug.print(",");
  SerialDebug.print(packet.PacketNumber);
  SerialDebug.print(",");
  SerialDebug.print(packet.hour);
  SerialDebug.print(",");
  SerialDebug.print(packet.minute);
  SerialDebug.print(",");
  SerialDebug.print(packet.second);
  SerialDebug.print(",");
  SerialDebug.print(packet.lat);
  SerialDebug.print(",");
  SerialDebug.print(packet.lon);
  SerialDebug.print(",");
  SerialDebug.print(packet.alt);
  SerialDebug.print(",");
  SerialDebug.print(packet.vSpeed);
  SerialDebug.print(",");
  SerialDebug.print(packet.eSpeed);
  SerialDebug.print(",");
  SerialDebug.print(packet.nSpeed);
  SerialDebug.print(",");
  SerialDebug.print(packet.sats);
  SerialDebug.print(",");
  SerialDebug.print(packet.temp);
  SerialDebug.print(",");
  SerialDebug.print(packet.rh);
  SerialDebug.print(",");
  SerialDebug.print(packet.battery);
  SerialDebug.println();
}

void setup() {
  SerialDebug.begin(115200);
  //SerialDebug.println("ReSonde Receiver");

  //SerialDebug.println("Now setting up Radio");
  radio.setRfSwitchTable(rfswitch_pins, rfswitch_table);
  int state = radio.begin(434.0, 62.5, 9, 5, RADIOLIB_SX126X_SYNC_WORD_PRIVATE, 10, 8, 3.3, false);
  if (state == RADIOLIB_ERR_NONE) {
    //SerialDebug.println(F("success!"));
  } else {
    //SerialDebug.print(F("failed, code "));
    //SerialDebug.println(state);
    while (true) { delay(10); }
  }

  radio.setDio1Action(setFlag);

  //SerialDebug.println("Starting to receive");
  state = radio.startReceive();
  if (state == RADIOLIB_ERR_NONE) {
    //SerialDebug.println(F("success!"));
  } else {
    //SerialDebug.print(F("failed, code "));
    //SerialDebug.println(state);
    while (true) { delay(10); }
  }
}

void loop() {
  if (receivedFlag) {
    receivedFlag = false;
    int state = radio.readData((uint8_t*)&packet, sizeof(packet));

    if (state == RADIOLIB_ERR_NONE) {
      //SerialDebug.println("Received Packet!");
      printPacket();
    } else if (state == RADIOLIB_ERR_CRC_MISMATCH) {
      SerialDebug.println("CRC Error!");
    } else {
      //Serial.print(F("failed, code "));
      //Serial.println(state);
    }
  }
}
