---
match: esp32|espressif
---
# ESP32 (Arduino-ESP32)

- GPIO: `pinMode` and `digitalWrite`.
  - Keep inputs off the boot strapping pins (0, 2, 5, 12, 15).
  - Never use 6 to 11, which are wired to the flash chip.
  - 34 to 39 are input only and have no pull-ups.
  - ADC2 doesn't work while Wi-Fi is on.
- Interrupts: `void IRAM_ATTR on_edge()` with `attachInterrupt`, kept tiny.
- UART: `Serial` is USB. Other ports set their pins at begin: `Serial1.begin(baud, SERIAL_8N1, rxPin, txPin)`.
- SPI and I2C: `SPI.begin(sck, miso, mosi, ss)` and `Wire.begin(sda, scl)`.
- PWM: `ledcAttach` and `ledcWrite` on core 3.x, or `ledcSetup` and `ledcAttachPin` on 2.x.
- Time: `millis()`, and `esp_timer` for microseconds. FreeRTOS tasks with a queue can replace the event ring buffer. Long loops must yield, with `delay(1)` or `vTaskDelay`, or the watchdog resets the chip.
- Storage: `Preferences` (NVS).
- Logic is 3.3 V, and the pins aren't 5 V tolerant.
