"""A hand-written OpenAI agent loop: schema list + dict dispatch."""
import json

from openai import OpenAI

from .models import Refund

client = OpenAI()

TOOLS = [
    {"type": "function", "function": {"name": "refund_order", "description": "Refund an order",
                                      "parameters": {"type": "object", "properties": {"order_id": {"type": "integer"}}}}},
    {"type": "function", "function": {"name": "lookup_order", "description": "Look an order up",
                                      "parameters": {"type": "object", "properties": {"order_id": {"type": "integer"}}}}},
    # declared to the model, but no handler below: no_receiver
    {"type": "function", "function": {"name": "escalate", "description": "Hand over to a human",
                                      "parameters": {"type": "object", "properties": {}}}},
]


def refund_order(order_id: int, amount: int = 0):
    """Refund an order."""
    Refund.objects.create(order_id=order_id, amount=amount)
    return "refunded"


def lookup_order(order_id: int):
    return {"id": order_id}


TOOL_HANDLERS = {"refund_order": refund_order, "lookup_order": lookup_order}


def run_agent(prompt: str):
    messages = [{"role": "user", "content": prompt}]
    resp = client.chat.completions.create(model="gpt-4o-mini", messages=messages, tools=TOOLS)
    for call in resp.choices[0].message.tool_calls or []:
        handler = TOOL_HANDLERS[call.function.name]
        handler(**json.loads(call.function.arguments))
    return resp


def run_plugin_tool(tool_call):
    # the name is only known at runtime: reported as dynamic dispatch, not linked
    return globals()[tool_call.function.name](**json.loads(tool_call.function.arguments))
