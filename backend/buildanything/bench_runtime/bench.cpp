/* The bench's core: simulated time, pins, buses, the laptop link and results. Platform code, never written by an agent. */
#include "Arduino.h"
#include "SPI.h"
#include "Wire.h"
#include "Servo.h"
#include "bench.h"
#include <stdarg.h>
#include <map>
#include <deque>
#include <string>
#include <vector>
#include <exception>
#include <stdexcept>

#ifndef BENCH_SPI_MAX_HZ
#define BENCH_SPI_MAX_HZ 42000000UL
#endif
#ifndef BENCH_SPI_BASE_HZ
#define BENCH_SPI_BASE_HZ 0UL  // 0: any clock up to the maximum. Otherwise base / 2^k, as on STM32.
#endif
#ifndef BENCH_SPI_BYTE_OVERHEAD_NS
#define BENCH_SPI_BYTE_OVERHEAD_NS 1000
#endif
#ifndef BENCH_LOOP_NS
#define BENCH_LOOP_NS 1000
#endif
#ifndef BENCH_PIN_WRITE_NS
#define BENCH_PIN_WRITE_NS 600
#endif
#ifndef BENCH_SERIAL_BUFFER
#define BENCH_SERIAL_BUFFER 64
#endif
#ifndef BENCH_PWM_HZ
#define BENCH_PWM_HZ 1000
#endif
#ifndef BENCH_STM32_PORTS
#define BENCH_STM32_PORTS 1  // pins named PA0..PK15 lose writes until their port is switched on by pinMode
#endif

namespace {

struct Entry { std::string text; double t_ms; int count; bool ok; std::string who; };

uint64_t g_now = 0;
uint64_t g_limit_ns = 0;
bool g_irq_on = true, g_in_isr = false;
int g_pin_mode[BENCH_PIN_COUNT];
int g_latch[BENCH_PIN_COUNT];
bool g_configured[BENCH_PIN_COUNT];
bool g_port_on[16];
int g_seen_level[BENCH_PIN_COUNT];
struct Isr { std::function<void(void)> fn; int mode; bool pending; };
std::map<int, Isr> g_isrs;
std::vector<Entry> g_checks, g_problems, g_notes;
struct Measure { std::string name, unit; double value; };
std::vector<Measure> g_measures;
std::vector<std::string> g_screens;
std::string g_out_dir = ".";
int g_adc_bits = 10, g_pwm_bits = 8;
uint32_t g_pwm_hz = BENCH_PWM_HZ;

std::vector<bench::Device*>& devices() { static std::vector<bench::Device*> d; return d; }
std::vector<BenchScenario*>& scenarios() { static std::vector<BenchScenario*> s; return s; }

std::string vformat(const char* format, va_list args) {
  char b[1024];
  vsnprintf(b, sizeof b, format, args);
  return b;
}

void add(std::vector<Entry>& list, const std::string& who, const std::string& text, bool ok = true) {
  for (auto& e : list) if (e.who == who && e.text == text && e.ok == ok) { e.count++; return; }
  if (list.size() < 60) list.push_back({text, g_now / 1e6, 1, ok, who});
}

void tick_all() { for (auto* d : devices()) d->tick(g_now); }

void fire(int pin, Isr& isr) {
  if (!g_irq_on || g_in_isr) { isr.pending = true; return; }
  g_in_isr = true;
  isr.fn();
  g_in_isr = false;
}

void check_edges() {
  for (auto& [pin, isr] : g_isrs) {
    int now = bench::level(pin);
    int before = g_seen_level[pin];
    g_seen_level[pin] = now;
    if (before < 0 || before == now) continue;
    if (isr.mode == CHANGE || (isr.mode == RISING && now) || (isr.mode == FALLING && !now)) fire(pin, isr);
  }
}

struct SerialPort {
  bool begun = false;
  uint32_t baud = 115200;
  std::deque<uint8_t> rx;                         // in the chip's receive buffer
  std::deque<std::pair<uint64_t, uint8_t>> wire;   // on the way, with arrival times
  uint64_t tx_free_at = 0;                         // when the transmit buffer has drained
  size_t tx_queued = 0;
  uint64_t last_drain = 0;
  std::string sent;
};
SerialPort g_ports[4];

uint64_t byte_ns(uint32_t baud) { return baud ? 10ull * 1000000000ull / baud : 0; }

void move_serial() {
  for (int p = 0; p < 4; ++p) {
    SerialPort& s = g_ports[p];
    while (!s.wire.empty() && s.wire.front().first <= g_now) {
      if (!s.begun) add(g_notes, "Bench", "Bytes reached a serial port before Serial.begin() was called, so they were lost.");
      else if (s.rx.size() >= BENCH_SERIAL_BUFFER) add(g_problems, "Bench", "Incoming serial bytes were lost because the receive buffer was full: the firmware didn't read them fast enough.");
      else s.rx.push_back(s.wire.front().second);
      s.wire.pop_front();
    }
    if (s.tx_queued && s.baud) {
      uint64_t each = byte_ns(s.baud);
      while (s.tx_queued && s.last_drain + each <= g_now) { s.tx_queued--; s.last_drain += each; }
      if (!s.tx_queued) s.last_drain = g_now;
    } else s.last_drain = g_now;
  }
}

void advance(uint64_t ns) {
  if (!ns) return;
  g_now += ns;
  if (g_limit_ns && g_now > g_limit_ns) throw std::runtime_error("time limit");
  move_serial();
  tick_all();
  if (!g_isrs.empty()) check_edges();
}

int port_of(int pin) { return pin >= 256 && pin < 256 + 176 ? (pin - 256) / 16 : -1; }

void notify_pin(int pin) {
  int lv = bench::level(pin);
  for (auto* d : devices()) d->on_pin(pin, lv);
}

std::string json(const std::string& s) {
  std::string o = "\"";
  for (unsigned char c : s) {
    if (c == '"' || c == '\\') { o += '\\'; o += (char)c; }
    else if (c == '\n') o += "\\n";
    else if (c < 0x20) { char b[8]; snprintf(b, sizeof b, "\\u%04x", c); o += b; }
    else o += (char)c;
  }
  return o + "\"";
}

std::string entries(const std::vector<Entry>& list) {
  std::string o = "[";
  for (size_t i = 0; i < list.size(); ++i) {
    const Entry& e = list[i];
    char t[64];
    snprintf(t, sizeof t, "%.3f", e.t_ms);
    o += (i ? "," : "") + std::string("{\"who\":") + json(e.who) + ",\"text\":" + json(e.text) + ",\"t_ms\":" + t + ",\"count\":" + std::to_string(e.count) + ",\"ok\":" + (e.ok ? "true" : "false") + "}";
  }
  return o + "]";
}

}  // namespace

