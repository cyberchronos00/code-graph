#include <cassert>
#include <memory>

#include "bus/bus.hpp"

int main() {
    bus::Bus b;
    auto counter = std::make_shared<bus::CounterHandler>();
    b.subscribe(counter);
    b.publish(bus::Event{"orders", "1"});
    b.publish(bus::Event{"internal", "x"});
    assert(counter->count() == 1);
    return 0;
}
