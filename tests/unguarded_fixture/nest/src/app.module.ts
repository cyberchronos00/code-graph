import { Module } from '@nestjs/common'
import { APP_GUARD } from '@nestjs/core'
import { BooksController } from './books.controller'
import { JwtAuthGuard } from './jwt-auth.guard'

@Module({
  controllers: [BooksController],
  providers: [{ provide: APP_GUARD, useClass: JwtAuthGuard }],
})
export class AppModule {}
