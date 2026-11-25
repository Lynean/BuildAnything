# Skill: FIRMWARE

For every microcontroller board. It follows the layered style of the person's STM32 and EFR32 training projects, and fixes the weak spots found in that code and in earlier runs.

## 1. Three layers

| Layer | Where | Its job | May include | Owner |
|---|---|---|---|---|
| DRIVER | `src/driver/<peripheral>.h/.cpp` | Turns a peripheral on and moves raw bits: clock enable, pin modes, peripheral settings, interrupt setup, raw read and write, the interrupt handler. Knows registers, HAL calls, Arduino pin calls and device libraries. Knows nothing about what the bytes mean. | Platform headers, device libraries, `ba_pins.h` | Electrical |
| MIDDLEWARE | `src/mid/<module>.h/.cpp` | Gives raw data meaning: debounced button events, a checked message, a colour, a temperature in °C, a screen widget, a clock. Hides the hardware, so the app never sees a pin, register or library object. | Driver headers, `ba_packets.h`, utilities | Electrical |
| APP | `src/app/<feature>.h/.cpp` and `src/main.cpp` | What the product does: the state machine, the game rules, which event causes what. | Mid headers and `ba_packets.h` types only | Software |

- Includes and calls only go down a layer. News comes back up through callbacks that the upper layer registers at init.
- Never skip a layer. The app calling `digitalRead`, or naming a `BA_PIN_`, is wrong.
- `BA_PIN_` names and raw pin calls (`pinMode`, `digitalWrite`, `digitalRead`, `analogRead`, `attachInterrupt`) appear only in `src/driver`.
- A device library, such as a display library, is created and owned inside a driver. The mid layer builds on the driver's functions.
- `src/mid/mid.h` includes every mid header, and `src/app/app.h` every app header, as umbrella headers.
- `main.cpp` only starts the app: `setup()` calls `App_Init()`, and `loop()` calls `App_Process(millis())`.

## 2. Plan before code

Fill `plan` before writing any file. Each module gets its layer, its name (which is also its file name), its purpose, the full C signature of every public function, the enums, structs and callback types it defines, the modules below it that it uses, and, for drivers, the `BA_PIN_` names it owns. Plan in this order:

1. **Pins.** Every agreed pin belongs to exactly one driver, including on-board parts such as a user button, an LED or the USB serial link.
2. **Drivers.** One per peripheral or device: `gpio`, `uart`, `spi`, `i2c`, `pwm`, `adc`, `tft`. They offer `X_Init(...)`, raw read and write, and a raw callback such as a byte received or an edge seen.
3. **Mid modules.** One per thing a person would name: `button`, `display`, `clock`, `link`, `led`. Define the event enums and data structs here. These headers are the contract with the app.
4. **App.** Map each requirement to the mid function or event that serves it. If a requirement has none, add it to a mid module now.
5. **Timing.** Say what runs every loop pass and at what period, and what runs in interrupts. Interrupts only capture data.

Write the mid headers first and keep them stable. Software writes the app against them, so changing a public mid signature later is a change request.

## 3. Patterns

**Callbacks, registered upward:**
```c
typedef void (*button_event_cb_t)(uint8_t button, button_event_t event);
void Button_Init(button_event_cb_t on_event);   /* the app passes App_OnButton */
```
The module keeps `static button_event_cb_t s_on_event;`, set in `Init`, and checks it for NULL before calling. The chain runs driver to mid to app: the UART driver delivers bytes to the link module, and the link module delivers whole messages to the app.

**Data types:**
- `typedef enum` for events, states and message IDs, such as `BUTTON_CLICK_1`, `APP_STATE_IDLE` and `MSG_SET_TIME`.
- Bit-flag enums for options that combine: `COLOR_RED = 1u << 0`, `COLOR_PINK = COLOR_RED | COLOR_BLUE`.
- Constant config tables, so one loop can set up identical devices: `static const led_pins_t LEDS[] = {...}`.
- One state struct per instance, such as `blink_t` with colour, remaining count, on time and off time.
- Fixed-width types only (`uint8_t`, `int16_t`, `uint32_t`), and `bool` from `<stdbool.h>`.
- For wire formats, use the `ba_packets.h` encode and decode functions. When parsing a link that isn't defined there, check the length first, then copy fields out with `memcpy` and handle byte order. Never cast a struct onto a buffer.

