// busd: reads "topic payload" lines from stdin and publishes them.
#include <iostream>
#include <string>

#include "bus/bus.hpp"

int main() {
    auto b = bus::make_default_bus();
    std::string topic, payload;
    while (std::cin >> topic && std::getline(std::cin, payload)) {
        b->publish(bus::Event{topic, payload});
    }
#ifdef BUS_WITH_METRICS
    std::cout << "published " << b->published() << "\n";
#endif
    return 0;
}
