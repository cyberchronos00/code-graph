import { UseGuards } from '@nestjs/common';
import { Args, Mutation, Query, Resolver } from '@nestjs/graphql';

@Resolver()
export class ShelfResolver {
  @Query(() => [String])
  shelves() {
    return [];
  }

  @Mutation(() => String, { name: 'renameShelf' })
  rename(@Args('name') name: string) {
    return name;
  }
}
