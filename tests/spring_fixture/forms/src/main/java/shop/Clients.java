package shop;

import org.springframework.stereotype.Service;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PostExchange;
import org.springframework.web.client.RestClient;
import org.springframework.web.client.RestTemplate;
import org.springframework.web.reactive.function.client.WebClient;
import org.springframework.web.service.annotation.HttpExchange;
import org.springframework.cloud.openfeign.FeignClient;

@Service
class Clients {
    RestTemplate rest;
    WebClient web;
    RestClient http;

    void go() {
        rest.getForObject("/api/books/{id}", String.class);
        web.get().uri("/api/orders/{id}");
        http.post().uri("/api/orders");
        rest.exchange("https://files.example/raw/{name}", org.springframework.http.HttpMethod.GET, null, String.class);
        WebClient.create("https://cdn.example").get().uri("/api/books/{id}");
        RestClient.builder().baseUrl("https://orders.example").build().get().uri("/v1/items/{id}");
    }
}

@FeignClient(name = "cat", url = "https://catalog.example")
interface Cat {
    @GetMapping("/api/books/{id}")
    String one();
}

@HttpExchange("/v1")
interface Ex {
    @PostExchange("/sync")
    void sync();
}
