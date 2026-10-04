import Bull from "bull";
import { Queue } from "bullmq";

export enum QueueName {
  Emails = "emails",
  Reports = "reports",
}

export const emails = new Queue(QueueName.Emails);

export function createQueue(name: string) {
  return new Bull(name, "redis://localhost:6379");
}

let cachedDigest: ReturnType<typeof createQueue> | undefined;
export const digestQueue = () => {
  if (!cachedDigest) {
    cachedDigest = createQueue("digest");
  }
  return cachedDigest;
};
