# BENCH skill: fake parts and scenarios for the laptop test bench

The device's real firmware is compiled for the laptop and runs against fake parts. Time is simulated from what
the firmware does: each pin write, SPI byte at the requested clock, serial byte at the baud rate, loop pass and
delay takes the time it would on the chip. So a slow redraw is slow here, and a late response is late here.

You never see the firmware. That's on purpose: a fake written from the firmware would share its mistakes.

## 1. Writing a fake part

A fake is one C++ header, `fakes/<name>.h`, holding one class derived from `bench::Device` and one global instance
declared `inline`, constructed with the `BA_PIN_` names from `connections`. Include only `"bench.h"`, `"ba_pins.h"`,
`"ba_parts.h"` if facts exist, and standard headers. Never include firmware headers.

### What a fake must do
- **Behave as the documents say.** Model the commands, registers, data formats, levels and timing the pages and facts state.
  A value the documents don't give goes in `gaps`, and the fake picks the most common choice for that kind of part and says so in a comment.
- **Report every broken rule** with `problem()`. Say what happened, the rule, and where it comes from, for example:
  `problem("RAMWR before SLPOUT finished: the datasheet (p.89) needs 120 ms after SLPOUT, only %.1f ms passed", ms)`.
  Typical rules: a maximum clock (compare `spi.hz` or the I2C `hz` with the documented maximum), the SPI mode, a reset pulse
  that is too short, commands sent before a power-on or reset delay is over, a register written with a reserved value, a
  read from an address the part doesn't have, data sent while the part is asleep, and chip select or data/command levels.
- **Only report what really breaks the rule.** Where the documents say what happens to a short or partial pulse (for example
  "shorter than 5 µs: ignored"), do exactly that: ignore it without a problem. A pin briefly driven low when the microcontroller
  first configures it is normal. Report a problem when the part would really misbehave or the documents forbid it.
- **Track state over time** with `bench::now_ns()`: when reset was released, when a mode was entered, when the last byte came.
- **Ignore bytes when chip select is inactive.** Read it with `bench::level(pin)` inside `on_spi`.
- **Give scenarios something to observe:** public methods that answer what a person or instrument would see, such as
  `bool lit()`, `int presses_seen()`, `double angle_deg()`, `bool awake()`. List each one in `observe` with its meaning.
- **Let scenarios act like a person or the outside world:** `press()`, `release()`, `set_temperature(c)`, `set_distance_mm(d)`.
  When a level a part drives changes, call `changed(pin)` so interrupts fire.

### Patterns
- **Buttons and switches:** model the resistor and contact from the documents: what the pin reads when released
  (pulled up or down by the board, or floating if nothing is fitted) and when pressed. Return -1 from `drive()` only when
  nothing drives the pin. Add contact bounce if the documents mention it, for a few milliseconds after each change.
- **Displays:** use `bench::Screen` for the pixels. Decode command and data bytes using the data/command pin, track the
  column and row window and the pixel format, and push each pixel. Set `screen.lit` only when everything the documents say
  is needed to see an image is true: out of reset, awake, display on, and backlight on at the right level.
  Expose `const bench::Screen& screen()`. Save a picture in scenarios with `screen.save("name")`.
  Apply the orientation settings (row/column exchange and the row and column mirror bits, or however the documents name them)
  when mapping RAM addresses to screen pixels, so `Screen` holds the picture a person would see on the panel, the right way up.
  Use the visible area the module shows, which can be smaller than the controller's RAM: set the window offsets the documents give.
- **I2C sensors:** acknowledge only your own address. Keep a register pointer set by the first written byte. Return
  register values from what the scenario set, encoded exactly as the documents describe (byte order, scaling, signedness).
- **UART modules:** answer bytes received in `on_uart` with `uart_send()` in the documented format and timing.
- **Servos and motor drivers:** take pulse width and period from `on_pwm`. Complain about pulses outside the documented range
  or a wrong frame rate. Move the output at the documented speed rather than instantly.

