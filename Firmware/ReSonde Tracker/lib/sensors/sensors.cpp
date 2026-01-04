#include "debug.h"
#include <Arduino.h>

// ---- temperature measurement ----- //

#include <Adafruit_MAX31865.h>

#define RREF 4020.0
#define RNOMINAL 1000.0

Adafruit_MAX31865 temp = Adafruit_MAX31865(PB8, PB5, PB4, PB3); // Initialise temperature IC with specific pins for ReSonde

void SetupTemperature()
{
  temp.begin(MAX31865_3WIRE); // Setup temperature IC for 3 wire RTD
}

int16_t getFormattedTemperature()
{
  temp.clearFault();
  float temperature = temp.temperature(RNOMINAL, RREF);
  uint8_t fault = temp.readFault();
  if (fault)
  {
    if (fault & MAX31865_FAULT_HIGHTHRESH)
      return (320);
    if (fault & MAX31865_FAULT_LOWTHRESH)
      return (-320);
    if (fault & MAX31865_FAULT_REFINLOW)
      return (480);
    if (fault & MAX31865_FAULT_REFINHIGH)
      return (-480);
    if (fault & MAX31865_FAULT_RTDINLOW)
      return (640);
    if (fault & MAX31865_FAULT_OVUV)
      return (-640);
  }
  else
  {
    return (round(temperature * 320.0f)); // Get temperature in celsius and convert for packet
  }
}

// ---- battery voltage measurement ----- //
int8_t getFormattedBattVoltage()
{
  return (map(analogRead(PB2), 0, 1024, 0, 255)); // Read battery voltage on PB2 and convert for packet
}

// ---- humidity measurement ----- //
// frequency measurement based on following example: https://github.com/stm32duino/STM32Examples/blob/main/examples/Peripherals/HardwareTimer/InputCapture/InputCapture.ino

uint32_t channel;
volatile uint32_t FrequencyMeasured, LastCapture = 0, CurrentCapture;
uint32_t input_freq = 0;
volatile uint32_t rolloverCompareCount = 0;
HardwareTimer *MyTim;

bool newFrequency = false;

void InputCapture_IT_callback(void)
{
  CurrentCapture = MyTim->getCaptureCompare(channel);
  /* frequency computation */
  if (CurrentCapture > LastCapture)
  {
    FrequencyMeasured = input_freq / (CurrentCapture - LastCapture);
  }
  else if (CurrentCapture <= LastCapture)
  {
    /* 0x1000 is max overflow value */
    FrequencyMeasured = input_freq / (0x10000 + CurrentCapture - LastCapture);
  }
  LastCapture = CurrentCapture;
  rolloverCompareCount = 0;
  newFrequency = true;
}

void Rollover_IT_callback(void)
{
  rolloverCompareCount++;

  if (rolloverCompareCount > 1)
  {
    FrequencyMeasured = 0;
  }
}

void pauseFrequencyMeasurement()
{ // disable frequency measurement when not needed to save power and cpu cycles
  if (MyTim == nullptr)
    return; // check if timer exists
  MyTim->detachInterrupt(channel);
  MyTim->detachInterrupt();
  MyTim->pause();
}

void enableFrequencyMeasurement()
{
  if (MyTim == nullptr)
    return; // check if timer exists
  MyTim->attachInterrupt(channel, InputCapture_IT_callback);
  MyTim->attachInterrupt(Rollover_IT_callback);
  MyTim->resume();
}

void SetupFrequencyMeasurement()
{
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

  pauseFrequencyMeasurement();
}

uint16_t sta = 100;    // number of samples to average
uint16_t timeout = 50; // max time it can take to get the number of samples in milliseconds

uint32_t getFrequency()
{
  unsigned long startMillis = millis();
  uint16_t samples = 0;
  uint32_t frequencyBuffer = 0;

  while (samples < sta and (millis() - startMillis) < timeout)
  {
    if (newFrequency)
    {
      samples++;
      newFrequency = false;
      frequencyBuffer += FrequencyMeasured;
    }
  }

  if (samples == sta)
  {
    return (round(frequencyBuffer / sta));
  }
  else
  {
    DEBUG_PRINTLN("Frequency measurement timeout");
    return 0; // timeout
  }
}

const float C_ref = 107e-12;   // capacity of reference capacitor in F including stray capacitance
const uint32_t R = 220e3;      // resistance of resistor in oscillator in ohms
const float stray_c = 10e-12;  // stray capacitance in F
const uint16_t stab_delay = 5; // delay to allow oscillator to stabilise in ms

const float C0 = 120;      // nominal sensor capacitance in pF
const float HC0 = 3420e-6; // nominal humidity coefficient of capacitance per %RH

//float K = 0.0f;       // calibration constant determined through calibration with reference C - Not used anymore!!!
float prev_RH = 0.0f; // previous relative humidity value

uint8_t getHumidityFormatted(int16_t temperature)
{

  enableFrequencyMeasurement();

  digitalWrite(PB12, LOW); // make sure the oscillator uses the reference capacitor
  delay(stab_delay);       // let the oscillator stabilise
  uint32_t f_cal = getFrequency();
  
  if (f_cal == 0)
  {
    pauseFrequencyMeasurement();
    return (255); // frequency measurement failed, return 255 to signal error
  }
 
  // calibration done, now measuring sensor

  digitalWrite(PB12, HIGH); // switch to sensor
  delay(stab_delay);        // let the oscillator stabilise
  uint32_t f_RH = getFrequency();       // get frequency with RH sensor

  float C_total_sensor = C_ref * ((float)f_cal / (float)f_RH);

  //float C_temp = (float)f_RH / (R * K); // calculate total capacity from frequency
  float C_RH = C_total_sensor - stray_c;        // subtract stray capacitance from total capacity to get sensor capacitance
  float C_RH_pF = C_RH * 1.0e12f;       // convert to pF

  // we now have the capacitance of the sensor in pF so we can convert it to relative humidity

  float dC = -0.0014f * (prev_RH) * ((temperature / 320.0f) - 30.0f); // temperature compensation based on last humidity value
  // now, we can calculate RH from the adjusted capacitance
  float RH = ((C_RH_pF - dC) - C0) / (C0 * HC0);

  digitalWrite(PB12, LOW);
  pauseFrequencyMeasurement();

  prev_RH = RH;
  
  if (RH < 0.0f)
  {
    return (0);
  }
  else if (RH > 125.0f)
  {
    return (252);
  }
  else
  {
    return (round(RH * 2.0));
  }
}