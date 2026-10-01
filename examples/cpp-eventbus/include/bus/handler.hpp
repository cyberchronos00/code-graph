#pragma once

#include "bus/event.hpp"

namespace bus {

/// Receives published events. Implementations are called through virtual dispatch.
class Handler {
public:
    virtual ~Handler() = default;
    virtual bool accepts(const Event& e) const { return !e.topic.empty(); }
    virtual void handle(const Event& e) = 0;
};

class LogHandler : public Handler {
public:
    explicit LogHandler(bool verbose) : verbose_(verbose) {}
    void handle(const Event& e) override;

private:
    bool verbose_;
};

class CounterHandler final : public Handler {
public:
    bool accepts(const Event& e) const override;
    void handle(const Event& e) override;
    long count() const { return count_; }

private:
    long count_ = 0;
};

}  // namespace bus
