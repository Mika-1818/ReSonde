#include <Arduino.h>
#include <SPI.h>
#include <Wire.h>
#include <Adafruit_GFX.h>
#include <Adafruit_SSD1306.h>
#include <RadioLib.h>

#include <WiFi.h>
#include <HTTPClient.h>


////// CHANGE THESE VALUES TO YOUR WIFI CREDENTIALS //////
#define SSID "Your_SSID"         // replace with your WiFi SSID
#define PASSWORD "Your_PASSWORD" // replace with your WiFi password


// OLED display definitions
#define SCREEN_WIDTH 128
#define SCREEN_HEIGHT 64
#define OLED_RESET -1
Adafruit_SSD1306 display(SCREEN_WIDTH, SCREEN_HEIGHT, &Wire, OLED_RESET);

#define LED 25 // LED pin

#define FREQUENCY           434.6   // MHz      LoRa settings. Leave like this to work with ReSonde
#define LORA_SPREADING_FACTOR 9
#define LORA_BANDWIDTH      62.5   // kHz
#define LORA_CODING_RATE    8       // 4/8
#define LORA_SYNC_WORD      0x12
#define TX_POWER            2      // tx power in dBm, neccesary but not relevant
#define LORA_PREAMBLE_LENGTH 8      // symbols

const char* serverUrl = "https://dashboard.resonde.de/api/upload";


struct __attribute__((packed)) Packet { // see printPacket() for field descriptions
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
} packet;                                   // Main packet to be received



SX1278 radio = new Module(18, 26, 23, -1); // LoRa(sx1278) module (CS, IRQ, RST, GPIO), works fine with ttgo V2
volatile bool receivedFlag = false; // goes true when a packet is received

ICACHE_RAM_ATTR
void setFlag(void) {
  // set received flag after packet has been received
  receivedFlag = true;
}

String convertTime(unsigned long in_time) { //function to convert unix time to string
  unsigned long secondsInDay = in_time % 86400UL;

  int hours   = secondsInDay / 3600;
  int minutes = (secondsInDay % 3600) / 60;
  int seconds = secondsInDay % 60;

  char buffer[9];
  snprintf(buffer, sizeof(buffer), "%02d:%02d:%02d", hours, minutes, seconds);
  return(buffer);
}


void updateDisplay() {
  // Function to update the OLED display with received data
  display.clearDisplay();
  display.setCursor(0,0);
  display.print("SN:"); display.print(packet.SN);
  display.print(" | "); display.println(packet.counter);
  display.print("Time: "); display.println(convertTime(packet.time));
  display.print(String((float)packet.lat * 1e-7, 6));
  display.print("  "); display.println(String((float)packet.lon * 1e-7, 6));
  display.print("Alt: "); display.print((int)round(packet.alt * 1e-3)); display.print("m");
  display.print(" S: "); display.println(packet.sats);
  display.print("Env: "); display.print(packet.temp / 320.0f); display.print("C");
  display.print(" | "); display.print(packet.rh * 0.5f); display.println("%");
  display.print("Batt: "); display.print((packet.battery * 3.3f) / 255.0f); display.println(" V");
  display.print("RSSI: "); display.print(radio.getRSSI()); display.println("dBm");
  if(WiFi.status() == WL_CONNECTED){
    display.println("WiFi connected!");
  } else {
    display.println("WiFi NOT connected!");
  }
  display.display();
}

void printPacket() {
  Serial.print(packet.SN); Serial.print(", "); // Serial Number
  Serial.print(packet.counter); Serial.print(", "); // Packet counter
  Serial.print(packet.time); Serial.print(", "); // Time in unix code
  Serial.print(packet.lat); Serial.print(", "); // Latitude, e-7 to get standard format
  Serial.print(packet.lon); Serial.print(", "); // Longitude, e-7 to get standard format
  Serial.print(packet.alt); Serial.print(", "); // Altitude, in mm
  Serial.print(packet.vSpeed); Serial.print(", "); // Vertical speed, in cm/s
  Serial.print(packet.eSpeed); Serial.print(", "); // East speed, in cm/s
  Serial.print(packet.nSpeed); Serial.print(", "); // North speed, in cm/s
  Serial.print(packet.sats); Serial.print(", "); // Number of satellites
  Serial.print(packet.temp); Serial.print(", "); // Temperature, in /320 to get C
  Serial.print(packet.rh); Serial.print(", "); // Relative humidity, /2 to get %
  Serial.print(packet.battery); Serial.print(", "); // battery voltage, (battery*3.3)/255 to get V
  Serial.println(radio.getRSSI());
}

