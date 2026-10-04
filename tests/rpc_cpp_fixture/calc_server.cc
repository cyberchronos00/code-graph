#include <thrift/server/TSimpleServer.h>

#include "Calculator.h"

using namespace calc;

class CalculatorHandler : virtual public CalculatorIf {
 public:
  void ping() override {}

  int32_t add(const int32_t num1, const int32_t num2) override { return num1 + num2; }

  void getStruct(shared::SharedStruct& ret, const int32_t key) override {}
};
