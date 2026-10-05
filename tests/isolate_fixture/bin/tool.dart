import 'dart:isolate';

void main(List<String> args, SendPort reply) {
  reply.send(args.length);
}
