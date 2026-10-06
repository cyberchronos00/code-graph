package shop;

import org.junit.jupiter.api.Test;
import org.springframework.test.web.reactive.server.WebTestClient;
import org.springframework.boot.test.web.client.TestRestTemplate;
import org.springframework.test.web.servlet.MockMvc;

import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;

public class HttpTests {
    MockMvc mvc;
    WebTestClient client;
    TestRestTemplate tpl;

    @Test
    void mockMvc() throws Exception {
        mvc.perform(get("/kt/items"));
    }

    @Test
    void webClient() {
        client.get().uri("/jv/items").exchange();
    }

    @Test
    void restTemplate() {
        tpl.getForObject("/jv/items", String.class);
    }
}
