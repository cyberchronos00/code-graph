#include <assert.h>
#include <string.h>

#include "ringbuf/ringbuf.h"

static void test_wraparound(void) {
    rb_buffer *rb = rb_create(4);
    char out[4];
    assert(rb_write(rb, "abc", 3) == 3);
    assert(rb_read(rb, out, 2) == 2);
    assert(rb_write(rb, "def", 3) == 3);
    assert(rb_used(rb) == 4);
    rb_destroy(rb);
}

int main(void) {
    test_wraparound();
    return 0;
}
