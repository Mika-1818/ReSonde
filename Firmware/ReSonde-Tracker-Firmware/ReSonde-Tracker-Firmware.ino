#include <SparkFun_u-blox_GNSS_v3.h>
#include <SoftwareSerial.h>
#include <RadioLib.h>
#include <Adafruit_MAX31865.h>
#include <math.h>
#include <HardwareTimer.h>

// defines for Temp measurement
#define RREF 4020.0
#define RNOMINAL 1000.0


// ReSonde Tracker Pins
#define LED_RED PA10
#define LED_GREEN PA11
#define CAL_SW PB12
#define RH_HEATER PA12
#define CH_A PC13
#define CH_B PA5
#define RH PA0

const float C0 = 120;

const int inputPin = PA0;
const int SAMPLES_TO_AVERAGE = 200;  // Average 200 pulses (~10ms at 20kHz)
const int SIGNAL_TIMEOUT_MS = 20;    // Return 0Hz if no signal for 50ms


volatile uint32_t g_totalTicks = 0;      // Accumulated ticks for N samples
volatile bool g_dataReady = false;       // Flag when a batch is ready
volatile uint32_t g_lastSignalTime = 0;  // To detect if signal died


HardwareSerial SerialDebug(PB7, PB6);
SoftwareSerial SerialGPS(PA3, PA2);
SFE_UBLOX_GNSS_SERIAL GNSS;

STM32WLx radio = new STM32WLx_Module();


Adafruit_MAX31865 temp = Adafruit_MAX31865(PB8, PB5, PB4, PB3);  //Initialise temperature IC

HardwareTimer *MyTim;
uint32_t channel;
uint32_t timerClockFreq = 0;

static const uint32_t rfswitch_pins[] = { RADIOLIB_NC, RADIOLIB_NC, RADIOLIB_NC, RADIOLIB_NC, RADIOLIB_NC };
static const Module::RfSwitchMode_t rfswitch_table[] = {
  { STM32WLx::MODE_IDLE, { LOW, LOW, LOW } },
  { STM32WLx::MODE_RX, { HIGH, HIGH, LOW } },
  { STM32WLx::MODE_TX_LP, { HIGH, HIGH, HIGH } },
  { STM32WLx::MODE_TX_HP, { HIGH, LOW, HIGH } },
  END_OF_MODE_TABLE,
};

