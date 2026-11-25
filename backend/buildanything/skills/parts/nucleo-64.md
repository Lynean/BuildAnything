---
match: nucleo
---
# Nucleo-64 boards

- B1 (the blue user button) is on PC13. It's held HIGH by an external pull-up and reads LOW when pressed, so use `INPUT`, not a pull-down.
- LD2 (the green user LED) is on PA5, which is shared with SPI SCK on Arduino pin D13. If SPI uses PA5, LD2 flickers with the clock.
- The ST-LINK's USB serial port is USART2 on PA2/PA3, which is the predefined `Serial`.
- The board runs at 3.3 V. Only some pins are 5 V tolerant, so check the datasheet before connecting 5 V signals.
