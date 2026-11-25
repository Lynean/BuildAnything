---
match: st7735|1\.8.?(?:inch|in|\").{0,20}tft|128\s*[x×]\s*160
---
# ST7735 1.8 inch TFT, 128x160

- Use the Adafruit ST7735 and ST7789 library with Adafruit GFX, created inside a TFT driver.
- Start with `initR(INITR_BLACKTAB)`. If the colours are inverted or there's a stripe of noise at one edge, the panel is a different tab variant: try `INITR_GREENTAB` or `INITR_REDTAB`.
- On STM32 the library includes `wiring_private.h`, which the core lacks. The app adds a stand-in automatically.
- Hardware SPI is much faster than bit-banging, so set the SPI pins and pass `&SPI` to the constructor where the board allows it.
- The backlight pin (LED or BL) is on when HIGH on most of these boards. Switch it on after `initR`.
