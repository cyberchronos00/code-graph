package main

import (
	"crypto/tls"
	"net/http"

	"golang.org/x/crypto/ssh"
	"google.golang.org/grpc"
	"google.golang.org/grpc/credentials/insecure"
)

func lenientClient() *http.Client {
	return &http.Client{Transport: &http.Transport{TLSClientConfig: &tls.Config{InsecureSkipVerify: true}}}
}

func strictClient() *http.Client {
	return &http.Client{Transport: &http.Transport{TLSClientConfig: &tls.Config{InsecureSkipVerify: false}}}
}

func stockSession() *ssh.ClientConfig {
	return &ssh.ClientConfig{User: "sync", HostKeyCallback: ssh.InsecureIgnoreHostKey()}
}

func inventoryConn() (*grpc.ClientConn, error) {
	return grpc.Dial("inventory.bookstore.example:50051", grpc.WithTransportCredentials(insecure.NewCredentials()))
}

func localInventoryConn() (*grpc.ClientConn, error) {
	return grpc.Dial("localhost:50051", grpc.WithInsecure())
}

func main() {}
