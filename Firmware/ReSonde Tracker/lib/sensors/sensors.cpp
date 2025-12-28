
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

// ---- humidity measurement ----- //
// frequency measurement based on following example: https://github.com/stm32duino/STM32Examples/blob/main/examples/Peripherals/HardwareTimer/InputCapture/InputCapture.ino

uint32_t channel;
volatile uint32_t FrequencyMeasured, LastCapture = 0, CurrentCapture;
uint32_t input_freq = 0;
volatile uint32_t rolloverCompareCount = 0;
HardwareTimer *MyTim;

void InputCapture_IT_callback(void)
{
  CurrentCapture = MyTim->getCaptureCompare(channel);
  /* frequency computation */
  if (CurrentCapture > LastCapture) {
    FrequencyMeasured = input_freq / (CurrentCapture - LastCapture);
  }
  else if (CurrentCapture <= LastCapture) {
    /* 0x1000 is max overflow value */
    FrequencyMeasured = input_freq / (0x10000 + CurrentCapture - LastCapture);
  }
  LastCapture = CurrentCapture;
  rolloverCompareCount = 0;
}

void Rollover_IT_callback(void)
{
  rolloverCompareCount++;

  if (rolloverCompareCount > 1)
  {
    FrequencyMeasured = 0;
  }

}

void SetupFrequencyMeasurement() {
  TIM_TypeDef *Instance = (TIM_TypeDef *)pinmap_peripheral(digitalPinToPinName(PA0), PinMap_PWM);
  channel = STM_PIN_CHANNEL(pinmap_function(digitalPinToPinName(PA0), PinMap_PWM));
  MyTim = new HardwareTimer(Instance);
  MyTim->setMode(channel, TIMER_INPUT_CAPTURE_RISING, PA0);
  uint32_t PrescalerFactor = 1;
  MyTim->setPrescaleFactor(PrescalerFactor);
  MyTim->setOverflow(0x10000);
  MyTim->attachInterrupt(channel, InputCapture_IT_callback);
  MyTim->attachInterrupt(Rollover_IT_callback);
  MyTim->resume();

  input_freq = MyTim->getTimerClkFreq() / MyTim->getPrescaleFactor();
}

uint32_t getFrequency() {
  return FrequencyMeasured;
}