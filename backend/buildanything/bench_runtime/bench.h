/* The laptop test bench: the device's real firmware runs here against fake parts.
 *
 * Time is simulated. It only moves when the firmware does something that takes time on the real chip:
 * a loop pass, a pin write, an SPI byte at the requested clock, a serial byte at the baud rate, or a delay.
 * So a slow full-screen redraw is slow here too, and a late button response shows up as late.
 *
 * Fake parts derive from bench::Device. They see what the microcontroller does to their pins and buses,
 * answer as the part's documents say it would, and call problem() whenever the firmware breaks a rule the
 * documents state. Scenarios drive the fakes and check what a person would see.
 */
#pragma once
#include <stdint.h>
#include <stddef.h>
#include <string>
#include <vector>
#include <functional>
#if __has_include("bench_board.h")
#include "bench_board.h"
#endif
#include "bench_pins.h"  // so ba_pins.h names like PA5 mean the same here as in the firmware

namespace bench {

struct SpiState {
  uint32_t hz;      // the clock on the wire (the request, rounded down as the chip does)
  uint8_t mode;     // 0 to 3
  bool msb_first;
};

class Device {
 public:
  explicit Device(const char* name);
  virtual ~Device() {}
  const char* name;

  // The microcontroller changed a pin connected to this part. level is 0 or 1.
  virtual void on_pin(int pin, int level) { (void)pin; (void)level; }
  // The level this part drives onto a pin: 0, 1, or -1 when it isn't driving it.
  virtual int drive(int pin) { (void)pin; return -1; }
  // Millivolts this part puts on an analog pin, or -1.
  virtual int drive_mv(int pin) { (void)pin; return -1; }
  // One SPI byte. Return what the part shifts back (0xFF when it sends nothing).
  // Use bench::level(pin) to see its chip select and data/command pins.
  virtual uint8_t on_spi(uint8_t out, const SpiState& spi) { (void)out; (void)spi; return 0xFF; }
  // I2C. Return true to acknowledge the address. Bytes written to this part, then a stop or a repeated start.
  virtual bool on_i2c_write(uint8_t address, const uint8_t* data, size_t length, uint32_t hz) { (void)address; (void)data; (void)length; (void)hz; return false; }
  // Bytes the controller reads from this part. Return how many it supplied.
  virtual size_t on_i2c_read(uint8_t address, uint8_t* data, size_t length, uint32_t hz) { (void)address; (void)data; (void)length; (void)hz; return 0; }
  // A byte the microcontroller sent on a UART this part listens to (Serial1, Serial2...). port 0 is the laptop link.
  virtual void on_uart(int port, uint8_t byte, uint32_t baud) { (void)port; (void)byte; (void)baud; }
  // A PWM or servo signal on a pin: pulse width and period in microseconds. period 0 means the output stopped.
  virtual void on_pwm(int pin, double high_us, double period_us) { (void)pin; (void)high_us; (void)period_us; }
  // Called as simulated time passes, so the part can run its own timers.
  virtual void tick(uint64_t now_ns) { (void)now_ns; }

 protected:
  // The firmware broke a rule from this part's documents. Say what happened and where the rule comes from.
  void problem(const char* format, ...);
  // This part's drive level changed on a pin: lets interrupts fire.
  void changed(int pin);
  // Send bytes to the microcontroller on a UART.
  void uart_send(int port, const uint8_t* data, size_t length);
};

// Time.
uint64_t now_ns();
double now_ms();
double now_us();

// The level on a pin as the circuit sees it: the microcontroller's output, else a part's drive, else the pull resistor.
int level(int pin);
bool is_output(int pin);
const char* pin_name(int pin);

// Running the firmware. setup() has already run when a scenario starts.
void run_ms(double ms);
// Runs until cond is true. Returns the milliseconds it took, or -1 if max_ms passed first.
double run_until(const std::function<bool()>& cond, double max_ms);

// The laptop link (Serial).
void serial_send(const uint8_t* data, size_t length);
void serial_send(const std::string& text);
const std::string& serial_output();     // every byte the firmware sent so far
size_t serial_output_since(size_t offset, std::string* out);

// Results.
void check(bool ok, const char* format, ...);
void measure(const char* name, double value, const char* unit);
void note(const char* format, ...);

// A display's pixels, for display fakes. RGB565, with when each pixel was last written.
class Screen {
 public:
  Screen(int width, int height);
  int width, height;
  void set_window(int x0, int y0, int x1, int y1);  // inclusive, as most controllers take it
  void push(uint16_t rgb565);                         // the next pixel in the window
  uint16_t pixel(int x, int y) const;
  // Pixels written in a region since a time, and how many of them changed colour.
  long written_since(int x, int y, int w, int h, uint64_t since_ns) const;
  long changed_since(int x, int y, int w, int h, uint64_t since_ns) const;
  // Each burst of drawing (writes less than 2 ms apart) that changed pixels in a region since a time: its start in ms.
  // Count them over a period for an update rate; the first one after an input is the response time.
  std::vector<double> update_times_ms(int x, int y, int w, int h, uint64_t since_ns = 0) const;
  // Times when at least this fraction of the whole screen was rewritten within one burst.
  std::vector<double> full_redraws_ms(double fraction = 0.9) const;
  long pixels_written() const { return written_; }
  bool lit = false;           // the fake sets this when the panel would show anything (on, awake, backlight)
  void save(const char* name) const;  // saved with the results so a person can see it
 private:
  std::vector<uint16_t> px_;
  std::vector<uint64_t> written_at_, changed_at_;
  int wx0_ = 0, wy0_ = 0, wx1_ = 0, wy1_ = 0, cx_ = 0, cy_ = 0;
  long written_ = 0;
  uint64_t last_push_ = 0;
  std::vector<std::pair<uint64_t, long>> bursts_;
  struct Update { uint64_t burst_at; int x0, y0, x1, y1; long changed; };
  std::vector<Update> updates_;
};

}  // namespace bench

struct BenchScenario {
  const char* title;
  const char* covers;   // requirement keys, comma separated
  void (*run)();
  BenchScenario(const char* title, const char* covers, void (*run)());
};

#define BENCH_CAT2(a, b) a##b
#define BENCH_CAT(a, b) BENCH_CAT2(a, b)
// A scenario: BENCH_SCENARIO("B1 makes the dinosaur jump within 100 ms", "R6") { ... }
#define BENCH_SCENARIO(title, covers)                                                        \
  static void BENCH_CAT(bench_scenario_, __LINE__)();                                        \
  static BenchScenario BENCH_CAT(bench_entry_, __LINE__)(title, covers, BENCH_CAT(bench_scenario_, __LINE__)); \
  static void BENCH_CAT(bench_scenario_, __LINE__)()