/* ---------- Devices and the scenario API ---------- */
namespace bench {

Device::Device(const char* n) : name(n) { devices().push_back(this); }
void Device::problem(const char* format, ...) { va_list a; va_start(a, format); add(g_problems, name, vformat(format, a)); va_end(a); }
void Device::changed(int pin) { (void)pin; if (!g_isrs.empty()) check_edges(); }
void Device::uart_send(int port, const uint8_t* data, size_t length) {
  if (port < 0 || port > 3) return;
  uint64_t t = g_ports[port].wire.empty() ? g_now : g_ports[port].wire.back().first;
  for (size_t i = 0; i < length; ++i) { t += byte_ns(g_ports[port].baud); g_ports[port].wire.push_back({t, data[i]}); }
}

uint64_t now_ns() { return g_now; }
double now_ms() { return g_now / 1e6; }
double now_us() { return g_now / 1e3; }

bool is_output(int pin) { return pin >= 0 && pin < BENCH_PIN_COUNT && (g_pin_mode[pin] == OUTPUT || g_pin_mode[pin] == OUTPUT_OPEN_DRAIN) && g_configured[pin]; }

int level(int pin) {
  if (pin < 0 || pin >= BENCH_PIN_COUNT) return 0;
  if (is_output(pin) && g_pin_mode[pin] == OUTPUT) return g_latch[pin];
  int driven = -1;
  const char* first = nullptr;
  for (auto* d : devices()) {
    int v = d->drive(pin);
    if (v < 0) continue;
    if (driven >= 0 && v != driven) add(g_problems, "Bench", std::string("Two parts drive ") + pin_name(pin) + " to different levels (" + first + " and " + d->name + ").");
    if (driven < 0) { driven = v; first = d->name; }
  }
  if (is_output(pin)) return g_latch[pin] == 0 ? 0 : (driven >= 0 ? driven : 0);  // open drain needs a pull-up to read high
  if (driven >= 0) return driven;
  if (g_pin_mode[pin] == INPUT_PULLUP) return 1;
  if (g_pin_mode[pin] == INPUT_PULLDOWN) return 0;
  return 0;
}

const char* pin_name(int pin) {
  static char b[16][12];
  static int n = 0;
  char* o = b[n++ % 16];
  if (port_of(pin) >= 0) snprintf(o, 12, "P%c%d", 'A' + port_of(pin), (pin - 256) % 16);
  else snprintf(o, 12, "pin %d", pin);
  return o;
}

void run_ms(double ms) {
  uint64_t until = g_now + (uint64_t)(ms * 1e6);
  fprintf(stderr, "t=%.1f ms\n", now_ms());
  while (g_now < until) { loop(); advance(BENCH_LOOP_NS); }
}

double run_until(const std::function<bool()>& cond, double max_ms) {
  uint64_t start = g_now, until = g_now + (uint64_t)(max_ms * 1e6);
  while (g_now < until) {
    if (cond()) return (g_now - start) / 1e6;
    loop();
    advance(BENCH_LOOP_NS);
  }
  return cond() ? (g_now - start) / 1e6 : -1;
}

void serial_send(const uint8_t* data, size_t length) {
  SerialPort& s = g_ports[0];
  uint64_t t = s.wire.empty() ? g_now : std::max<uint64_t>(g_now, s.wire.back().first);
  for (size_t i = 0; i < length; ++i) { t += byte_ns(s.baud); s.wire.push_back({t, data[i]}); }
}
void serial_send(const std::string& text) { serial_send((const uint8_t*)text.data(), text.size()); }
const std::string& serial_output() { return g_ports[0].sent; }
size_t serial_output_since(size_t offset, std::string* out) {
  const std::string& s = g_ports[0].sent;
  if (out) *out = offset < s.size() ? s.substr(offset) : "";
  return s.size();
}

void check(bool ok, const char* format, ...) { va_list a; va_start(a, format); add(g_checks, "Scenario", vformat(format, a), ok); va_end(a); }
void measure(const char* name, double value, const char* unit) { g_measures.push_back({name, unit, value}); }
void note(const char* format, ...) { va_list a; va_start(a, format); add(g_notes, "Scenario", vformat(format, a)); va_end(a); }

/* ---------- Screen ---------- */
#ifndef BENCH_SCREEN_TURN
#define BENCH_SCREEN_TURN 0
#endif
Screen::Screen(int w, int h) : width(w), height(h), px_(w * h, 0), written_at_(w * h, 0), changed_at_(w * h, 0) {}
void Screen::set_window(int x0, int y0, int x1, int y1) {
  wx0_ = std::max(0, std::min(x0, x1)); wx1_ = std::min(width - 1, std::max(x0, x1));
  wy0_ = std::max(0, std::min(y0, y1)); wy1_ = std::min(height - 1, std::max(y0, y1));
  cx_ = wx0_; cy_ = wy0_;
  int ux0 = wx0_, ux1 = wx1_, uy0 = wy0_, uy1 = wy1_;  // in the picture's coordinates, after any turn a person set
  if (BENCH_SCREEN_TURN == 1 || BENCH_SCREEN_TURN == 2) { ux0 = width - 1 - wx1_; ux1 = width - 1 - wx0_; }
  if (BENCH_SCREEN_TURN == 1 || BENCH_SCREEN_TURN == 3) { uy0 = height - 1 - wy1_; uy1 = height - 1 - wy0_; }
  updates_.push_back({0, ux0, uy0, ux1, uy1, 0});
  if (updates_.size() > 400000) updates_.erase(updates_.begin(), updates_.begin() + 200000);
}
void Screen::push(uint16_t c) {
  if (cx_ >= 0 && cx_ < width && cy_ >= 0 && cy_ < height) {
    // A person can turn the picture to match the real panel: how the glass is mounted isn't in a controller datasheet.
    int px = cx_, py = cy_;
    if (BENCH_SCREEN_TURN == 1 || BENCH_SCREEN_TURN == 2) px = width - 1 - px;
    if (BENCH_SCREEN_TURN == 1 || BENCH_SCREEN_TURN == 3) py = height - 1 - py;
    size_t i = (size_t)py * width + px;
    written_at_[i] = g_now ? g_now : 1;
    if (px_[i] != c) { px_[i] = c; changed_at_[i] = g_now ? g_now : 1; if (!updates_.empty()) updates_.back().changed++; }
  }
  ++written_;
  if (bursts_.empty() || g_now > last_push_ + 2000000ull) bursts_.push_back({g_now, 0});  // a pause of 2 ms starts a new burst
  bursts_.back().second++;
  if (!updates_.empty() && !updates_.back().burst_at) updates_.back().burst_at = bursts_.back().first ? bursts_.back().first : 1;
  last_push_ = g_now;
  if (++cx_ > wx1_) { cx_ = wx0_; if (++cy_ > wy1_) cy_ = wy0_; }
}
uint16_t Screen::pixel(int x, int y) const { return x >= 0 && y >= 0 && x < width && y < height ? px_[(size_t)y * width + x] : 0; }
long Screen::written_since(int x, int y, int w, int h, uint64_t since) const {
  long n = 0;
  for (int j = std::max(0, y); j < std::min(height, y + h); ++j)
    for (int i = std::max(0, x); i < std::min(width, x + w); ++i) n += written_at_[(size_t)j * width + i] > since;
  return n;
}
long Screen::changed_since(int x, int y, int w, int h, uint64_t since) const {
  long n = 0;
  for (int j = std::max(0, y); j < std::min(height, y + h); ++j)
    for (int i = std::max(0, x); i < std::min(width, x + w); ++i) n += changed_at_[(size_t)j * width + i] > since;
  return n;
}
std::vector<double> Screen::update_times_ms(int x, int y, int w, int h, uint64_t since) const {
  std::vector<double> out;
  uint64_t last = 0;
  for (const Update& u : updates_) {
    if (!u.changed || u.burst_at <= since || u.burst_at == last) continue;
    if (u.x1 < x || u.x0 >= x + w || u.y1 < y || u.y0 >= y + h) continue;
    out.push_back(u.burst_at / 1e6);
    last = u.burst_at;
  }
  return out;
}
std::vector<double> Screen::full_redraws_ms(double fraction) const {
  std::vector<double> out;
  for (auto& b : bursts_) if (b.second >= fraction * width * height) out.push_back(b.first / 1e6);
  return out;
}
void Screen::save(const char* name) const {
  std::string file;
  for (const char* p = name; *p; ++p) file += isalnum((unsigned char)*p) ? *p : '_';
  file += ".ppm";
  FILE* f = fopen((g_out_dir + "/" + file).c_str(), "wb");
  if (!f) return;
  fprintf(f, "P6\n%d %d\n255\n", width, height);
  for (uint16_t c : px_) {
    int r = ((c >> 11) & 0x1F) * 255 / 31, g = ((c >> 5) & 0x3F) * 255 / 63, b = (c & 0x1F) * 255 / 31;
    if (!lit) { r /= 8; g /= 8; b /= 8; }
    fputc(r, f); fputc(g, f); fputc(b, f);
  }
  fclose(f);
  g_screens.push_back(file);
}

}  // namespace bench

