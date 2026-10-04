import grpc

from protos.fleet import fleet_pb2_grpc


def lookup(stop):
    channel = grpc.insecure_channel("fleet:443")
    guide = fleet_pb2_grpc.RouteGuideStub(channel)
    return guide.GetFeature(stop)
