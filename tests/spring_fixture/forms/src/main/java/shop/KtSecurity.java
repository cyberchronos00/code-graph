package shop;

import org.springframework.context.annotation.Bean;
import org.springframework.core.annotation.Order;
import org.springframework.security.config.annotation.web.builders.HttpSecurity;
import org.springframework.security.web.SecurityFilterChain;

public class KtSecurity {
    @Bean
    @Order(1)
    public SecurityFilterChain ktChain(HttpSecurity http) throws Exception {
        return http.securityMatcher("/kt/**")
            .authorizeHttpRequests(a -> a.requestMatchers("/kt/**").authenticated())
            .build();
    }
}
