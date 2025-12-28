
// ---- temperature measurement ----- //

#include <Adafruit_MAX31865.h>

#define RREF 4020.0
#define RNOMINAL 1000.0

Adafruit_MAX31865 temp = Adafruit_MAX31865(PB8, PB5, PB4, PB3);  //Initialise temperature IC with specific pins for ReSonde

void SetupTemperature() {
  temp.begin(MAX31865_3WIRE);  //Setup temperature IC for 3 wire RTD
}

int16_t getFormattedTemperature() {
  return(round(temp.temperature(RNOMINAL, RREF) * 320.0)); // Get temperature in celsius and convert for packet
}

// ---- battery voltage measurement ----- //
int8_t getFormattedBattVoltage() {
  return(map(analogRead(PC0), 0, 4096, 0, 255)); // Read battery voltage on PC0 and convert for packet
}