#include "bus/handler.hpp"

namespace bus {

bool CounterHandler::accepts(const Event& e) const { return e.topic != "internal"; }

void CounterHandler::handle(const Event& e) {
    (void)e;
    ++count_;
}

}  // namespace bus
