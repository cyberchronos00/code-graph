import { IsNumber, IsOptional, IsString } from 'class-validator';

export class CreateBookDto {
  @IsString() title: string;
  @IsNumber() price: number;
  @IsOptional() @IsNumber() stock?: number;
}
