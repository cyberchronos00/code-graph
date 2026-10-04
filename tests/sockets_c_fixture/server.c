#include <arpa/inet.h>
#include <netinet/in.h>
#include <string.h>
#include <sys/socket.h>
#include <uv.h>
#include "common.h"

int echo_server(void) {
    int fd = socket(AF_INET, SOCK_STREAM, 0);
    struct sockaddr_in addr;
    memset(&addr, 0, sizeof(addr));
    addr.sin_family = AF_INET;
    addr.sin_addr.s_addr = htonl(INADDR_ANY);
    addr.sin_port = htons(ECHO_PORT);
    bind(fd, (struct sockaddr *)&addr, sizeof(addr));
    return listen(fd, 16);
}

static void on_read(uv_udp_t *h, ssize_t n, const uv_buf_t *buf, const struct sockaddr *a, unsigned f) {}

int beacon_listener(uv_loop_t *loop) {
    uv_udp_t sock;
    struct sockaddr_in addr;
    uv_udp_init(loop, &sock);
    uv_ip4_addr("127.0.0.1", BEACON_PORT, &addr);
    uv_udp_bind(&sock, (const struct sockaddr *)&addr, 0);
    return uv_udp_recv_start(&sock, NULL, on_read);
}
