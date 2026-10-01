import { CanActivate, ExecutionContext, Injectable } from '@nestjs/common';

@Injectable()
export class ThrottleGuard implements CanActivate {
  canActivate(context: ExecutionContext): boolean {
    return true;
  }
}
