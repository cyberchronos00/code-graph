# External systems

A Postgres server, Redis, SMTP, LDAP, SFTP or S3 bucket is one node. The address usually lives
in configuration, not in the call.

```
code -CONNECTS_TO-> external:<protocol>:<target>
external -CONFIGURED_BY-> env:<KEY> | config:<key>
external -CREDENTIAL_FROM-> env:<KEY>          # where the secret is, never its value
```

`target` is `host:port` when a literal, a DSN, `.env.example` or a compose service names it;
`config:<module>.<setting>` for a settings dict aimed at the local machine; otherwise
`env:<KEY>`. After `cg link`, two repos that name the same address share the node.

| source | what becomes a node |
|---|---|
| Laravel `config/database.php` | the default connection, connections models use, custom connections. Unused framework templates are skipped |
| `READS_ENV` prefixes | `DB_*`, `DATABASE_URL`, `REDIS_*`, `MAIL_*` (only when the mailer is smtp), `MONGO*`, `AMQP_*`, `LDAP_*`, `S3_*`, `KAFKA_*`, … |
| Python `*settings*` / `*config*` | Django `DATABASES` / `CACHES`, `DATABASE_URL`, `CELERY_BROKER_URL`. sqlite and local-memory caches are not network systems |
| `.env.example` and compose | non-secret values; compose services with a known image (postgres, redis, kafka broker images, …). A broker no client mentions is still listed, with no CONNECTS_TO |
| client constructors | Python (`psycopg`, `redis`, `pymongo`, `pika`, `boto3`, …) and Node (`pg`, `ioredis`, `mongoose`, `amqplib`, `knex`, …). A localhost default is not a system |
| ORM datasources | Prisma `datasource`, TypeORM `DataSource`, Drizzle `dbCredentials`, Laravel tables on `$connection`, PHP `new PDO`, Spring `spring.datasource.url`. sqlite / H2 / HSQLDB / Derby are skipped. The one SQL system in a repo is attached to ORM tables that name none (`via: sole <protocol> system`) |
| Redis / Elasticsearch | literal key prefixes and `KEY_PREFIX` / `ELASTICSEARCH_INDEX_PREFIX` on `attrs.key_prefixes` / `attrs.indices` |
| HTTP and SDKs | `external:http(s):<host>:<port>` for a real other-origin host; `external:s3:<bucket>`, `gcs`, `azure-blob`, `aws:<service>`, `saas:stripe`, `llm:<provider>`. Loopback and `${host}` stay unattached. Test-only callers are left out |
| Laravel disks / django-storages | `s3` / `gcs` / `azure` disks. `Storage::disk` and `default_storage.save` connect to that disk |

Confidence is `exact` for a literal host, `resolved` through `.env.example` or compose,
`heuristic` otherwise. `DB_*` without a driver takes the one SQL client in package.json /
requirements / pyproject, or the compose service. A Node env wrapper (`env.X`,
`EnvSchema.safeParse(process.env)`, Joi `validate(process.env)`) is READS_ENV with `via`
`env wrapper` / `env schema`. `process.env.X =` is a write, not a read.

DSNs cover postgres(ql), mysql / mariadb, mssql, oracle, mongodb(+srv), redis / rediss, amqp(s),
smtp(s), ldap(s), sftp / ssh, ftp(s), elasticsearch, nats, kafka (`bootstrap_servers`),
mqtt(s), imap, clickhouse, cassandra, http(s) and `jdbc:`. Default ports come from the scheme (
`amqps` 5671, `smtps` 465, `ldaps` 636). TLS is true for TLS schemes, `sslmode=require` /
`ssl=true` and ports 465 / 636 / 993; false for plaintext schemes; unknown otherwise.

Python constructors include `psycopg` / `asyncpg`, `pymysql`, `redis.from_url`, `pymongo`,
`smtplib`, `ldap3`, `pika` / `kombu`, `aiokafka` / `confluent_kafka`, `elasticsearch`,
`boto3.client("s3")`, and a client built bare then `connect()` 'd (`paramiko`, `ftplib`,
`SMTP.connect` in another method). Node constructors include `pg` `Pool`, `ioredis` (including
a project subclass and `super()`), `mysql2`, `mongoose`, `amqplib`, `new Kafka({brokers})`,
`Sequelize`, `knex`, `ldapjs`, `S3Client`, `ssh2`, `basic-ftp`. sqlite dialects are not
systems.

Node attrs: `protocol`, `host`, `port`, `tls`, `resource` (database, bucket),
`address_source` (literal, env-example, compose, config, env), `credential_source`, `auth` (
`ambient` or `explicit`).

The graph never stores a secret. A DSN password becomes `***` before anything else sees it.
`cg external --source literal` lists hard-coded addresses or credentials.

```bash
cg external --db graph.db
cg external --protocol redis --tls-off --db graph.db
cg external 'external:postgres:*' --json --db graph.db
```

Each system lists its tables, the functions that use it, CONFIGURED_BY / CREDENTIAL_FROM, and
TLS. MCP: `external_systems(pattern?, protocol?, source?, tls_off?)`. `reaches` and `impact` on
`external:…`, `table:…` and `column:orders.total` walk CONNECTS_TO, USES_CONNECTION,
READS_TABLE / WRITES_TABLE and column edges.

`impact` on a database reaches model users through the tables stored in it (ORM `CONNECTS_TO`,
`op = table`). A table's impact also covers code that reads or writes only some of its columns
(`READS_COLUMN` / `WRITES_COLUMN` on the columns it `CONTAINS`). `cg link` still skips
`origin_kind: other` when matching routes; the `external:http(s)` nodes are not routes. An SDK
endpoint URL is `attrs.endpoint` on the resource node and is not also an `external:http(s)`
node. Explicit keys are `CREDENTIAL_FROM`; a default credential chain is `auth=ambient`.
Callers that are already test nodes are left out.

## Not covered yet

- Node clients built from a caller-supplied options object, or from a template string.
- Spring `application.yml` beyond `spring.datasource.url`, Rails `database.yml`, Helm and
  Terraform values, compose files outside the root.
- Mail, SMS, push and the Kubernetes API. Broker pairing is [protocols.md](protocols.md).
- A Prisma schema in a sibling workspace package is not found from the app.

Corpus notes: [validation-log.md](validation-log.md#external-systems-40).
