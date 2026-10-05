import Redis from 'ioredis'

const redis = new Redis(process.env.REDIS_URL)

export async function cacheOrder(id: string, data: string) {
  await redis.set(`order:${id}`, data)
  await redis.del(`order:${id}:lock`)
}
