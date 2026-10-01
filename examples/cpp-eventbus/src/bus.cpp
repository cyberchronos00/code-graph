#include "bus/bus.hpp"

#include <cstdlib>

namespace bus {

namespace {
bool verbose_from_env() {
    const char* v = std::getenv("BUS_VERBOSE");
    return v != nullptr && v[0] == '1';
}
}  // namespace

void Bus::subscribe(std::shared_ptr<Handler> h) { handlers_.push_back(std::move(h)); }

std::size_t Bus::publish(const Event& e) {
    std::size_t delivered = 0;
    for (auto& h : handlers_) {
        if (h->accepts(e)) {
            h->handle(e);  // virtual dispatch
            ++delivered;
        }
    }
#ifdef BUS_WITH_METRICS
    ++published_;
#endif
    return delivered;
}

std::unique_ptr<Bus> make_default_bus() {
    auto b = std::make_unique<Bus>();
    b->subscribe(std::make_shared<LogHandler>(verbose_from_env()));
    b->subscribe(std::make_shared<CounterHandler>());
    return b;
}

}  // namespace bus
