#pragma once

#include <string>

namespace bus {

struct Event {
    std::string topic;
    std::string payload;
};

}  // namespace bus
