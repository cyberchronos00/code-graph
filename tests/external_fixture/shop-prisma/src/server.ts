import express from 'express'
import { PrismaClient } from '@prisma/client'

const prisma = new PrismaClient()
const app = express()

export async function listOrders() {
  return prisma.order.findMany()
}

app.get('/orders', async (_req, res) => {
  res.json(await listOrders())
})

app.listen(3000)
