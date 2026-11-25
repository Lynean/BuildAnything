#pragma once
#include <string>
#include <stdlib.h>
#include <stdio.h>
#include <string.h>

class __FlashStringHelper;

// Arduino's String, on top of std::string.
class String {
 public:
  String() {}
  String(const char* s) : s_(s ? s : "") {}
  String(const __FlashStringHelper* s) : s_(s ? (const char*)s : "") {}
  String(const std::string& s) : s_(s) {}
  String(char c) : s_(1, c) {}
  String(unsigned char n, unsigned char base = 10) { set_num((unsigned long long)n, base); }
  String(int n, unsigned char base = 10) { set_signed(n, base); }
  String(unsigned int n, unsigned char base = 10) { set_num(n, base); }
  String(long n, unsigned char base = 10) { set_signed(n, base); }
  String(unsigned long n, unsigned char base = 10) { set_num(n, base); }
  String(long long n, unsigned char base = 10) { set_signed(n, base); }
  String(unsigned long long n, unsigned char base = 10) { set_num(n, base); }
  String(float n, unsigned char digits = 2) { set_float(n, digits); }
  String(double n, unsigned char digits = 2) { set_float(n, digits); }

  const char* c_str() const { return s_.c_str(); }
  unsigned int length() const { return (unsigned int)s_.size(); }
  bool reserve(unsigned int n) { s_.reserve(n); return true; }
  char charAt(unsigned int i) const { return i < s_.size() ? s_[i] : 0; }
  void setCharAt(unsigned int i, char c) { if (i < s_.size()) s_[i] = c; }
  char operator[](unsigned int i) const { return charAt(i); }
  char& operator[](unsigned int i) { return s_[i]; }
  String& operator+=(const String& o) { s_ += o.s_; return *this; }
  String& operator+=(const char* o) { s_ += o ? o : ""; return *this; }
  String& operator+=(char c) { s_ += c; return *this; }
  template <class T> String& operator+=(T n) { return *this += String(n); }
  bool concat(const String& o) { s_ += o.s_; return true; }
  template <class T> bool concat(T n) { *this += String(n); return true; }
  friend String operator+(const String& a, const String& b) { return String(a.s_ + b.s_); }
  friend String operator+(const String& a, const char* b) { return String(a.s_ + (b ? b : "")); }
  friend String operator+(const char* a, const String& b) { return String(std::string(a ? a : "") + b.s_); }
  template <class T> friend String operator+(const String& a, T b) { return a + String(b); }
  bool operator==(const String& o) const { return s_ == o.s_; }
  bool operator==(const char* o) const { return s_ == (o ? o : ""); }
  bool operator!=(const String& o) const { return s_ != o.s_; }
  bool operator!=(const char* o) const { return !(*this == o); }
  bool operator<(const String& o) const { return s_ < o.s_; }
  bool equals(const String& o) const { return s_ == o.s_; }
  bool equalsIgnoreCase(const String& o) const { if (o.s_.size() != s_.size()) return false; for (size_t i = 0; i < s_.size(); ++i) if (tolower(s_[i]) != tolower(o.s_[i])) return false; return true; }
  int compareTo(const String& o) const { return s_.compare(o.s_); }
  bool startsWith(const String& p) const { return s_.compare(0, p.s_.size(), p.s_) == 0; }
  bool endsWith(const String& p) const { return s_.size() >= p.s_.size() && s_.compare(s_.size() - p.s_.size(), p.s_.size(), p.s_) == 0; }
  int indexOf(char c, unsigned int from = 0) const { size_t i = s_.find(c, from); return i == std::string::npos ? -1 : (int)i; }
  int indexOf(const String& t, unsigned int from = 0) const { size_t i = s_.find(t.s_, from); return i == std::string::npos ? -1 : (int)i; }
  int lastIndexOf(char c) const { size_t i = s_.rfind(c); return i == std::string::npos ? -1 : (int)i; }
  String substring(unsigned int from) const { return from < s_.size() ? String(s_.substr(from)) : String(); }
  String substring(unsigned int from, unsigned int to) const { if (to > s_.size()) to = (unsigned int)s_.size(); return from < to ? String(s_.substr(from, to - from)) : String(); }
  void replace(const String& a, const String& b) { if (a.s_.empty()) return; size_t i = 0; while ((i = s_.find(a.s_, i)) != std::string::npos) { s_.replace(i, a.s_.size(), b.s_); i += b.s_.size(); } }
  void remove(unsigned int index) { if (index < s_.size()) s_.erase(index); }
  void remove(unsigned int index, unsigned int count) { if (index < s_.size()) s_.erase(index, count); }
  void toUpperCase() { for (auto& c : s_) c = (char)toupper(c); }
  void toLowerCase() { for (auto& c : s_) c = (char)tolower(c); }
  void trim() { size_t a = s_.find_first_not_of(" \t\r\n"); size_t b = s_.find_last_not_of(" \t\r\n"); s_ = a == std::string::npos ? "" : s_.substr(a, b - a + 1); }
  long toInt() const { return atol(s_.c_str()); }
  float toFloat() const { return (float)atof(s_.c_str()); }
  double toDouble() const { return atof(s_.c_str()); }
  void toCharArray(char* buffer, unsigned int size) const { if (!size) return; strncpy(buffer, s_.c_str(), size - 1); buffer[size - 1] = 0; }
  void getBytes(unsigned char* buffer, unsigned int size) const { toCharArray((char*)buffer, size); }
  explicit operator bool() const { return true; }

 private:
  std::string s_;
  void set_num(unsigned long long n, int base) { char b[70]; char* p = b + sizeof b - 1; *p = 0; if (base < 2) base = 10; do { int d = (int)(n % base); *--p = (char)(d < 10 ? '0' + d : 'a' + d - 10); n /= base; } while (n); s_ = p; }
  void set_signed(long long n, int base) { if (n < 0 && base == 10) { set_num((unsigned long long)(-n), base); s_ = "-" + s_; } else set_num((unsigned long long)n, base); }
  void set_float(double n, int digits) { char b[64]; snprintf(b, sizeof b, "%.*f", digits, n); s_ = b; }
};
