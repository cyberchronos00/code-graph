import os

import docker

local = docker.from_env()
remote = docker.DockerClient(base_url="tcp://10.20.0.5:2375")
sock = docker.DockerClient(base_url="unix:///run/bookstore/docker.sock")
tls = docker.TLSConfig(client_cert=(os.environ["DOCKER_CERT"], os.environ["DOCKER_KEY"]), verify=True)
secure = docker.DockerClient(base_url="tcp://build.bookstore.example:2376", tls=tls)
by_env = docker.DockerClient(base_url=os.environ["DOCKER_HOST"])


def run_report():
    return local.containers.run("bookstore/report", detach=True)


def remote_list():
    return remote.containers.list()


def sock_images():
    return sock.images.pull("bookstore/report")


def secure_list():
    return secure.containers.list()


def env_ping():
    return by_env.ping()
