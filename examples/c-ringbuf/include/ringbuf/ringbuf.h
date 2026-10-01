/* ringbuf: a fixed-size byte ring buffer. Public API. */
#ifndef RINGBUF_H
#define RINGBUF_H

#include <stddef.h>

#if defined(_WIN32)
#define RB_API __declspec(dllexport)
#else
#define RB_API __attribute__((visibility("default")))
#endif

typedef struct rb_buffer rb_buffer;

RB_API rb_buffer *rb_create(size_t capacity);
RB_API void rb_destroy(rb_buffer *rb);
RB_API size_t rb_write(rb_buffer *rb, const void *data, size_t len);
RB_API size_t rb_read(rb_buffer *rb, void *out, size_t len);
RB_API size_t rb_used(const rb_buffer *rb);

#endif
