package facts

import org.springframework.context.annotation.Bean
import org.springframework.core.annotation.Order
import org.springframework.security.config.annotation.web.builders.HttpSecurity
import org.springframework.security.config.annotation.web.invoke
import org.springframework.security.web.SecurityFilterChain
import org.springframework.security.web.util.matcher.RequestMatcher
import org.springframework.web.bind.annotation.GetMapping
import org.springframework.web.bind.annotation.RestController

@RestController
class ApiV2Controller {
    @GetMapping("/v2/items")
    fun items(): String = "i"

    @GetMapping("/v2/ops/stats")
    fun stats(): String = "s"

    @GetMapping("/hooks/ping")
    fun hook(): String = "p"
}

// several chains (#99): the first one, by @Order, whose securityMatcher matches the request applies
class ApiSecurityConfig {
    @Bean
    @Order(1)
    fun apiChain(http: HttpSecurity): SecurityFilterChain {
        http {
            securityMatcher("/v2/**")
            authorizeHttpRequests {
                authorize("/v2/ops/**", hasRole("OPS"))
                authorize(anyRequest, hasAuthority("SCOPE_api"))
            }
        }
        return http.build()
    }

    @Bean
    @Order(2)
    fun hookChain(http: HttpSecurity, hooks: RequestMatcher): SecurityFilterChain {
        http.securityMatcher(hooks).authorizeHttpRequests { it.anyRequest().denyAll() }
        return http.build()
    }
}
