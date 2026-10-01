/* rbtool: pipe stdin through a ring buffer to stdout. */
#include <stdio.h>
#include <stdlib.h>

#include "ringbuf/ringbuf.h"

static size_t buffer_size(void) {
    const char *s = getenv("RBTOOL_SIZE");
    return s ? (size_t)strtoul(s, NULL, 10) : 4096;
}

int main(void) {
    rb_buffer *rb = rb_create(buffer_size());
    char chunk[256];
    size_t n;
    while ((n = fread(chunk, 1, sizeof chunk, stdin)) > 0) {
        rb_write(rb, chunk, n);
        n = rb_read(rb, chunk, sizeof chunk);
        fwrite(chunk, 1, n, stdout);
    }
    rb_destroy(rb);
    return 0;
}
