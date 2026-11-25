---
match: ststm32|\bstm32|nucleo|blackpill|bluepill
---
# STM32 (Arduino STM32duino)

- GPIO: `pinMode`, `digitalWrite`, `digitalRead`. With SPL, `GPIO_Init` with a filled `GPIO_InitTypeDef` after `RCC_AHB1PeriphClockCmd`. With HAL, `HAL_GPIO_Init`.
- Interrupts: `attachInterrupt(pin, isr, FALLING)`, which sets up EXTI and the NVIC.
- UART: the ST-LINK USB port is the predefined `Serial` (USART2 on PA2/PA3). Never construct a `HardwareSerial` for it. Other ports use `HardwareSerial Serial1(rxPin, txPin)`.
- SPI: `SPI.setMOSI()`, `SPI.setMISO()` and `SPI.setSCLK()` before `SPI.begin()`. I2C: `Wire.setSDA()` and `Wire.setSCL()`.
- Time: `millis()`, and `HardwareTimer` for periodic interrupts. Storage: the EEPROM emulation library.
