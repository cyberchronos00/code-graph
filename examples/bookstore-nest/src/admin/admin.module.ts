import { Module } from '@nestjs/common';
import { TypeOrmModule } from '@nestjs/typeorm';
import { Book } from './book.entity';
import { BooksController } from './books.controller';

@Module({ imports: [TypeOrmModule.forFeature([Book])], controllers: [BooksController] })
export class AdminModule {}
