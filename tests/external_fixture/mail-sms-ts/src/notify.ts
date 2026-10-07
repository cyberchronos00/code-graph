import sgMail from '@sendgrid/mail'
import formData from 'form-data'
import Mailgun from 'mailgun.js'
import { ServerClient } from 'postmark'
import { Resend } from 'resend'
import twilio from 'twilio'
import { Vonage } from '@vonage/server-sdk'

sgMail.setApiKey(process.env.SENDGRID_API_KEY as string)

export async function viaSendgrid(to: string) {
  await sgMail.send({ to, from: 'shop@bookstore.example', subject: 'Hi', text: 'Hello' })
}

export async function viaMailgun(to: string) {
  const mg = new Mailgun(formData).client({ username: 'api', key: process.env.MAILGUN_API_KEY as string })
  await mg.messages.create('mg.bookstore.example', { to, from: 'shop@bookstore.example', text: 'Hello' })
}

export async function viaPostmark(to: string) {
  const client = new ServerClient('abcdef01-2345-6789-abcd-ef0123456789')
  await client.sendEmail({ From: 'shop@bookstore.example', To: to, Subject: 'Hi', TextBody: 'Hello' })
}

export async function viaResend(to: string) {
  const resend = new Resend(process.env.RESEND_API_KEY)
  await resend.emails.send({ from: 'shop@bookstore.example', to, subject: 'Hi', text: 'Hello' })
}

export async function sms(to: string) {
  const client = twilio(process.env.TWILIO_ACCOUNT_SID, process.env.TWILIO_AUTH_TOKEN)
  await client.messages.create({ to, from: '+15550001', body: 'Shipped' })
}

export async function vonageSms(to: string) {
  const vonage = new Vonage({ apiKey: process.env.VONAGE_API_KEY as string, apiSecret: process.env.VONAGE_API_SECRET as string })
  await vonage.sms.send({ to, from: 'Shop', text: 'Shipped' })
}

export async function unknownKey(to: string, key: string) {
  const client = new Resend(undefined)
  await client.emails.send({ from: 'a@bookstore.example', to, subject: 'x', text: 'y' })
}