BenchScenario::BenchScenario(const char* t, const char* c, void (*r)()) : title(t), covers(c), run(r) { scenarios().push_back(this); }

/* ---------- The Arduino core ---------- */
extern "C" {

void pinMode(uint32_t pin, uint32_t mode) {
  if (pin >= BENCH_PIN_COUNT) return;
  int port = port_of(pin);
  if (port >= 0) g_port_on[port] = true;
  bool was_output = bench::is_output(pin);
  g_pin_mode[pin] = (int)mode;
  g_configured[pin] = true;
  if ((mode == OUTPUT || mode == OUTPUT_OPEN_DRAIN) && !was_output && port >= 0 && BENCH_STM32_PORTS && g_latch[pin] < 0) g_latch[pin] = 0;
  if (g_latch[pin] < 0) g_latch[pin] = 0;
  advance(BENCH_PIN_WRITE_NS * 2);
  notify_pin((int)pin);
  if (g_isrs.count((int)pin)) g_seen_level[pin] = bench::level((int)pin);
}

void digitalWrite(uint32_t pin, uint32_t value) {
  if (pin >= BENCH_PIN_COUNT) return;
  int port = port_of(pin);
  if (!bench::is_output(pin)) {
    if (port >= 0 && BENCH_STM32_PORTS && !g_port_on[port]) {
      add(g_problems, "Bench", std::string(bench::pin_name(pin)) + " was written before pinMode switched its port on, so the write was lost. Call pinMode(OUTPUT) first.");
      advance(BENCH_PIN_WRITE_NS);
      return;
    }
    add(g_notes, "Bench", std::string(bench::pin_name(pin)) + " was written while it wasn't an output.");
    if (port < 0 && value && g_pin_mode[pin] == INPUT) g_pin_mode[pin] = INPUT_PULLUP;  // AVR behaviour
  }
  g_latch[pin] = value ? 1 : 0;
  advance(BENCH_PIN_WRITE_NS);
  if (bench::is_output(pin)) notify_pin((int)pin);
}

int digitalRead(uint32_t pin) {
  if (pin >= BENCH_PIN_COUNT) return 0;
  advance(BENCH_PIN_WRITE_NS);
  if (!g_configured[pin]) add(g_notes, "Bench", std::string(bench::pin_name(pin)) + " was read before pinMode set it up.");
  return bench::level((int)pin);
}

int analogRead(uint32_t pin) {
  advance(10000);
  int mv = -1;
  for (auto* d : devices()) if ((mv = d->drive_mv((int)pin)) >= 0) break;
  if (mv < 0) mv = bench::level((int)pin) ? 3300 : 0;
  long full = (1L << g_adc_bits) - 1;
  return (int)std::min<long>(full, (long)mv * full / 3300);
}

void analogWrite(uint32_t pin, int value) {
  advance(BENCH_PIN_WRITE_NS * 4);
  double period = 1e6 / g_pwm_hz;
  double duty = std::max(0.0, std::min(1.0, value / (double)((1 << g_pwm_bits) - 1)));
  if (pin < BENCH_PIN_COUNT) { g_configured[pin] = true; g_pin_mode[pin] = OUTPUT; g_latch[pin] = duty > 0.5; }
  for (auto* d : devices()) d->on_pwm((int)pin, duty * period, period);
}
void analogReadResolution(int bits) { g_adc_bits = bits; }
void analogWriteResolution(int bits) { g_pwm_bits = bits; }
void analogWriteFrequency(uint32_t hz) { g_pwm_hz = hz ? hz : BENCH_PWM_HZ; }

unsigned long millis(void) { advance(100); return (unsigned long)(g_now / 1000000ull); }
unsigned long micros(void) { advance(100); return (unsigned long)(g_now / 1000ull); }
void delay(unsigned long ms) { uint64_t until = g_now + ms * 1000000ull; while (g_now < until) advance(std::min<uint64_t>(100000, until - g_now)); }
void delayMicroseconds(unsigned int us) { advance(us * 1000ull); }
void yield(void) { advance(100); }

void attachInterrupt(uint32_t pin, void (*callback)(void), int mode) { g_isrs[(int)pin] = {callback, mode, false}; g_seen_level[pin] = bench::level((int)pin); }
void detachInterrupt(uint32_t pin) { g_isrs.erase((int)pin); }
void bench_interrupts(bool on) {
  g_irq_on = on;
  if (on) for (auto& [pin, isr] : g_isrs) if (isr.pending) { isr.pending = false; fire(pin, isr); }
}
void tone(uint32_t pin, unsigned int frequency, unsigned long duration) { (void)duration; for (auto* d : devices()) d->on_pwm((int)pin, frequency ? 5e5 / frequency : 0, frequency ? 1e6 / frequency : 0); }
void noTone(uint32_t pin) { for (auto* d : devices()) d->on_pwm((int)pin, 0, 0); }
unsigned long pulseIn(uint32_t pin, uint32_t state, unsigned long timeout) { (void)pin; (void)state; advance(timeout * 1000ull); return 0; }
void shiftOut(uint32_t data_pin, uint32_t clock_pin, uint32_t bit_order, uint8_t value) {
  for (int i = 0; i < 8; ++i) {
    int b = bit_order == LSBFIRST ? (value >> i) & 1 : (value >> (7 - i)) & 1;
    digitalWrite(data_pin, b); digitalWrite(clock_pin, HIGH); digitalWrite(clock_pin, LOW);
  }
}
uint8_t shiftIn(uint32_t data_pin, uint32_t clock_pin, uint32_t bit_order) {
  uint8_t v = 0;
  for (int i = 0; i < 8; ++i) {
    digitalWrite(clock_pin, HIGH);
    int b = digitalRead(data_pin);
    v |= bit_order == LSBFIRST ? b << i : b << (7 - i);
    digitalWrite(clock_pin, LOW);
  }
  return v;
}

}  // extern "C"

