#pragma once
#include "Arduino.h"

class Servo {
 public:
  uint8_t attach(int pin) { return attach(pin, 544, 2400); }
  uint8_t attach(int pin, int min_us, int max_us);
  void detach();
  void write(int value);
  void writeMicroseconds(int us);
  int read() const { return angle_; }
  int readMicroseconds() const { return us_; }
  bool attached() const { return pin_ >= 0; }
 private:
  int pin_ = -1, min_ = 544, max_ = 2400, us_ = 1500, angle_ = 90;
};
