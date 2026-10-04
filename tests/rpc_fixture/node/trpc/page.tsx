import { api, trpc } from "./client";

export function Posts() {
  const posts = api.post.all.useQuery();
  const create = api.post.create.useMutation();
  return [posts, create];
}

export function prefetchStats() {
  return trpc.admin.stats.queryOptions();
}

export async function serverPing() {
  // a server-side caller
  return api.ping();
}

export function notAProcedure() {
  return api.post.missing.useQuery();
}
