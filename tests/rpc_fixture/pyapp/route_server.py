from concurrent import futures

import grpc

from protos import route_guide_pb2, route_guide_pb2_grpc


class RouteGuideServicer(route_guide_pb2_grpc.RouteGuideServicer):
    def GetFeature(self, request, context):
        return route_guide_pb2.Feature(name="here")

    def ListFeatures(self, request, context):
        yield route_guide_pb2.Feature(name="a")

    def describe(self):
        return "not an rpc"


def serve():
    server = grpc.server(futures.ThreadPoolExecutor(max_workers=4))
    route_guide_pb2_grpc.add_RouteGuideServicer_to_server(RouteGuideServicer(), server)
    server.add_insecure_port("[::]:50051")
    server.start()
