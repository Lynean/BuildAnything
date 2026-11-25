---
match: efr32|silabs|silicon labs|xg2\d|thunderboard|mg2\d
---
# Silicon Labs EFR32 (Gecko SDK)

- Enable the clock first: `CMU_ClockEnable(cmuClock_GPIO, true)`.
- GPIO: `GPIO_PinModeSet(port, pin, gpioModePushPull, initial)`, `GPIO_PinOutSet` and `GPIO_PinOutClear`.
- UART:
  - `USART_InitAsync` with `USART_INITASYNC_DEFAULT`.
  - Route the pins through `GPIO->USARTROUTE[n]`.
  - Then call `NVIC_EnableIRQ` and `USART_IntEnable(..., USART_IEN_RXDATAV)`.
- Zigbee apps have no loop. The stack calls your callbacks, such as `emberAfMainInitCallback`, `emberAfHalButtonIsrCallback` and `emberAfStackStatusCallback`. Timing uses `EmberEventControl` with `emberEventControlSetDelayMS`, or `sl_sleeptimer`.
- Storage: tokens or NVM3.
