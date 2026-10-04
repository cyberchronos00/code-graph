#include <arpa/inet.h>
#include <netinet/in.h>
#include <sys/socket.h>
#include <uv.h>
#include "common.h"

int echo_client(void) {
    int fd = socket(AF_INET, SOCK_STREAM, 0);
    struct sockaddr_in addr = {0};
    addr.sin_family = AF_INET;
    addr.sin_port = htons(ECHO_PORT);
    inet_pton(AF_INET, "127.0.0.1", &addr.sin_addr);
    return connect(fd, (struct sockaddr *)&addr, sizeof(addr));
}

int beacon_send(uv_loop_t *loop, uv_udp_send_t *req, uv_udp_t *sock, uv_buf_t *buf) {
    struct sockaddr_in dest;
    uv_ip4_addr("127.0.0.1", BEACON_PORT, &dest);
    return uv_udp_send(req, sock, buf, 1, (const struct sockaddr *)&dest, NULL);
}
