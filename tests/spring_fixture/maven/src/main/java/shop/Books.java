package shop;

import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

@RestController
@RequestMapping("/books")
public class Books {
    @GetMapping("/{id:\\d+}")
    public String one() {
        return "a";
    }
}
