# External systems: databases, caches, brokers, mail, directories, file transfer

Code talks to systems outside the repository: a Postgres server, a Redis cache, an SMTP relay, an LDAP directory, an
SFTP host, an S3 bucket. The address usually lives in configuration (`DB_HOST`, `DATABASE_URL`, `settings.DATABASES`,
a docker-compose service), not in the call, so without this layer the graph ends at "reads env DB_HOST" and nobody
can ask "what talks to the reporting database" or "which systems are reached without TLS". cg models each system as
one node:

```
code / connection:<name> / settings module -CONNECTS_TO-> external:<protocol>:<target>
external:<protocol>:<target> -CONFIGURED_BY-> env:<KEY> | config:<key>
external:<protocol>:<target> -CREDENTIAL_FROM-> env:<KEY>          (location of the secret, never its value)
```

`target` is `host:port` when the address is known (a literal, a DSN, an `.env.example` value or a docker-compose
service), `config:<module>.<setting>` for a settings dict that points at the local machine, otherwise `env:<KEY>`
(the key the code reads). Two repositories pointing at the same address share the node after `cg link`, so
`reaches external:postgres:db:5432` lists the entry points of both.

## Sources

| Source | What is read | Confidence |
|---|---|---|
| Laravel `config/database.php` connections | the default connection (`DB_CONNECTION` in `.env.example` or the config default), connections used by models / `DB::connection()` (USES_CONNECTION) and custom connections; the framework's unused template connections are skipped. CONNECTS_TO connection → external (op query), CONFIGURED_BY to the config / env node, CREDENTIAL_FROM the password key | exact for a literal host, resolved through `.env.example` / compose, heuristic otherwise |
| Env keys read by code (`READS_ENV`) | keys grouped by prefix (`DB_*`, `DATABASE_URL`, `POSTGRES_*`, `REDIS_*`, `MAIL_*` / `SMTP_*`, `MONGO*`, `RABBITMQ_*`, `AMQP_*`, `LDAP_*`, `SFTP_*`, `S3_*`, `ELASTICSEARCH_*`, `KAFKA_*` ...): the URL / HOST key gives the address, PORT the port, PASSWORD / SECRET keys the credential. Every code reader gets CONNECTS_TO (op connect). `MAIL_*` counts only when the mailer is smtp; `DB_*` without a driver or scheme takes the one SQL client in the dependencies (package.json, requirements, pyproject) or compose | as above |
| Python settings | module-level dicts in `*settings*` / `*config*` modules: Django `DATABASES` / `CACHES` (`ENGINE` / `BACKEND` gives the protocol; sqlite, local-memory and file caches are no network system), NetBox-style `DATABASE` / `REDIS` with aliases, values from literals, `os.environ.get("K", default)`, `os.getenv`, `os.environ["K"]`, django-environ `env("K")` and other module dicts; URL settings `DATABASE_URL`, `CELERY_BROKER_URL`, `BROKER_URL`, `REDIS_URL`, `CACHE_URL`, `EMAIL_HOST` | exact / resolved / heuristic |
| `.env.example`, `.env.sample`, `.env.dist`, `.env.template`, `example.env` | values of non-secret keys; secret keys keep only their location; a DSN password becomes `***` before anything else sees it | |
| `docker-compose*.yml`, `compose*.yaml` | services with a known image (postgres, mysql / mariadb, redis / valkey, mongo, rabbitmq, elasticsearch / opensearch, memcached, minio, mailpit / mailhog, openldap, nats, kafka, mosquitto, clickhouse, cassandra): a host equal to the service name resolves to `service:port` with `deployment_name` and `image` | resolved |
| Python client constructors | `psycopg` / `psycopg2` / `asyncpg` / `pg8000` `connect(host=, port=, dbname=)` or a DSN, `pymysql` / `MySQLdb` / `mysql.connector`, `redis.Redis(host=)` / `redis.from_url(url)`, `pymongo.MongoClient(url)`, `smtplib.SMTP(host, port)` / `SMTP_SSL` (TLS, 465), `ftplib.FTP` / `FTP_TLS`, `ldap3.Server(host)`, `pika` / `aio_pika` / `kombu`, `elasticsearch`, `pymemcache`, `boto3.client("s3", endpoint_url=)`: literal or env-read arguments; CONNECTS_TO from the calling function (op connect, `client` attr); a client left at its localhost default is no system | exact for a literal, otherwise as above |
| Model calls (`attrs.llm_calls` from [AI tools](ai-tools.md)) | `external:llm:<provider>` (openai, anthropic, azure-openai, ollama ...) with the `models` called, or `external:llm:<host>:<port>` for a non-local `base_url`; CONNECTS_TO from the calling function, CREDENTIAL_FROM the provider's API-key env var when code reads it; calls from test code are left out | resolved / exact |
| Third-party HTTP (`http` nodes with origin_kind other) | shown by `cg external` as `external:https:<host>:<port>` through an adapter, without extra nodes; origins only called from tests are left out | exact |

