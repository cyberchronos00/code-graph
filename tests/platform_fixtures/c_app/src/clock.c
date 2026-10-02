#include "clock.h"
#ifdef _WIN32
#include <windows.h>
void sleep_ms(int ms) { Sleep(ms); }
#elif defined(__APPLE__) || defined(__linux__)
#include <unistd.h>
static void wait_us(int us) { usleep(us); }
void sleep_ms(int ms) { wait_us(ms * 1000); }
#endif

#if defined(__ANDROID__)
static void vibrate(void) {}
#endif

void beep(void) {
#if defined(__ANDROID__)
    vibrate();
#elif defined(_WIN32) && HAVE_SOUND
    MessageBeep(0);
#endif
}
