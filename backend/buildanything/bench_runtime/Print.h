#pragma once
#include <stdint.h>
#include <stddef.h>
#include <string.h>
#include <stdarg.h>
#include "WString.h"

class Print;
class Printable {
 public:
  virtual ~Printable() {}
  virtual size_t printTo(Print& p) const = 0;
};

class Print {
 public:
  virtual ~Print() {}
  virtual size_t write(uint8_t byte) = 0;
  virtual size_t write(const uint8_t* buffer, size_t size) { size_t n = 0; while (size--) { if (!write(*buffer++)) break; ++n; } return n; }
  size_t write(const char* text) { return text ? write((const uint8_t*)text, strlen(text)) : 0; }
  size_t write(const char* buffer, size_t size) { return write((const uint8_t*)buffer, size); }
  virtual int availableForWrite() { return 0; }
  virtual void flush() {}

  size_t print(const __FlashStringHelper* text) { return write((const char*)text); }
  size_t print(const String& s) { return write(s.c_str()); }
  size_t print(const char* text) { return write(text); }
  size_t print(char c) { return write((uint8_t)c); }
  size_t print(unsigned char n, int base = DEC_BASE) { return number((unsigned long)n, base); }
  size_t print(int n, int base = DEC_BASE) { return signed_number(n, base); }
  size_t print(unsigned int n, int base = DEC_BASE) { return number(n, base); }
  size_t print(long n, int base = DEC_BASE) { return signed_number(n, base); }
  size_t print(unsigned long n, int base = DEC_BASE) { return number(n, base); }
  size_t print(long long n, int base = DEC_BASE) { return signed_number(n, base); }
  size_t print(unsigned long long n, int base = DEC_BASE) { return number(n, base); }
  size_t print(double n, int digits = 2) { char b[48]; snprintf(b, sizeof b, "%.*f", digits, n); return write(b); }
  size_t print(const Printable& x) { return x.printTo(*this); }
  template <class T> size_t println(const T& x) { size_t n = print(x); return n + println(); }
  template <class T> size_t println(const T& x, int arg) { size_t n = print(x, arg); return n + println(); }
  size_t println() { return write("\r\n"); }
  size_t printf(const char* format, ...) {
    char b[256];
    va_list args;
    va_start(args, format);
    int n = vsnprintf(b, sizeof b, format, args);
    va_end(args);
    return n > 0 ? write((const uint8_t*)b, (size_t)n < sizeof b ? (size_t)n : sizeof b - 1) : 0;
  }
  int getWriteError() { return 0; }
  void clearWriteError() {}

 private:
  enum { DEC_BASE = 10 };
  size_t number(unsigned long long n, int base) {
    char b[70];
    char* p = b + sizeof b - 1;
    *p = 0;
    if (base < 2) base = 10;
    do { int d = (int)(n % base); *--p = (char)(d < 10 ? '0' + d : 'A' + d - 10); n /= base; } while (n);
    return write(p);
  }
  size_t signed_number(long long n, int base) {
    if (n < 0 && base == 10) { size_t k = write((uint8_t)'-'); return k + number((unsigned long long)(-n), base); }
    return number((unsigned long long)n, base);
  }
};
