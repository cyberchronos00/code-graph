plugins {
    kotlin("jvm") version "1.9.24"
}

dependencies {
    implementation("org.springframework.kafka:spring-kafka:3.1.4")
    implementation("org.springframework.amqp:spring-rabbit:3.1.4")
    implementation("com.rabbitmq:amqp-client:5.21.0")
    implementation("io.nats:jnats:2.17.6")
}
