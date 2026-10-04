#include "Calculator.h"

using namespace calc;

int ask(std::shared_ptr<apache::thrift::protocol::TProtocol> protocol) {
  CalculatorClient client(protocol);
  client.ping();
  return client.add(1, 2);
}
