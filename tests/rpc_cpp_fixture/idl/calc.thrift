include "shared.thrift"

namespace py calc

exception InvalidOperation {
  1: i32 whatOp,
  2: string why
}

# Inherits getStruct from shared.SharedService.
service Calculator extends shared.SharedService {
  void ping(),
  i32 add(1: i32 num1, 2: i32 num2),
  i32 divide(1: i32 a, 2: i32 b) throws (1: InvalidOperation ouch),
  oneway void zip()
  // i32 retired(1: i32 x)
}
