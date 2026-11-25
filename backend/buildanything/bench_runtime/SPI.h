#pragma once
#include "Arduino.h"

#define SPI_MODE0 0x00
#define SPI_MODE1 0x01
#define SPI_MODE2 0x02
#define SPI_MODE3 0x03
#define SPI_CLOCK_DIV2 2
#define SPI_CLOCK_DIV4 4
#define SPI_CLOCK_DIV8 8
#define SPI_CLOCK_DIV16 16
#define SPI_CLOCK_DIV32 32
#define SPI_CLOCK_DIV64 64
#define SPI_CLOCK_DIV128 128
#define SPI_HAS_TRANSACTION 1

class SPISettings {
 public:
  SPISettings() : clock(4000000), bit_order(MSBFIRST), mode(SPI_MODE0) {}
  SPISettings(uint32_t clock, uint8_t bit_order, uint8_t mode) : clock(clock), bit_order(bit_order), mode(mode) {}
  uint32_t clock;
  uint8_t bit_order;
  uint8_t mode;
};

class SPIClass {
 public:
  SPIClass(int bus = 0) : bus_(bus) {}
  SPIClass(uint32_t mosi, uint32_t miso, uint32_t sclk, uint32_t ssel = 0) : bus_(0) { (void)mosi; (void)miso; (void)sclk; (void)ssel; }
  void begin();
  void begin(int8_t sck, int8_t miso = -1, int8_t mosi = -1, int8_t ss = -1) { (void)sck; (void)miso; (void)mosi; (void)ss; begin(); }
  void end();
  void beginTransaction(SPISettings settings);
  void endTransaction();
  uint8_t transfer(uint8_t data);
  uint16_t transfer16(uint16_t data);
  void transfer(void* buffer, size_t count);
  void transfer(const void* tx, void* rx, size_t count);
  void transferBytes(const uint8_t* tx, uint8_t* rx, uint32_t count) { transfer(tx, rx, count); }
  void writeBytes(const uint8_t* data, uint32_t size) { transfer(data, nullptr, size); }
  void write(uint8_t data) { transfer(data); }
  void write16(uint16_t data) { transfer16(data); }
  void setClockDivider(uint32_t divider);
  void setDataMode(uint8_t mode);
  void setBitOrder(uint8_t order);
  void setFrequency(uint32_t hz);
  void usingInterrupt(int) {}
  // STM32 core pin selection
  void setMOSI(uint32_t) {}
  void setMISO(uint32_t) {}
  void setSCLK(uint32_t) {}
  void setSSEL(uint32_t) {}
 private:
  int bus_;
};
extern SPIClass SPI;
