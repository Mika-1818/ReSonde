#pragma once
#include <Arduino.h>

#ifdef DEBUG
    extern HardwareSerial SerialDebug;
    #define DEBUG_BEGIN(baud) SerialDebug.begin(baud); delay(300); SerialDebug.println("ReSonde starting with debugging!")
    #define DEBUG_PRINT(x)    SerialDebug.print(x)
    #define DEBUG_PRINTLN(x)  SerialDebug.println(x)
#else
    #define DEBUG_BEGIN(baud)
    #define DEBUG_PRINT(x)
    #define DEBUG_PRINTLN(x)
#endif