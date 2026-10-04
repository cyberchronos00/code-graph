import { ping } from "./health";
import { postRouter as posts } from "./post";
import { createTRPCRouter, publicProcedure } from "./trpc";

export const appRouter = createTRPCRouter({
  post: posts,
  ping,
  admin: createTRPCRouter({
    stats: publicProcedure.query(() => ({ users: 0 })),
  }),
});

export type AppRouter = typeof appRouter;
