# Plain-Make build of the same sources (for the `bear -- make` compile-database flow).
CC ?= cc
CFLAGS ?= -O2 -Wall -Iinclude

all: rbtool test_ringbuf

libringbuf.a: src/ringbuf.o src/log.o
	ar rcs $@ $^

rbtool: tools/rbtool.o libringbuf.a
	$(CC) -o $@ $^

test_ringbuf: tests/test_ringbuf.o libringbuf.a
	$(CC) -o $@ $^

%.o: %.c
	$(CC) $(CFLAGS) -c -o $@ $<

clean:
	rm -f src/*.o tools/*.o tests/*.o libringbuf.a rbtool test_ringbuf

.PHONY: all clean