void attachInterrupt(uint32_t pin, std::function<void(void)> callback, int mode) { g_isrs[(int)pin] = {callback, mode, false}; g_seen_level[pin] = bench::level((int)pin); }

static uint32_t g_random = 12345;
long random(long howbig) { if (howbig <= 0) return 0; g_random = g_random * 1103515245u + 12345u; return (long)((g_random >> 1) % (uint32_t)howbig); }
long random(long lo, long hi) { return hi <= lo ? lo : lo + random(hi - lo); }
void randomSeed(unsigned long seed) { if (seed) g_random = (uint32_t)seed; }

/* ---------- Stream and serial ---------- */
int Stream::timed_read() {
  uint64_t until = g_now + timeout_ * 1000000ull;
  do { int c = read(); if (c >= 0) return c; advance(10000); } while (g_now < until);
  return -1;
}
size_t Stream::readBytes(char* buffer, size_t length) { size_t n = 0; while (n < length) { int c = timed_read(); if (c < 0) break; buffer[n++] = (char)c; } return n; }
size_t Stream::readBytesUntil(char t, char* buffer, size_t length) { size_t n = 0; while (n < length) { int c = timed_read(); if (c < 0 || c == t) break; buffer[n++] = (char)c; } return n; }
String Stream::readString() { String s; int c; while ((c = timed_read()) >= 0) s += (char)c; return s; }
String Stream::readStringUntil(char t) { String s; int c; while ((c = timed_read()) >= 0 && c != t) s += (char)c; return s; }
long Stream::parseInt() {
  int c;
  while ((c = peek()) >= 0 && c != '-' && !isdigit(c)) read();
  if (c < 0) { c = timed_read(); if (c < 0) return 0; } else read();
  bool neg = c == '-';
  long v = neg ? 0 : c - '0';
  while ((c = peek()) >= 0 && isdigit(c)) { v = v * 10 + (c - '0'); read(); }
  return neg ? -v : v;
}
float Stream::parseFloat() { return (float)parseInt(); }
bool Stream::find(const char* target) {
  size_t n = strlen(target), k = 0;
  int c;
  while ((c = timed_read()) >= 0) { k = (char)c == target[k] ? k + 1 : ((char)c == target[0] ? 1 : 0); if (k == n) return true; }
  return false;
}

