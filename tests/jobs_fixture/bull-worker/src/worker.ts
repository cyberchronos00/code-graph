import Bull from "bull";
import { Job, Worker } from "bullmq";

async function sendEmail(job: Job) {
  return job.data;
}

export function startWorkers() {
  new Worker("emails", sendEmail, { connection: { host: "redis" } });
  const digest = new Bull("digest", "redis://redis:6379");
  digest.process(async (job) => job.data);
}
