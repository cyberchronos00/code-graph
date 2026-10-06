package shop

import org.springframework.context.annotation.Bean
import org.springframework.core.annotation.Order
import org.springframework.security.config.annotation.web.builders.HttpSecurity
import org.springframework.security.web.SecurityFilterChain
import org.springframework.web.bind.annotation.GetMapping
import org.springframework.web.bind.annotation.RestController

@RestController
class KtApi {
    @GetMapping("/kt/items")
    fun items() = "ok"
}

class JvSecurity {
    @Bean
    @Order(2)
    fun jvChain(http: HttpSecurity): SecurityFilterChain {
        http.securityMatcher("/jv/**")
        http.authorizeHttpRequests {
            it.requestMatchers("/jv/**").hasRole("ADMIN")
        }
        return http.build()
    }
}