void HardwareSerial::begin(unsigned long baud, uint32_t) { g_ports[port_].begun = true; g_ports[port_].baud = (uint32_t)baud; advance(20000); }
void HardwareSerial::end() { g_ports[port_].begun = false; }
int HardwareSerial::available() { advance(100); return (int)g_ports[port_].rx.size(); }
int HardwareSerial::read() { advance(100); auto& rx = g_ports[port_].rx; if (rx.empty()) return -1; int c = rx.front(); rx.pop_front(); return c; }
int HardwareSerial::peek() { auto& rx = g_ports[port_].rx; return rx.empty() ? -1 : rx.front(); }
int HardwareSerial::availableForWrite() { advance(100); move_serial(); return (int)(BENCH_SERIAL_BUFFER - g_ports[port_].tx_queued); }
void HardwareSerial::flush() { SerialPort& s = g_ports[port_]; while (s.tx_queued) advance(byte_ns(s.baud)); }
size_t HardwareSerial::write(uint8_t byte) {
  SerialPort& s = g_ports[port_];
  if (!s.begun) { add(g_notes, "Bench", "The firmware wrote to Serial before Serial.begin() was called."); return 0; }
  while (s.tx_queued >= BENCH_SERIAL_BUFFER) advance(byte_ns(s.baud));  // a full buffer makes write() wait, as on the chip
  advance(300);
  s.tx_queued++;
  if (port_ == 0) s.sent += (char)byte;
  else for (auto* d : devices()) d->on_uart(port_, byte, s.baud);
  return 1;
}
HardwareSerial Serial(0), Serial1(1), Serial2(2), Serial3(3);

