import express from "express";
import { digestQueue, emails } from "./queues";

const app = express();

app.post("/signup", async (req, res) => {
  await emails.add("welcome", { user: req.body.user });
  res.json({ ok: true });
});

app.post("/digest", async (_req, res) => {
  await digestQueue().add({ day: 1 });
  res.json({ ok: true });
});

app.listen(3000);
