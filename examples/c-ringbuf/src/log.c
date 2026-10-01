#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>

#include "internal.h"

int rb_debug_enabled(void) {
    const char *v = getenv("RB_DEBUG");
    return v && v[0] == '1';
}

void rb_log(const char *fmt, ...) {
    va_list ap;
    va_start(ap, fmt);
    fputs("[ringbuf] ", stderr);
    vfprintf(stderr, fmt, ap);
    fputc('\n', stderr);
    va_end(ap);
}
