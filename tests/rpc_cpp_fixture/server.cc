#include <grpcpp/grpcpp.h>

#include "route_guide.grpc.pb.h"

using routeguide::RouteGuide;

class RouteGuideImpl final : public RouteGuide::Service {
 public:
  grpc::Status GetFeature(grpc::ServerContext* context, const routeguide::Point* point,
                          routeguide::Feature* feature) override;

  grpc::Status ListFeatures(grpc::ServerContext* context, const routeguide::Rectangle* rect,
                            grpc::ServerWriter<routeguide::Feature>* writer) override {
    return grpc::Status::OK;
  }
};

grpc::Status RouteGuideImpl::GetFeature(grpc::ServerContext* context, const routeguide::Point* point,
                                        routeguide::Feature* feature) {
  return grpc::Status::OK;
}

int main() {
  RouteGuideImpl service;
  grpc::ServerBuilder builder;
  builder.RegisterService(&service);
  return 0;
}
