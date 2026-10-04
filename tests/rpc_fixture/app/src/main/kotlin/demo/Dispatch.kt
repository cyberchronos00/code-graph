package demo

import fleet.v1.AssignReply
import fleet.v1.AssignRequest
import fleet.v1.DispatchGrpcKt
import io.grpc.ManagedChannel

class DispatchService : DispatchGrpcKt.DispatchCoroutineImplBase() {
    override suspend fun assign(request: AssignRequest): AssignReply = AssignReply.getDefaultInstance()
}

class FleetClient(channel: ManagedChannel) {
    private val stub = DispatchGrpcKt.DispatchCoroutineStub(channel)

    suspend fun send(request: AssignRequest): AssignReply = stub.assign(request)
}
