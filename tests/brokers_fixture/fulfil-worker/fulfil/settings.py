import os

ORDERS_TOPIC = "orders.created"
AUDIT_TOPIC = os.getenv("AUDIT_TOPIC", "audit.orders")
