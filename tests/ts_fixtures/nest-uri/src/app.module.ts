import { Controller, Get, Module, Param, Version } from '@nestjs/common';

@Controller('authors')
export class AuthorsController {
  @Get(':id')
  findOne(@Param('id') id: string) {
    return { id };
  }

  @Version('2')
  @Get()
  listV2() {
    return [];
  }
}

@Module({ controllers: [AuthorsController] })
export class AppModule {}
