#include <string.h>
#include <sys/socket.h>
#include <sys/un.h>

#define CTL_PATH "/run/ctl.sock"

int ctl_listen(void) {
    struct sockaddr_un addr;
    int fd = socket(AF_UNIX, SOCK_STREAM, 0);
    memset(&addr, 0, sizeof(addr));
    addr.sun_family = AF_UNIX;
    strncpy(addr.sun_path, CTL_PATH, sizeof(addr.sun_path) - 1);
    bind(fd, (struct sockaddr *)&addr, sizeof(addr));
    return fd;
}

int ctl_connect(void) {
    struct sockaddr_un addr;
    int fd = socket(AF_UNIX, SOCK_STREAM, 0);
    addr.sun_family = AF_UNIX;
    strncpy(addr.sun_path, CTL_PATH, sizeof(addr.sun_path) - 1);
    connect(fd, (struct sockaddr *)&addr, sizeof(addr));
    return fd;
}
