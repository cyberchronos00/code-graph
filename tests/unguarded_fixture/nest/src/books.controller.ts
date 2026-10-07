import { Body, Controller, Get, Post } from '@nestjs/common'
import { Public } from './public.decorator'

@Controller('books')
export class BooksController {
  @Get()
  list() {
    return []
  }

  @Public()
  @Get('featured')
  featured() {
    return []
  }

  @Post()
  create(@Body() body: { title: string }) {
    return body
  }
}
