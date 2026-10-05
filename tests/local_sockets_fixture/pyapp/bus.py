from dbus_next.aio import MessageBus
from dbus_next.service import ServiceInterface, method, signal


class Notifier(ServiceInterface):
    def __init__(self):
        super().__init__("org.example.Notifier1")

    @method()
    def Notify(self, text: "s") -> "u":
        return 1

    @signal()
    def Closed(self) -> "u":
        return 1


async def notify(text):
    bus = await MessageBus().connect()
    introspection = await bus.introspect("org.example.Notifier", "/org/example/Notifier")
    proxy = bus.get_proxy_object("org.example.Notifier", "/org/example/Notifier", introspection)
    iface = proxy.get_interface("org.example.Notifier1")
    return await iface.call_notify(text)
