import grpc

from protos import route_guide_pb2, route_guide_pb2_grpc
from protos.fleet import fleet_pb2_grpc


def get_one(stub, point):
    return stub.GetFeature(point)


def run():
    with grpc.insecure_channel("localhost:50051") as channel:
        stub = route_guide_pb2_grpc.RouteGuideStub(channel)
        get_one(stub, route_guide_pb2.Point(latitude=1))
        for feature in stub.ListFeatures(route_guide_pb2.Rectangle()):
            print(feature)
        stub.RouteChat(iter([]))


def assign(channel, ride):
    dispatch = fleet_pb2_grpc.DispatchStub(channel)
    return dispatch.Assign(ride)
