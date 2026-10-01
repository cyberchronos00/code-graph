import { NestFactory } from '@nestjs/core';
import { VersioningType } from '@nestjs/common';
import { AppModule } from './app.module';
import { RequestIdInterceptor } from './common/request-id.interceptor';

async function bootstrap() {
  const app = await NestFactory.create(AppModule);
  app.setGlobalPrefix('v1', { exclude: ['health'] });
  app.enableVersioning({ type: VersioningType.HEADER, header: 'X-Api-Version' });
  app.useGlobalInterceptors(new RequestIdInterceptor());
  await app.listen(process.env.PORT ?? 3000);
}
bootstrap();
