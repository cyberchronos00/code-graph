from thrift.server import TServer

from gen.calc import Calculator


class CalculatorHandler:
    def ping(self):
        return None

    def add(self, num1, num2):
        return num1 + num2

    def getStruct(self, key):
        return None


def serve(transport, tfactory, pfactory):
    handler = CalculatorHandler()
    processor = Calculator.Processor(handler)
    return TServer.TSimpleServer(processor, transport, tfactory, pfactory)
