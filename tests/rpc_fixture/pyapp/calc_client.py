from thrift.protocol import TBinaryProtocol

from gen.calc import Calculator


def run(transport):
    protocol = TBinaryProtocol.TBinaryProtocol(transport)
    client = Calculator.Client(protocol)
    client.ping()
    total = client.add(1, 2)
    return total, client.getStruct(1)
