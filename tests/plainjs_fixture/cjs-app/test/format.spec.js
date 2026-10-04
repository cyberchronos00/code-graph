const assert = require("assert");
const { formatOrder } = require("../src/format");

describe("formatOrder", () => {
  it("joins id and total", () => {
    assert.strictEqual(formatOrder({ id: 1, total: 2 }), "1:2");
  });
});
