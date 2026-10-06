package shop;

import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RestController;

@RestController
public class JvApi {
    @GetMapping("/jv/items")
    public String items() {
        return "ok";
    }
}