DSNs: postgres(ql) / pgsql, mysql / mariadb, mssql / sqlserver, oracle, mongodb(+srv), redis / rediss / valkey, amqp(s),
smtp(s), ldap(s), sftp / ssh / scp, ftp(s), s3, memcached, elasticsearch, nats, kafka, mqtt(s), imap(s), pop3(s),
clickhouse, cassandra, http(s) and `jdbc:` prefixes. Default ports come from the protocol (or the TLS scheme:
`amqps` 5671, `smtps` 465, `ldaps` 636 ...). TLS: true for TLS schemes, `sslmode=require` / `ssl=true` / `tls=true`
and TLS ports (465, 636, 993 ...), false for the plaintext schemes, unknown otherwise.

Node attrs: `protocol`, `target`, `host`, `port`, `confidence`, `address_source` (literal | env-example | compose |
config | env | code-default) and `address_at`, `address_default` (the loopback / fallback address behind an
`env:` or `config:` target), `deployment_name` / `image` / `compose_file`, `scheme`, `resource` (database, vhost,
bucket), `user`, `tls`, `credential_source` (env | literal) and `credential_at` (a key or `file:line`), `library` /
`connection` (Laravel), `setting` (Python), `protocol_source` (dependencies).

## Secrets

The graph never stores a secret value: passwords, tokens and the password part of a DSN are dropped when the files
are read. What is kept is where the credential comes from (`env:DB_PASSWORD`, or `config/database.php:42` for a
literal), so "which systems use a hard-coded password" is a query (`cg external --source literal`), and the test
suite checks that the fixture secrets do not appear anywhere in the database file.

## `cg external`

```
cg external --db graph.db                     # every system with its users, address and credential source
cg external --db graph.db --protocol redis    # one protocol
cg external --db graph.db --source literal    # hard-coded addresses or credentials
cg external --db graph.db --tls-off           # systems known to be reached without TLS
cg external 'external:postgres:*' --db graph.db --json
```

Per system: the code and connections using it (with file:line and how: connection, env key, setting), the entry
kinds reaching those users, CONFIGURED_BY / CREDENTIAL_FROM keys, TLS. MCP: `external_systems(pattern?, protocol?,
source?, tls_off?)`. `reaches external:...` lists the entry points that reach a system, and `impact external:...` /
`impact table:...` lists the code using it (CONNECTS_TO / USES_CONNECTION / READS_TABLE / WRITES_TABLE /
MAPS_TO_TABLE as the first hop, then callers) with the entry points above it.

## Not covered yet

- Client constructors in Node (`new Pool({...})`, `new Redis(...)`, `nodemailer.createTransport`, `mongoose.connect`)
  and PHP (`new PDO($dsn)`), and `paramiko.SSHClient().connect()` (a method on an instance): the address must come
  from configuration.
- Config schemas that read the environment indirectly (zod / class-validator env DTOs, `environment.X` wrappers)
  give no env nodes, so their keys are not grouped; env writes (`process.env.X = ...`) count as reads.
- Spring `application.yml`, Rails `database.yml`, Kubernetes / Helm / Terraform values, settings built with
  f-strings (NetBox's `CACHES` from `REDIS`), docker-compose files outside the indexed root.
- Third-party HTTP hosts are an adapter in `cg external`, not graph nodes.
