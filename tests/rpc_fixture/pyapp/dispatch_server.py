import grpc

from protos.fleet import fleet_pb2, fleet_pb2_grpc


class Dispatcher:
    """Registered by variable, without the generated base class."""

    def Assign(self, request, context):
        return fleet_pb2.AssignReply(driver="d1")


def serve():
    server = grpc.server(None)
    svc = Dispatcher()
    fleet_pb2_grpc.add_DispatchServicer_to_server(svc, server)
    return server