/* ---------- SPI ---------- */
static bench::SpiState g_spi = {4000000, 0, true};
static bool g_spi_begun = false;
static uint32_t actual_hz(uint32_t asked) {
  uint32_t hz = std::min<uint32_t>(asked, BENCH_SPI_MAX_HZ);
  if (BENCH_SPI_BASE_HZ) { uint32_t f = BENCH_SPI_BASE_HZ / 2; while (f > hz && f > BENCH_SPI_BASE_HZ / 256) f /= 2; hz = f; }
  return hz ? hz : 1;
}
void SPIClass::begin() { g_spi_begun = true; }
void SPIClass::end() { g_spi_begun = false; }
void SPIClass::beginTransaction(SPISettings s) { g_spi = {s.clock, s.mode, s.bit_order == MSBFIRST}; advance(500); }
void SPIClass::endTransaction() { advance(200); }
void SPIClass::setClockDivider(uint32_t divider) { g_spi.hz = (uint32_t)(F_CPU / (divider ? divider : 4)); }
void SPIClass::setDataMode(uint8_t mode) { g_spi.mode = mode; }
void SPIClass::setBitOrder(uint8_t order) { g_spi.msb_first = order == MSBFIRST; }
void SPIClass::setFrequency(uint32_t hz) { g_spi.hz = hz; }
uint8_t SPIClass::transfer(uint8_t data) {
  if (!g_spi_begun) add(g_problems, "Bench", "SPI was used before SPI.begin().");
  bench::SpiState s = g_spi;
  s.hz = actual_hz(s.hz);  // parts see the clock on the wire: the chip rounds the request down
  uint8_t in = 0xFF;
  for (auto* d : devices()) { uint8_t r = d->on_spi(data, s); if (r != 0xFF) in = r; }
  advance(8ull * 1000000000ull / actual_hz(s.hz) + BENCH_SPI_BYTE_OVERHEAD_NS);
  return in;
}
uint16_t SPIClass::transfer16(uint16_t data) {
  uint8_t a = transfer(g_spi.msb_first ? data >> 8 : data & 0xFF), b = transfer(g_spi.msb_first ? data & 0xFF : data >> 8);
  return g_spi.msb_first ? (uint16_t)(a << 8 | b) : (uint16_t)(b << 8 | a);
}
void SPIClass::transfer(void* buffer, size_t count) { uint8_t* p = (uint8_t*)buffer; for (size_t i = 0; i < count; ++i) p[i] = transfer(p[i]); }
void SPIClass::transfer(const void* tx, void* rx, size_t count) {
  const uint8_t* t = (const uint8_t*)tx;
  uint8_t* r = (uint8_t*)rx;
  for (size_t i = 0; i < count; ++i) { uint8_t v = transfer(t ? t[i] : 0xFF); if (r) r[i] = v; }
}
SPIClass SPI;

