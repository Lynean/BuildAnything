#pragma once
#include "Arduino.h"

class TwoWire : public Stream {
 public:
  TwoWire(int bus = 0) : bus_(bus) {}
  void begin();
  void begin(uint8_t own_address) { (void)own_address; begin(); }
  void begin(int sda, int scl, uint32_t hz = 100000) { (void)sda; (void)scl; begin(); setClock(hz); }
  void end() {}
  void setClock(uint32_t hz) { hz_ = hz; }
  void setSDA(uint32_t) {}
  void setSCL(uint32_t) {}
  void beginTransmission(uint8_t address);
  void beginTransmission(int address) { beginTransmission((uint8_t)address); }
  uint8_t endTransmission(bool stop = true);
  uint8_t requestFrom(uint8_t address, size_t quantity, bool stop = true);
  uint8_t requestFrom(int address, int quantity) { return requestFrom((uint8_t)address, (size_t)quantity, true); }
  uint8_t requestFrom(int address, int quantity, int stop) { return requestFrom((uint8_t)address, (size_t)quantity, stop != 0); }
  size_t write(uint8_t byte) override;
  size_t write(const uint8_t* data, size_t length) override;
  using Print::write;
  int available() override;
  int read() override;
  int peek() override;
  void flush() override {}
  void setWireTimeout(uint32_t, bool = false) {}
  void onReceive(void (*)(int)) {}
  void onRequest(void (*)(void)) {}
 private:
  int bus_;
  uint32_t hz_ = 100000;
  uint8_t address_ = 0;
  uint8_t tx_[256];
  size_t tx_len_ = 0;
  bool transmitting_ = false;
  uint8_t rx_[256];
  size_t rx_len_ = 0, rx_pos_ = 0;
};
extern TwoWire Wire;
extern TwoWire Wire1;
