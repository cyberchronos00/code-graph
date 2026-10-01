import { IsInt, IsString, Min } from 'class-validator';

export class ReserveStockDto {
  @IsString() sku: string;
  @IsInt() @Min(1) quantity: number;
}