/* ---------- I2C ---------- */
void TwoWire::begin() {}
void TwoWire::beginTransmission(uint8_t address) { address_ = address; tx_len_ = 0; transmitting_ = true; }
size_t TwoWire::write(uint8_t byte) { if (!transmitting_ || tx_len_ >= sizeof tx_) return 0; tx_[tx_len_++] = byte; return 1; }
size_t TwoWire::write(const uint8_t* data, size_t length) { size_t n = 0; while (n < length && write(data[n])) ++n; return n; }
uint8_t TwoWire::endTransmission(bool) {
  transmitting_ = false;
  advance((uint64_t)(tx_len_ + 1) * 9ull * 1000000000ull / (hz_ ? hz_ : 100000) + 20000);
  for (auto* d : devices()) if (d->on_i2c_write(address_, tx_, tx_len_, hz_)) return 0;
  return 2;
}
uint8_t TwoWire::requestFrom(uint8_t address, size_t quantity, bool) {
  rx_len_ = rx_pos_ = 0;
  if (quantity > sizeof rx_) quantity = sizeof rx_;
  advance((uint64_t)(quantity + 1) * 9ull * 1000000000ull / (hz_ ? hz_ : 100000) + 20000);
  for (auto* d : devices()) { size_t n = d->on_i2c_read(address, rx_, quantity, hz_); if (n) { rx_len_ = n; break; } }
  return (uint8_t)rx_len_;
}
int TwoWire::available() { return (int)(rx_len_ - rx_pos_); }
int TwoWire::read() { return rx_pos_ < rx_len_ ? rx_[rx_pos_++] : -1; }
int TwoWire::peek() { return rx_pos_ < rx_len_ ? rx_[rx_pos_] : -1; }
TwoWire Wire(0), Wire1(1);

