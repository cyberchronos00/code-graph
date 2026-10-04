function formatOrder(order) {
  return `${order.id}:${order.total}`;
}

exports.formatOrder = formatOrder;
