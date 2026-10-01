#pragma once

#include <memory>
#include <vector>

#include "bus/handler.hpp"

namespace bus {

class Bus {
public:
    void subscribe(std::shared_ptr<Handler> h);
    std::size_t publish(const Event& e);
#ifdef BUS_WITH_METRICS
    long published() const { return published_; }
#endif

private:
    std::vector<std::shared_ptr<Handler>> handlers_;
#ifdef BUS_WITH_METRICS
    long published_ = 0;
#endif
};

/// Builds a bus with the default handlers.
std::unique_ptr<Bus> make_default_bus();

}  // namespace bus
