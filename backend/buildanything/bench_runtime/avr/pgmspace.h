#pragma once
#include <stdint.h>
#include <string.h>
#define PROGMEM
#define PGM_P const char*
#define PSTR(s) (s)
#define pgm_read_byte(addr) (*(const uint8_t*)(addr))
#define pgm_read_word(addr) (*(const uint16_t*)(addr))
#define pgm_read_dword(addr) (*(const uint32_t*)(addr))
#define pgm_read_float(addr) (*(const float*)(addr))
#define pgm_read_ptr(addr) (*(void* const*)(addr))
#define pgm_read_byte_near(addr) pgm_read_byte(addr)
#define pgm_read_word_near(addr) pgm_read_word(addr)
#define pgm_read_byte_far(addr) pgm_read_byte(addr)
#define memcpy_P memcpy
#define strcpy_P strcpy
#define strncpy_P strncpy
#define strlen_P strlen
#define strcmp_P strcmp
#define sprintf_P sprintf
#define snprintf_P snprintf
typedef uint8_t prog_uint8_t;
typedef uint16_t prog_uint16_t;
