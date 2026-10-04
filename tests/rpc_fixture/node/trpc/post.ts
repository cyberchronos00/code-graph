import type { TRPCRouterRecord } from "@trpc/server";

import { publicProcedure } from "./trpc";

function loadAll() {
  return [];
}

export const postRouter = {
  all: publicProcedure.query(() => {
    return loadAll();
  }),
  create: publicProcedure
    .input((v: unknown) => v as { name: string })
    .mutation(({ input }) => {
      return { id: 1, name: input.name };
    }),
} satisfies TRPCRouterRecord;