**Cooperative, non-blocking scheduling:**
- The loop calls each module's `X_Process(now_ms)` every pass. Nothing waits.
- Software timers use `Timer_Start(name, period_ms, repeats, callback, context)`. It returns an id, supports one-shot and repeating, and stops with `Timer_Stop(id)`. Use them for blinking, button timeouts and periodic sends.
- Event queue: a ring buffer (size, count, head, tail, item size). An interrupt adds to it, and the loop takes from it.
- App state machine: an enum plus a `switch` in `App_Process`, with long work split across states.
- Button middleware:
  - Debounce by timestamp (30 to 40 ms).
  - Emit `PRESSED` immediately on the press edge, for games and real-time control.
  - Count clicks and emit `CLICK_n` after about 200 ms of quiet.
  - Emit hold events from a timer.
- Saved settings (flash, NVS or EEPROM) are written on change, limited in rate, and never from an interrupt.

**Names:**
- `Module_Verb` for functions: `Button_Init`, `Uart_Write`, `Led_SetColor`.
- `snake_case_t` for types.
- `s_` for static module state.
- `UPPER_CASE` for constants.
- App handlers passed down as callbacks are named `App_On<Event>`.

## 4. Rules

- **Never hold back a requested feature for lack of proof.** What the person says about their parts and wiring is a fact. Build the feature in full, and put any doubt in the block description. Never leave a pin undriven, a display uninitialised or a function empty because nobody has verified the hardware.
- **Known parts use their standard library** inside a driver, rather than raw bytes: the maker's or a widely used Arduino library for that exact part. List it in libraries.
- **No stubs.** Every function does its job. A function that only ignores its inputs and returns nothing or a failure is refused.
- **Outputs that must be on are on at startup.** For example, a display backlight is switched on once the display is initialised.
- **Interrupts capture and leave.** Read the register, push into a ring buffer or set a flag, clear the interrupt. No parsing, drawing, printing, flash writes, delays or app callbacks inside an interrupt.
- **Shared variables.** Anything shared between an interrupt and the loop is `volatile`. Read multi-byte values with interrupts briefly off.
- **Bounds.** Check every buffer index: `if (pos < LEN) buf[pos++] = b; else s_overflows++;`.
- **No busy-waiting.** Never wait on a peripheral in the loop, as in `while (!TXE)`. Queue output and send what fits each pass, using `Serial.availableForWrite()` on Arduino.
- **Timing.** Use `if ((uint32_t)(now - start) >= period)`, which is safe when the counter wraps. No `delay()` after setup.
- **Include guards** wrap the whole header, with `#endif` on the last line.
- **Encapsulation.** Module state is `static`. Only what the header declares is public.
- **Pin setup order.** Set a pin's mode before the first write. Drive every agreed pin, including a backlight.
- **Active levels.** Take every input's and output's active level from the board manual. If the manual doesn't settle it, keep it in one constant, such as `BUTTON_ACTIVE_LEVEL`, and say so in the block description.
- **Pins from the list.** Pins come only from `ba_pins.h`. A driver never types a pin number.
- **Files.** One job per file, and no requested feature behind a `#define` that's off.

## 5. Platform, board and part facts

These arrive in part_skills, chosen for this device's platform, board and parts. Follow them where they apply. When part_skills says nothing about a part, look it up in its datasheet or the board manual before relying on it.

## 6. Templates

```c
/* src/driver/uart.h */
#ifndef DRIVER_UART_H
#define DRIVER_UART_H
#include <stdint.h>
typedef void (*uart_rx_cb_t)(uint8_t byte);
void Uart_Init(uint32_t baud, uart_rx_cb_t on_byte);
uint16_t Uart_Write(const uint8_t *data, uint16_t len);  /* bytes queued; never blocks */
void Uart_Process(void);                                 /* moves queued bytes; call every pass */
#endif
```

```c
/* src/mid/button.h */
#ifndef MID_BUTTON_H
#define MID_BUTTON_H
#include <stdint.h>
typedef enum { BUTTON_PRESSED, BUTTON_RELEASED, BUTTON_CLICK_1, BUTTON_CLICK_2, BUTTON_HOLD_1S } button_event_t;
typedef void (*button_event_cb_t)(uint8_t button, button_event_t event);
void Button_Init(button_event_cb_t on_event);
void Button_Process(uint32_t now_ms);
#endif
```

```c
/* src/app/app.cpp */
static void App_OnButton(uint8_t button, button_event_t event) { /* change state */ }
void App_Init(void) { Button_Init(App_OnButton); Display_Init(); }
void App_Process(uint32_t now_ms) { Button_Process(now_ms); Display_Process(now_ms); /* state machine */ }
```

On Arduino frameworks, write `.cpp` files in this C style. `main.cpp` holds only `setup()` and `loop()`.
