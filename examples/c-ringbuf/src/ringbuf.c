#include <stdlib.h>
#include <string.h>

#include "ringbuf/ringbuf.h"
#include "internal.h"

#ifdef RB_THREADSAFE
#include <pthread.h>
#endif

struct rb_buffer {
    unsigned char *data;
    size_t cap, head, tail, used;
#ifdef RB_THREADSAFE
    pthread_mutex_t lock;
#endif
};

static void rb_lock(rb_buffer *rb) {
#ifdef RB_THREADSAFE
    pthread_mutex_lock(&rb->lock);
#else
    (void)rb;
#endif
}

static void rb_unlock(rb_buffer *rb) {
#ifdef RB_THREADSAFE
    pthread_mutex_unlock(&rb->lock);
#else
    (void)rb;
#endif
}

static size_t min_size(size_t a, size_t b) { return a < b ? a : b; }

rb_buffer *rb_create(size_t capacity) {
    rb_buffer *rb = calloc(1, sizeof *rb);
    if (!rb) return NULL;
    rb->data = malloc(capacity);
    rb->cap = capacity;
#ifdef RB_THREADSAFE
    pthread_mutex_init(&rb->lock, NULL);
#endif
    if (rb_debug_enabled()) rb_log("create cap=%zu", capacity);
    return rb;
}

void rb_destroy(rb_buffer *rb) {
    if (!rb) return;
    free(rb->data);
    free(rb);
}

size_t rb_write(rb_buffer *rb, const void *data, size_t len) {
    const unsigned char *p = data;
    rb_lock(rb);
    size_t n = min_size(len, rb->cap - rb->used);
    for (size_t i = 0; i < n; i++) {
        rb->data[rb->tail] = p[i];
        rb->tail = (rb->tail + 1) % rb->cap;
    }
    rb->used += n;
    rb_unlock(rb);
    return n;
}

size_t rb_read(rb_buffer *rb, void *out, size_t len) {
    unsigned char *p = out;
    rb_lock(rb);
    size_t n = min_size(len, rb->used);
    for (size_t i = 0; i < n; i++) {
        p[i] = rb->data[rb->head];
        rb->head = (rb->head + 1) % rb->cap;
    }
    rb->used -= n;
    rb_unlock(rb);
    return n;
}

size_t rb_used(const rb_buffer *rb) { return rb->used; }

/* Not declared in any header and never called. */
size_t rb_compact_legacy(rb_buffer *rb) {
    memmove(rb->data, rb->data + rb->head, rb->used);
    rb->head = 0;
    rb->tail = rb->used;
    return rb->used;
}
