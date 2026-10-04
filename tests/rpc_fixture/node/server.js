const grpc = require("@grpc/grpc-js");
const protoLoader = require("@grpc/proto-loader");

const definition = protoLoader.loadSync("protos/route_guide.proto");
const routeguide = grpc.loadPackageDefinition(definition).routeguide;

function getFeature(call, callback) {
  callback(null, { name: "here" });
}

function routeChat(call) {
  call.on("data", (note) => call.write(note));
}

function main() {
  const server = new grpc.Server();
  server.addService(routeguide.RouteGuide.service, { getFeature: getFeature, routeChat });
  server.bindAsync("0.0.0.0:50051", grpc.ServerCredentials.createInsecure(), () => server.start());
}

main();
