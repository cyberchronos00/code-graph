#include <grpcpp/grpcpp.h>

#include "route_guide.grpc.pb.h"

using routeguide::RouteGuide;

class RouteGuideClient {
 public:
  explicit RouteGuideClient(std::shared_ptr<grpc::Channel> channel) : stub_(RouteGuide::NewStub(channel)) {}

  void Lookup() {
    grpc::ClientContext context;
    routeguide::Feature feature;
    stub_->GetFeature(&context, routeguide::Point(), &feature);
  }

  void Chat() {
    grpc::ClientContext context;
    auto stream = stub_->RouteChat(&context);
  }

 private:
  std::unique_ptr<RouteGuide::Stub> stub_;
};

int main() {
  RouteGuideClient guide(grpc::CreateChannel("localhost:50051", grpc::InsecureChannelCredentials()));
  guide.Lookup();
  return 0;
}
