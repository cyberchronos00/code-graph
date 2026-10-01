#include <iostream>

#include "bus/handler.hpp"

namespace bus {

void LogHandler::handle(const Event& e) {
    if (verbose_) {
        std::cerr << "[" << e.topic << "] " << e.payload << "\n";
    }
}

}  // namespace bus
