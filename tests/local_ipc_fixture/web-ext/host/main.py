import json
import struct
import sys


def read_message():
    raw = sys.stdin.buffer.read(4)
    size = struct.unpack("=I", raw)[0]
    return json.loads(sys.stdin.buffer.read(size))


if __name__ == "__main__":
    print(read_message())
