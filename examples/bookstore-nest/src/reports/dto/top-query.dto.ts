import { IsOptional, IsString } from 'class-validator';

export class TopQueryDto {
  @IsString() mode: string;
  @IsOptional() @IsString() date_from?: string;
  @IsOptional() @IsString() category_id?: string;
}