void uploadTelemetry() {
    HTTPClient http;
    http.begin(serverUrl);
    http.addHeader("Content-Type", "application/json");
    
    // Build JSON payload matching the expected format
    String payload = "{";
    payload += "\"sn\":" + String(packet.SN) + ",";
    payload += "\"counter\":" + String(packet.counter) + ",";
    payload += "\"time\":" + String(packet.time) + ",";
    payload += "\"lat\":" + String(packet.lat) + ",";
    payload += "\"lon\":" + String(packet.lon) + ",";
    payload += "\"alt\":" + String(packet.alt) + ",";
    payload += "\"vSpeed\":" + String(packet.vSpeed) + ",";
    payload += "\"eSpeed\":" + String(packet.eSpeed) + ",";
    payload += "\"nSpeed\":" + String(packet.nSpeed) + ",";
    payload += "\"sats\":" + String(packet.sats) + ",";
    payload += "\"temp\":" + String(packet.temp) + ",";
    payload += "\"rh\":" + String(packet.rh) + ",";
    payload += "\"battery\":" + String(packet.battery) + ",";
    payload += "\"rssi\":" + String(radio.getRSSI());
    payload += "}";
    
    int httpCode = http.POST(payload);
    http.end();
}


void setup() {
  Serial.begin(115200);

  pinMode(LED, OUTPUT);

  // Setup for OLED
  Wire.begin(21,22);
  if(!display.begin(SSD1306_SWITCHCAPVCC, 0x3C)) {
    Serial.println(F("SSD1306 allocation failed"));
    while(true){ delay(100); };
  }
  display.clearDisplay();
  display.setTextSize(1);
  display.setTextColor(SSD1306_WHITE);
  display.setCursor(0,0);
  display.println("ReSonde Receiver");
  display.display();

  
  WiFi.begin(SSID, PASSWORD);
  Serial.print("Connecting to WiFi");

  unsigned long startAttemptTime = millis();
  const unsigned long wifiTimeout = 30000; // 30 seconds

  while (WiFi.status() != WL_CONNECTED && millis() - startAttemptTime < wifiTimeout) {
    delay(500);
    Serial.print(".");
  }

  if (WiFi.status() == WL_CONNECTED) {
    Serial.println(" connected!");
    display.println("WiFi connected!");
  } else {
    Serial.println(" failed!");
    display.println("WiFi NOT connected!");
  }


  // Setup for SX1278 LoRa

  SPI.begin(5,19,27,18); // SCK, MISO, MOSI, SS

  Serial.print(F("[SX1278] Initializing ... ")); // Initialize LoRa module
  int state = radio.begin(FREQUENCY, LORA_BANDWIDTH, LORA_SPREADING_FACTOR, LORA_CODING_RATE, LORA_SYNC_WORD, TX_POWER, LORA_PREAMBLE_LENGTH);
  if (state == RADIOLIB_ERR_NONE) {
    Serial.println(F("success!"));
  } else {
    Serial.print(F("failed, code "));
    Serial.println(state);
    while (true) { delay(10); }
  }

  radio.setPacketReceivedAction(setFlag); // set interrupt function when packet is received

  Serial.print(F("[SX1278] Starting to listen ... ")); //Set up radio to receive mode
  state = radio.startReceive();
  if (state == RADIOLIB_ERR_NONE) {
    Serial.println(F("success!"));
    display.println("Receiving!");
  } else {
    Serial.print(F("failed, code "));
    Serial.println(state);
    while (true) { delay(10); }
  }

  display.display();

}

void loop() {
  if (receivedFlag) {
    // reset flag
    receivedFlag = false;

    int state = radio.readData((uint8_t*)&packet, sizeof(packet)); // read data from receiver and put into packet struct

    if(state == RADIOLIB_ERR_NONE) {
      digitalWrite(LED, HIGH); // turning on LED to indicate packet was received
      updateDisplay(); // print data on OLED
      printPacket(); // print data on Serial port (USB)
      if(WiFi.status() == WL_CONNECTED){
        uploadTelemetry();
      }
      digitalWrite(LED, LOW); // turn off LED after processing and uploading the received packet
    }
  }
}