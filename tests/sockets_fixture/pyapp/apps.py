"""UDP application protocols: mDNS advertise / browse, OSC, CoAP, SSDP."""
import aiocoap
import aiocoap.resource as resource
from aiocoap import GET, Message
from pythonosc import dispatcher, osc_server, udp_client
from ssdpy import SSDPClient, SSDPServer
from zeroconf import ServiceBrowser, ServiceInfo, Zeroconf

SERVICE = "_printer._tcp.local."


def advertise(zc: Zeroconf):
    info = ServiceInfo(SERVICE, "Office._printer._tcp.local.", port=631, addresses=[b"\x7f\x00\x00\x01"])
    zc.register_service(info)


def browse(zc: Zeroconf):
    return ServiceBrowser(zc, ["_printer._tcp.local.", "_http._tcp.local."], handlers=[on_change])


def on_change(zeroconf, service_type, name, state_change):
    return name


def volume(address, *args):
    return args


def osc_receiver():
    d = dispatcher.Dispatcher()
    d.map("/mixer/volume", volume)
    osc_server.ThreadingOSCUDPServer(("0.0.0.0", 9000), d).serve_forever()


def osc_sender():
    client = udp_client.SimpleUDPClient("127.0.0.1", 9000)
    client.send_message("/mixer/volume", 0.5)


class TimeResource(resource.Resource):
    async def render_get(self, request):
        return aiocoap.Message(payload=b"now")


def coap_server():
    root = resource.Site()
    root.add_resource(["time"], TimeResource())
    return root


async def coap_client(ctx):
    return await ctx.request(Message(code=GET, uri="coap://localhost/time")).response


def ssdp_advertise():
    SSDPServer("lamp", device_type="urn:schemas-upnp-org:device:BinaryLight:1").serve_forever()


def ssdp_search():
    return SSDPClient().m_search("urn:schemas-upnp-org:device:BinaryLight:1")