### Example: an I2C temperature sensor (shape only, not real values)
```cpp
#pragma once
#include "bench.h"
#include "ba_pins.h"
class FakeTempSensor : public bench::Device {
 public:
  FakeTempSensor() : Device("Temperature sensor") {}
  void set_celsius(double c) { celsius_ = c; }       // a scenario changes the room temperature
  int reads() const { return reads_; }                // how many times the firmware read it
  bool on_i2c_write(uint8_t address, const uint8_t* data, size_t n, uint32_t hz) override {
    if (address != 0x48) return false;                // facts: i2c_address 0x48 (datasheet p.12)
    if (hz > 400000) problem("I2C at %u Hz, above the 400 kHz maximum (datasheet p.5)", (unsigned)hz);
    if (n >= 1) pointer_ = data[0];
    if (n >= 1 && pointer_ > 3) problem("Register 0x%02X doesn't exist: the part has registers 0 to 3 (p.14)", pointer_);
    return true;
  }
  size_t on_i2c_read(uint8_t address, uint8_t* out, size_t n, uint32_t) override {
    if (address != 0x48) return 0;
    int16_t raw = (int16_t)(celsius_ / 0.0625) << 4;   // 12-bit, left-aligned (p.13)
    if (n > 0) out[0] = raw >> 8;
    if (n > 1) out[1] = raw & 0xFF;
    ++reads_;
    return n < 2 ? n : 2;
  }
 private:
  double celsius_ = 22.0;
  uint8_t pointer_ = 0;
  int reads_ = 0;
};
inline FakeTempSensor temp_sensor;
```

## 2. Writing scenarios

`scenarios.cpp` includes `"fakes.h"` and defines scenarios with `BENCH_SCENARIO("what a person would check", "R3,R4") { ... }`.
Each starts from power-on: `setup()` has already run. Scenarios are black-box: act through the fakes and the laptop link,
and observe through the fakes, the serial output and time. Never call firmware functions or read its variables.

- One scenario per behaviour. Give it the requirement keys it covers.
- Turn each numeric requirement into a measured value: `bench::measure("B1 to jump", ms, "ms")` then
  `bench::check(ms >= 0 && ms <= 100, "jump started %.1f ms after B1 (limit 100 ms)", ms)`. Use the requirement's own limit, no extra margin. Limits include their end value: "within 100 ms" passes at exactly 100 ms, and "every second" passes with gaps of exactly 1000 ms.
- Wait with `bench::run_ms()`, or `bench::run_until([&]{ return condition; }, max_ms)` which returns the time taken or -1.
- Give the device time to start before judging it, as long as the requirements allow. Say how long you waited in the check.
- **Check what can be observed instead of giving up.** The bench can't recognise a dinosaur or read digits, but it can
  see where and when pixels change, and that is what most requirements come down to. Say the stand-in in the check text,
  for example "the game area (below y=30) changed 22 times per second". Put a requirement in `not_covered` only when no part
  of it can be observed, and when only part of it can, check that part and list the rest in `not_covered` as well.
- A "visible" change means pixels changed on a lit screen: `screen.lit && screen.changed_since(x, y, w, h, t0) > 0`.
  A response time is the first entry of `screen.update_times_ms(x, y, w, h, t0)` minus the input time.
  An update rate is how many entries `update_times_ms` gives over a measured period, divided by the period.
  Pick the region from what the requirement says (for example "the clock at the top"); if it doesn't say, use the whole screen.
- A clock that keeps time: after setting it, the clock region should change about once a second, and keep doing so with no more messages.
- Flicker or a "full-screen redraw" shows up in `screen.full_redraws_ms()` after start-up.
- To play a game long enough to reach a later state (a collision, a higher speed), just run time forward; to restart, act as the player would.
- To send a message from the laptop, include `"ba_packets.h"` and use its encode function and `ba_serial_frame`, then
  `bench::serial_send()`. Read replies from `bench::serial_output()`.
- Save a picture of every screen at the end of each scenario that involves one, with a name that says what it shows.
- If a requirement can't be checked on the bench (looks, materials, mechanical parts, real wiring), list it in `not_covered` with why.
- Every scenario ends with at least one `bench::check`.
