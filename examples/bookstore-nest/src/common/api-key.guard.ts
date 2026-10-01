import { CanActivate, ExecutionContext, Injectable } from '@nestjs/common';
import { ConfigService } from '@nestjs/config';

/** Accepts requests that carry the partner API key. */
@Injectable()
export class ApiKeyGuard implements CanActivate {
  constructor(private readonly config: ConfigService) {}

  canActivate(context: ExecutionContext): boolean {
    const expected = this.config.get<string>('PARTNER_API_KEY');
    return context.switchToHttp().getRequest().headers['x-api-key'] === expected;
  }
}
