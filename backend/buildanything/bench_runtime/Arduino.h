/* A stand-in for the Arduino core, so the device's own firmware compiles and runs on the laptop bench. */
#pragma once
#include <stdint.h>
#include <stddef.h>
#include <stdlib.h>
#include <string.h>
#include <stdio.h>
#include <math.h>
#include <ctype.h>
#if __has_include("bench_board.h")
#include "bench_board.h"
#endif
#include "bench_pins.h"
#include "avr/pgmspace.h"

typedef uint8_t byte;
typedef bool boolean;
typedef uint16_t word;

#define HIGH 1
#define LOW 0
#define INPUT 0
#define OUTPUT 1
#define INPUT_PULLUP 2
#define INPUT_PULLDOWN 3
#define OUTPUT_OPEN_DRAIN 4
#define INPUT_ANALOG 5
#define CHANGE 2
#define FALLING 3
#define RISING 4
#ifdef __cplusplus
enum BitOrder { LSBFIRST = 0, MSBFIRST = 1 };
#else
#define LSBFIRST 0
#define MSBFIRST 1
#endif
#define SERIAL_8N1 0x06
#define SERIAL_8N2 0x0E
#define SERIAL_8E1 0x26
#define SERIAL_8O1 0x36
#define DEC 10
#define HEX 16
#define OCT 8
#define BIN 2
#ifndef PI
#define PI 3.1415926535897932384626433832795
#endif
#define HALF_PI 1.5707963267948966192313216916398
#define TWO_PI 6.283185307179586476925286766559
#define DEG_TO_RAD 0.017453292519943295769236907684886
#define RAD_TO_DEG 57.295779513082320876798154814105
#ifndef LED_BUILTIN
#define LED_BUILTIN 400
#endif
#define NUM_DIGITAL_PINS BENCH_PIN_COUNT

#define radians(deg) ((deg) * DEG_TO_RAD)
#define degrees(rad) ((rad) * RAD_TO_DEG)
#define bit(b) (1UL << (b))
#define bitRead(value, b) (((value) >> (b)) & 0x01)
#define bitSet(value, b) ((value) |= (1UL << (b)))
#define bitClear(value, b) ((value) &= ~(1UL << (b)))
#define bitWrite(value, b, v) ((v) ? bitSet(value, b) : bitClear(value, b))
#define lowByte(w) ((uint8_t)((w) & 0xff))
#define highByte(w) ((uint8_t)((w) >> 8))
#define digitalPinToInterrupt(p) (p)
#define digitalPinToPort(p) (p)
#define digitalPinToBitMask(p) (1u)
#define interrupts() bench_interrupts(true)
#define noInterrupts() bench_interrupts(false)
#define sei() bench_interrupts(true)
#define cli() bench_interrupts(false)
#define clockCyclesPerMicrosecond() 84
#define F_CPU 84000000UL

#ifdef __cplusplus
#include <algorithm>
template <class A, class B> inline auto min(A a, B b) -> decltype(a < b ? a : b) { return a < b ? a : b; }
template <class A, class B> inline auto max(A a, B b) -> decltype(a < b ? a : b) { return a > b ? a : b; }
template <class T, class L, class H> inline T constrain(T x, L lo, H hi) { return x < lo ? (T)lo : x > hi ? (T)hi : x; }
template <class T> inline auto sq(T x) -> decltype(x * x) { return x * x; }
inline long map(long x, long in_min, long in_max, long out_min, long out_max) { return in_max == in_min ? out_min : (x - in_min) * (out_max - out_min) / (in_max - in_min) + out_min; }
extern "C" {
#endif
void pinMode(uint32_t pin, uint32_t mode);
void digitalWrite(uint32_t pin, uint32_t value);
int digitalRead(uint32_t pin);
int analogRead(uint32_t pin);
void analogWrite(uint32_t pin, int value);
void analogReadResolution(int bits);
void analogWriteResolution(int bits);
void analogWriteFrequency(uint32_t hz);
unsigned long millis(void);
unsigned long micros(void);
void delay(unsigned long ms);
void delayMicroseconds(unsigned int us);
void attachInterrupt(uint32_t pin, void (*callback)(void), int mode);
void detachInterrupt(uint32_t pin);
void bench_interrupts(bool on);
void yield(void);
void tone(uint32_t pin, unsigned int frequency, unsigned long duration);
void noTone(uint32_t pin);
unsigned long pulseIn(uint32_t pin, uint32_t state, unsigned long timeout);
void shiftOut(uint32_t data_pin, uint32_t clock_pin, uint32_t bit_order, uint8_t value);
uint8_t shiftIn(uint32_t data_pin, uint32_t clock_pin, uint32_t bit_order);
#ifdef __cplusplus
}
long random(long howbig);
long random(long howsmall, long howbig);
void randomSeed(unsigned long seed);
#include <functional>
void attachInterrupt(uint32_t pin, std::function<void(void)> callback, int mode);

class __FlashStringHelper;
#define F(text) (reinterpret_cast<const __FlashStringHelper*>(PSTR(text)))
#include "WString.h"
#include "Print.h"

class Stream : public Print {
 public:
  virtual int available() = 0;
  virtual int read() = 0;
  virtual int peek() = 0;
  void setTimeout(unsigned long ms) { timeout_ = ms; }
  size_t readBytes(char* buffer, size_t length);
  size_t readBytes(uint8_t* buffer, size_t length) { return readBytes((char*)buffer, length); }
  size_t readBytesUntil(char terminator, char* buffer, size_t length);
  String readString();
  String readStringUntil(char terminator);
  long parseInt();
  float parseFloat();
  bool find(const char* target);
 protected:
  unsigned long timeout_ = 1000;
  int timed_read();
};

class HardwareSerial : public Stream {
 public:
  explicit HardwareSerial(int port) : port_(port) {}
  void begin(unsigned long baud, uint32_t config = SERIAL_8N1);
  void end();
  int available() override;
  int read() override;
  int peek() override;
  int availableForWrite() override;
  void flush() override;
  size_t write(uint8_t byte) override;
  using Print::write;
  operator bool() const { return true; }
  // STM32 core extras
  void setRx(uint32_t) {}
  void setTx(uint32_t) {}
  int port_;
};
extern HardwareSerial Serial;
extern HardwareSerial Serial1;
extern HardwareSerial Serial2;
extern HardwareSerial Serial3;
typedef HardwareSerial USBSerial;
#define SerialUSB Serial

void setup(void);
void loop(void);
#endif
