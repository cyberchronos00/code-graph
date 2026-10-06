package shop;

import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.beans.factory.annotation.Qualifier;
import org.springframework.context.annotation.Primary;
import org.springframework.stereotype.Service;

interface Price {}

@Service
@Qualifier("fast")
class FastPrice implements Price {}

@Service
@Qualifier("slow")
class SlowPrice implements Price {}

@Service
class Checkout {
    Checkout(@Qualifier("fast") Price price) {}
}

interface Mail {}

@Service
class Smtp implements Mail {}

@Service
@Primary
class ApiMail implements Mail {}

@Service
class Notifier {
    @Autowired
    Mail mail;
}
