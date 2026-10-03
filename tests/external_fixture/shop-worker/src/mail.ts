export function mailer() {
  return { host: process.env.SMTP_HOST, port: Number(process.env.SMTP_PORT), pass: process.env.SMTP_PASSWORD }
}
