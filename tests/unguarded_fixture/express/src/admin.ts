import express from 'express'

const admin = express()

function requireStaffLogin(req, res, next) {
  if (!req.session.staffId) return res.sendStatus(401)
  next()
}

admin.use('/admin', requireStaffLogin)

admin.get('/admin/orders', (req, res) => res.json([]))
admin.get('/shelf-notes', (req, res) => res.json([]))

admin.listen(3001)
