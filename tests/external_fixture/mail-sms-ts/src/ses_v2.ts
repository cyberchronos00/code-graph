import { SESv2Client, SendEmailCommand, CreateEmailIdentityCommand } from '@aws-sdk/client-sesv2'
import nodemailer from 'nodemailer'
import { resendTransport } from 'nodemailer-resend'

const ses = new SESv2Client({ region: 'eu-west-1' })

export async function sesv2Send(to: string) {
  await ses.send(new SendEmailCommand({ FromEmailAddress: 'shop@bookstore.example', Destination: { ToAddresses: [to] }, Content: { Simple: { Subject: { Data: 'Hi' }, Body: { Text: { Data: 'Hello' } } } } }))
}

export async function sesv2Identity(domain: string) {
  await ses.send(new CreateEmailIdentityCommand({ EmailIdentity: domain }))
}

const sesTransport = nodemailer.createTransport({ SES: { ses, aws: { SendEmailCommand } } })

export async function nodemailerSes(to: string) {
  await sesTransport.sendMail({ from: 'shop@bookstore.example', to, subject: 'Hi', text: 'Hello' })
}

const resendMailer = nodemailer.createTransport(resendTransport({ apiKey: process.env.RESEND_API_KEY }))

export async function nodemailerResend(to: string) {
  await resendMailer.sendMail({ from: 'shop@bookstore.example', to, subject: 'Hi', text: 'Hello' })
}

const smtpResend = nodemailer.createTransport({ host: 'smtp.resend.com', port: 465, secure: true, auth: { user: 'resend', pass: process.env.RESEND_API_KEY } })

export async function nodemailerSmtpResend(to: string) {
  await smtpResend.sendMail({ from: 'shop@bookstore.example', to, subject: 'Hi', text: 'Hello' })
}

const plain = nodemailer.createTransport({ host: 'smtp.bookstore.example', port: 587 })

export async function nodemailerPlainSmtp(to: string) {
  await plain.sendMail({ from: 'shop@bookstore.example', to, subject: 'Hi', text: 'Hello' })
}
