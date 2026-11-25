---
match: atmelavr|arduino uno|\buno\b|arduino nano|\bnano\b|mega2560|atmega
---
# Arduino AVR (Uno, Nano, Mega)

- Buttons: `pinMode(pin, INPUT_PULLUP)`, so they read LOW when pressed.
- Interrupts: `attachInterrupt` works only on pins 2 and 3 on an Uno. Other pins need pin-change interrupts.
- UART: one `Serial`, shared with USB. `SoftwareSerial` gives a second port at low speed.
- Memory: an Uno has 2 KB of RAM. No `String` in the loop, constant text in flash with `F("...")` or `PROGMEM`, and small buffers.
- `int` is 16-bit, so use `uint32_t` for `millis()` and any value above 32767.
- Storage: `EEPROM.update()`, which wears out with use.
- Logic is 5 V on the Uno, so level-shift for 3.3 V parts.