struct __attribute__((packed)) Packet {
  char SerialNumber[11] = "DL0HAB-001";
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

//Gemini!
void InputCapture_Callback(void) {
  static uint32_t lastCapture = 0;
  static uint32_t currentSamples = 0;
  static uint32_t accumulator = 0;

  uint32_t now = MyTim->getCaptureCompare(channel);
  g_lastSignalTime = millis();  // Update "alive" timestamp

  // Calculate period (handles rollover automatically with unsigned math)
  uint32_t period = now - lastCapture;
  lastCapture = now;

  // Accumulate data
  accumulator += period;
  currentSamples++;

  // Once we have enough samples, push to global variable
  if (currentSamples >= SAMPLES_TO_AVERAGE) {
    g_totalTicks = accumulator;
    g_dataReady = true;

    // Reset for next batch
    accumulator = 0;
    currentSamples = 0;
  }
}


void printPacket() {
  SerialDebug.println("Now printing the whole packet!");
  SerialDebug.print("Serial Number: ");
  SerialDebug.println(packet.SerialNumber);
  SerialDebug.print("Packet Number: ");
  SerialDebug.println(packet.PacketNumber);
  SerialDebug.print("Hour: ");
  SerialDebug.println(packet.hour);
  SerialDebug.print("Minute: ");
  SerialDebug.println(packet.minute);
  SerialDebug.print("Second: ");
  SerialDebug.println(packet.second);
  SerialDebug.print("Latitude: ");
  SerialDebug.println(packet.lat);
  SerialDebug.print("Longitude: ");
  SerialDebug.println(packet.lon);
  SerialDebug.print("Altitude: ");
  SerialDebug.println(packet.alt);
  SerialDebug.print("Vertical Speed: ");
  SerialDebug.println(packet.vSpeed);
  SerialDebug.print("Speed Eastwards: ");
  SerialDebug.println(packet.eSpeed);
  SerialDebug.print("Speed Northwards: ");
  SerialDebug.println(packet.nSpeed);
  SerialDebug.print("Satellites: ");
  SerialDebug.println(packet.sats);
  SerialDebug.print("Temperature: ");
  SerialDebug.println(packet.temp);
  SerialDebug.print("Humidity: ");
  SerialDebug.println(packet.rh);
  SerialDebug.print("Battery: ");
  SerialDebug.println(packet.battery);
}

void setup() {

  pinMode(CAL_SW, OUTPUT);
  digitalWrite(CAL_SW, LOW);

  analogReadResolution(10);

  SerialDebug.begin(115200);
  SerialDebug.println("ReSonde Tracker");
  SerialGPS.begin(9600);
  SerialDebug.println("GPS Serial Port initialized!");
  while (GNSS.begin(SerialGPS) == false) {
    SerialDebug.println(F("u-blox GNSS not detected. Retrying..."));
    delay(1000);
  }

  GNSS.setUART1Output(COM_TYPE_UBX);
  SerialDebug.println("GPS initialized successfully!");

  SerialDebug.println("Now initilising temperature IC!");
  temp.begin(MAX31865_3WIRE);

  SerialDebug.println("Now setting up Radio");
  radio.setRfSwitchTable(rfswitch_pins, rfswitch_table);
  int state = radio.begin(434.0, 31.0, 5, 5, RADIOLIB_SX126X_SYNC_WORD_PRIVATE, 20, 8, 3.3, false);

  if (state == RADIOLIB_ERR_NONE) {
    SerialDebug.println(F("success!"));
  } else {
    SerialDebug.print(F("failed, code "));
    SerialDebug.println(state);
    while (true) { delay(10); }
  }

  SerialDebug.println("Now setting up frequency counter for RH sensor");
  TIM_TypeDef *Instance = (TIM_TypeDef *)pinmap_peripheral(digitalPinToPinName(inputPin), PinMap_PWM);
  channel = STM_PIN_CHANNEL(pinmap_function(digitalPinToPinName(inputPin), PinMap_PWM));

  MyTim = new HardwareTimer(Instance);
  MyTim->setMode(channel, TIMER_INPUT_CAPTURE_RISING, inputPin);

  MyTim->setPrescaleFactor(1);
  MyTim->setOverflow(0xFFFFFFFF);

  MyTim->attachInterrupt(channel, InputCapture_Callback);
  MyTim->resume();

  timerClockFreq = MyTim->getTimerClkFreq();

  //GNSS.setLNAMode(SFE_UBLOX_LNA_MODE_NORMAL);

  GNSS.setDynamicModel(DYN_MODEL_AIRBORNE1g, VAL_LAYER_RAM_BBR);  //very important for weather balloons! Will allow for over 18km or so altitude measurements
}

float getFrequency() {
  // 1. Check for timeout (Signal stopped?)
  if (millis() - g_lastSignalTime > SIGNAL_TIMEOUT_MS) {
    return 0.0;
  }

  // 2. Check if new data is available
  if (g_dataReady) {
    // Critical Section: Read volatile variables safely
    // (Optional: Pause interrupts briefly if you see glitches,
    // usually not needed for single 32-bit reads)
    uint32_t totalTicks = g_totalTicks;
    g_dataReady = false;

    // 3. Calculate Average Frequency
    // Formula: Freq = (Clock * Samples) / Total_Accumulated_Ticks
    if (totalTicks > 0) {
      return (float)((uint64_t)timerClockFreq * SAMPLES_TO_AVERAGE) / (float)totalTicks;
    }
  }

  // If no new data yet, return the last known calculation or -1
  // For this example, we return -1 to indicate "Waiting for batch"
  // or you could use a static variable to return the previous valid value.
  return -1.0;
}

/*
uint8_t readRH() {
  digitalWrite(CAL_SW, LOW);
  delay(10);
  float cal_K = 100e-12 * 220000 * getFrequency();
  digitalWrite(CAL_SW, HIGH);
  delay(20);
  float meas_C = cal_K / (220000 * getFrequency());
  digitalWrite(CAL_SW, LOW);
  //float rh = (meas_C - C0) / (C0 * 0.00342 - 0.0014e-12 * (packet.temp - 30.0));

  float rh = (meas_C - C0) / (((C0 * 0.00342f) - (0.0014e-12f * (packet.temp - 30.0f))) < 1e-15f ? 1e-15f : ((C0 * 0.00342f) - (0.0014e-12f * (packet.temp - 30.0f))));


  if (rh < 0) {
    rh = 0;
  } else if (rh > 127) {
    rh = 127.5;
  }

  return (round(rh * 2));
  //return (round(meas_C * 10e12));
}
*/

uint8_t readRH() {
  digitalWrite(CAL_SW, LOW);
  delay(10);
  
  // Assuming getFrequency() returns Hz and cal_K is a scaling constant
  float cal_K = 100e-12f * 220000.0f * getFrequency(); 
  
  digitalWrite(CAL_SW, HIGH);
  delay(20);
  
  // Calculate measured capacitance in Farads
  float meas_C_F = cal_K / (220000.0f * getFrequency());
  
  // Convert to pF for easier calculation (C0 is ~120 pF)
  float meas_C = meas_C_F * 1e12f; 
  digitalWrite(CAL_SW, LOW);

  // Constants from Datasheet
  const float HC0 = 0.00342f;        // 3420 ppm/%RH converted to ratio 
  const float T_ref = 30.0f;         // Reference temperature in C [cite: 16]
  const float T_coeff = 0.0014f;     // Temp dependence coefficient 
  
  // denominator = (C0 * sensitivity) - (temperature correction factor)
  float denominator = (C0 * HC0) - (T_coeff * ((packet.temp/320) - T_ref));

  // Prevent division by zero
  if (abs(denominator) < 1e-9f) denominator = 1e-9f;

  float rh = (meas_C - C0) / denominator;

  // Constraints and formatting
  if (rh < 0) {
    rh = 0;
  } else if (rh > 100) {
    rh = 100; // Standard RH max is 100% 
  }

  // Returns RH in 0.5% increments (e.g., 200 = 100%)
  return (uint8_t)(round(rh * 2));
}

void loop() {
  if (GNSS.getPVT() == true) {
    SerialDebug.println("Got GPS packet");

    packet.PacketNumber++;

    packet.hour = GNSS.getHour();
    packet.minute = GNSS.getMinute();
    packet.second = GNSS.getSecond();
    SerialDebug.println("Got time");

    packet.sats = GNSS.getSIV();  // Number of sattelites used

    packet.lat = GNSS.getLatitude();   // Latitude: degrees * 10^-7
    packet.lon = GNSS.getLongitude();  // Calculation like with Latitude

    packet.alt = GNSS.getAltitudeMSL();  // Altitude above Mean Sea Level in mm
    SerialDebug.println("Got position");

    packet.vSpeed = GNSS.getNedDownVel();  // speed in mm/s
    packet.eSpeed = GNSS.getNedEastVel();
    packet.nSpeed = GNSS.getNedNorthVel();
    SerialDebug.println("Got speed");

    packet.temp = round(temp.temperature(RNOMINAL, RREF) * 320.0);

    uint8_t fault = temp.readFault();
    if (fault) {
      SerialDebug.print("Fault 0x");
      SerialDebug.println(fault, HEX);
      if (fault & MAX31865_FAULT_HIGHTHRESH) SerialDebug.println("RTD High Threshold");
      if (fault & MAX31865_FAULT_LOWTHRESH) SerialDebug.println("RTD Low Threshold");
      if (fault & MAX31865_FAULT_REFINLOW) SerialDebug.println("REFIN- > 0.85 x Bias");
      if (fault & MAX31865_FAULT_REFINHIGH) SerialDebug.println("REFIN- < 0.85 x Bias - FORCE- open");
      if (fault & MAX31865_FAULT_RTDINLOW) SerialDebug.println("RTDIN- < 0.85 x Bias - FORCE- open");
      if (fault & MAX31865_FAULT_OVUV) SerialDebug.println("Under/Over voltage");
      temp.clearFault();
      packet.temp = 1234;
    }
    SerialDebug.println("Got temp");

    packet.rh = readRH();
    packet.battery = round((analogRead(PB2) / 1241.2f) * 417.0f);
    SerialDebug.println("Got RH");

    //SerialDebug.println(getFrequency());

    printPacket();

    //if (packet.sats > 4) {
    SerialDebug.println("Attempting to transmit packet!");

    int state = radio.transmit((uint8_t *)&packet, sizeof(packet));  //(uint8_t*)&packet, sizeof(packet)

    if (state == RADIOLIB_ERR_NONE) {
      SerialDebug.println(F("Packet transmitted successfully!"));
    } else {
      SerialDebug.print(F("transmitting packet failed, code "));
      SerialDebug.println(state);
    }
    //}
  }
}
