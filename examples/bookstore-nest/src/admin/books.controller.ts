import { Body, Controller, Param, Post, Put } from '@nestjs/common';
import { InjectRepository } from '@nestjs/typeorm';
import { Repository } from 'typeorm';
import { Book } from './book.entity';
import { CreateBookDto } from './dto/upsert-book.dto';

/** Mounted under /admin by RouterModule. */
@Controller('books')
export class BooksController {
  constructor(@InjectRepository(Book) private readonly books: Repository<Book>) {}

  @Post()
  create(@Body() dto: CreateBookDto) {
    return this.books.save(dto);
  }

  @Put(':id')
  update(@Param('id') id: string, @Body() dto: CreateBookDto) {
    return this.books.update(Number(id), dto);
  }
}