/* ---------- Servo ---------- */
uint8_t Servo::attach(int pin, int min_us, int max_us) { pin_ = pin; min_ = min_us; max_ = max_us; writeMicroseconds(us_); return 1; }
void Servo::detach() { if (pin_ >= 0) for (auto* d : devices()) d->on_pwm(pin_, 0, 0); pin_ = -1; }
void Servo::write(int value) {
  if (value < 200) { angle_ = std::max(0, std::min(180, value)); writeMicroseconds(min_ + (max_ - min_) * angle_ / 180); }
  else writeMicroseconds(value);
}
void Servo::writeMicroseconds(int us) {
  us_ = std::max(min_, std::min(max_, us));
  if (pin_ >= 0) for (auto* d : devices()) d->on_pwm(pin_, us_, 20000);
}

/* ---------- Running one scenario ---------- */
int main(int argc, char** argv) {
  for (int i = 0; i < BENCH_PIN_COUNT; ++i) { g_latch[i] = -1; g_seen_level[i] = -1; }
  if (argc >= 2 && std::string(argv[1]) == "list") {
    printf("[");
    for (size_t i = 0; i < scenarios().size(); ++i) printf("%s{\"title\":%s,\"covers\":%s}", i ? "," : "", json(scenarios()[i]->title).c_str(), json(scenarios()[i]->covers).c_str());
    printf("]\n");
    return 0;
  }
  if (argc < 3) { fprintf(stderr, "usage: bench list | bench run <n> <out_dir>\n"); return 2; }
  size_t index = (size_t)atoi(argv[2]);
  if (argc >= 4) g_out_dir = argv[3];
  if (index >= scenarios().size()) return 2;
  g_limit_ns = 30ull * 60ull * 1000000000ull;  // 30 simulated minutes at most
  std::string error;
  try {
    setup();
    scenarios()[index]->run();
  } catch (std::exception& e) {
    error = e.what();
  }
  printf("{\"title\":%s,\"covers\":%s,\"error\":%s,\"sim_ms\":%.3f,\"checks\":%s,\"problems\":%s,\"notes\":%s,\"measures\":[",
         json(scenarios()[index]->title).c_str(), json(scenarios()[index]->covers).c_str(), json(error).c_str(), g_now / 1e6,
         entries(g_checks).c_str(), entries(g_problems).c_str(), entries(g_notes).c_str());
  for (size_t i = 0; i < g_measures.size(); ++i) printf("%s{\"name\":%s,\"value\":%.6g,\"unit\":%s}", i ? "," : "", json(g_measures[i].name).c_str(), g_measures[i].value, json(g_measures[i].unit).c_str());
  printf("],\"screens\":[");
  for (size_t i = 0; i < g_screens.size(); ++i) printf("%s%s", i ? "," : "", json(g_screens[i]).c_str());
  printf("],\"serial\":%s}\n", json(g_ports[0].sent.size() > 4000 ? g_ports[0].sent.substr(g_ports[0].sent.size() - 4000) : g_ports[0].sent).c_str());
  return 0;
}
