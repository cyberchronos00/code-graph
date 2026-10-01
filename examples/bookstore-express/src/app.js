const express = require('express');
const { requestId } = require('./middleware/request-id');
const { notFound } = require('./middleware/not-found');

const app = express();

app.use(express.json());
app.use(requestId);
app.use('/v1', require('./routes'));

app.get('/health', (req, res) => res.json({ ok: true }));

// registered after every route: runs only when nothing matched
app.use(notFound);

module.exports = app;
