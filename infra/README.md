# Top Arena production and delivery

The production deployment is intentionally small and observable:

- one systemd-managed Python process on `127.0.0.1:8910`;
- the system PostgreSQL instance over its local Unix socket;
- the EC2 instance role for access to S3 audio objects;
- Caddy for HTTPS and reverse proxying;
- `https://top-arena.labqoat.com` as the canonical public URL;
- GitHub Actions for verified web deployments and PyPI releases.

Docker is not required on the current host. The leaderboard Dockerfile remains useful
for reproducible builds on other infrastructure.

## Durability boundary

The current EC2 instance does not have an Elastic IP. The DNS name is stable for users,
but an EC2 stop/start can change the origin IPv4 address. If that happens, update the
Cloudflare `top-arena` A record and the GitHub `production` environment's `DEPLOY_HOST`
variable before deploying again.

PostgreSQL currently runs on the instance's root EBS volume. S3 audio has bucket
versioning, but leaderboard metadata still needs an independent database backup policy
before this should be treated as a high-availability service. No PostgreSQL TCP port
needs to be public.

## One-time host setup

Ubuntu 26.04's system packages provide PostgreSQL and the audio runtime library. Verify
the PostgreSQL major version after installation (18 is the expected Ubuntu 26.04
default):

```console
sudo apt update
sudo apt install --yes postgresql libsndfile1 rsync
psql --version
sudo systemctl enable --now postgresql
```

Use local peer authentication. The database role matches the service's `ubuntu`
operating-system user:

```console
sudo -u postgres psql --set=ON_ERROR_STOP=1 <<'SQL'
DO $$
BEGIN
    IF NOT EXISTS (SELECT FROM pg_catalog.pg_roles WHERE rolname = 'ubuntu') THEN
        CREATE ROLE ubuntu LOGIN;
    END IF;
END
$$;
SELECT 'CREATE DATABASE top_arena OWNER ubuntu'
WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'top_arena')\gexec
SQL
```

Install uv and create the deployment directory:

```console
curl -LsSf https://astral.sh/uv/install.sh | sh
/home/ubuntu/.local/bin/uv python install 3.14
install -d -o ubuntu -g ubuntu -m 0755 /home/ubuntu/top-bench
```

The first source copy must contain `pyproject.toml`, `uv.lock`, `apps/leaderboard`,
`packages/top-arena`, and `infra`. Then install the production environment and service:

```console
cd /home/ubuntu/top-bench
/home/ubuntu/.local/bin/uv sync \
  --locked \
  --no-dev \
  --package top-arena-leaderboard \
  --python 3.14

sudo install -d -o root -g ubuntu -m 0750 /etc/top-arena
sudo install -o root -g ubuntu -m 0640 \
  infra/systemd/top-arena.env.example /etc/top-arena/top-arena.env
sudo install -o root -g root -m 0644 \
  infra/systemd/top-arena.service /etc/systemd/system/top-arena.service
sudo systemctl daemon-reload
sudo systemctl enable --now top-arena
```

The environment file contains no AWS keys. boto3 receives short-lived credentials from
the instance role. Treat `/etc/top-arena/top-arena.env` as host configuration after the
first install; automated deployments do not overwrite it.

Verify the private upstream before adding public routing:

```console
systemctl status top-arena --no-pager
curl --fail --show-error http://127.0.0.1:8910/health
journalctl -u top-arena -n 100 --no-pager
```

## Cloudflare and Caddy

Create this record in the `labqoat.com` Cloudflare zone:

| Type | Name | Target | Proxy |
| --- | --- | --- | --- |
| A | `top-arena` | production origin IPv4 | Proxied |

Use Cloudflare SSL/TLS mode **Full (strict)**. Ports 80 and 443 must reach Caddy on the
origin. SSH remains separate and is used only by the deployment workflow.

The base Caddyfile keeps the pre-existing port-8900 application and imports independent
files from `/etc/caddy/conf.d/`. Install the Top Arena site without replacing that
application:

```console
sudo cp --archive /etc/caddy/Caddyfile /etc/caddy/Caddyfile.before-top-arena
sudo install -D -o root -g root -m 0644 \
  infra/caddy/top-arena.caddy /etc/caddy/conf.d/top-arena.caddy
sudo caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile
sudo systemctl restart caddy
curl --fail --show-error https://top-arena.labqoat.com/health
```

The Caddy site temporarily retains the old `sslip.io` hostname as a compatibility
alias. New documentation and package builds use only the canonical `labqoat.com` URL.

## GitHub web deployment

`.github/workflows/deploy-web.yml` runs after relevant pushes to `main` and can also be
started manually. Its path filter includes:

- `apps/leaderboard/**`;
- `infra/**`;
- root `pyproject.toml` and `uv.lock`;
- the deployment workflow itself.

Before touching production, the workflow provisions PostgreSQL, runs the Python checks,
applies and verifies migrations, runs leaderboard tests, and runs the browserless Node
UI tests. The deployment job then uploads only the server workspace, SDK workspace, and
infrastructure files. On the host, [`deploy.sh`](deploy.sh) synchronizes those scoped
directories, installs the locked environment, applies migrations, installs and
validates the systemd/Caddy configuration, restarts the service, and checks `/health`.
Concurrent deployments are serialized and are never cancelled halfway through.

Create a GitHub environment named `production` with these values:

| Kind | Name | Value |
| --- | --- | --- |
| Variable | `DEPLOY_HOST` | Origin IPv4 or SSH hostname. |
| Variable | `DEPLOY_USER` | `ubuntu` |
| Variable | `DEPLOY_PATH` | `/home/ubuntu/top-bench` |
| Secret | `DEPLOY_SSH_KEY` | Private half of a dedicated Ed25519 deployment key. |
| Secret | `DEPLOY_KNOWN_HOSTS` | Pinned `known_hosts` entry for `DEPLOY_HOST`. |

Only the dedicated public key belongs in `/home/ubuntu/.ssh/authorized_keys`. Do not
reuse a personal SSH key. The deployment user needs passwordless sudo only for the
specific host operations already required by the current installation: installing
systemd/Caddy files, validating and reloading Caddy, and restarting `top-arena`.

The workflow's public environment URL is `https://top-arena.labqoat.com`. A green run
means both the private origin health check and the public Cloudflare URL succeeded.

## PyPI trusted publishing

`.github/workflows/publish-package.yml` runs only when `packages/top-arena/**` or the
publishing workflow changes on `main`. It builds in a job with read-only repository
permissions, validates distribution metadata, installs the wheel into a clean virtual
environment, and passes the artifacts to a separate OIDC-enabled publish job.

Because `top-arena` does not yet exist on PyPI, configure a pending trusted publisher in
the PyPI account that should own the project:

| PyPI field | Value |
| --- | --- |
| PyPI project name | `top-arena` |
| Owner | `qforge-dev` |
| Repository | `top-bench` |
| Workflow | `publish-package.yml` |
| Environment | `pypi` |

Create the matching GitHub environment named `pypi`. No GitHub secret is required for
publishing. PyPI validates GitHub's short-lived OIDC identity and records attestations
for the uploaded wheel and source distribution.

Each workflow run turns the declared base version into a unique PEP 440 post-release,
for example `0.2.0.post17`. This makes every SDK change publishable without committing
an automated version bump back to the repository. Bump the base version manually when
the package's compatibility line changes.

## Verification and rollback

Operational checks:

```console
curl --fail --show-error https://top-arena.labqoat.com/health
systemctl status top-arena --no-pager
journalctl -u top-arena -n 100 --no-pager
sudo caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile
```

For an application rollback, revert the offending commit on `main`. The resulting push
passes through the same verification and deployment path, avoiding untracked changes
on the server. Database migrations are forward-only; review migration compatibility
before reverting code that depended on a schema change.

To remove only the public Top Arena route, restore the saved base Caddyfile or remove
the independent site file, validate, and restart Caddy. This does not affect PostgreSQL,
S3 objects, the service checkout, or the existing port-8900 application:

```console
sudo cp --archive /etc/caddy/Caddyfile.before-top-arena /etc/caddy/Caddyfile
sudo caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile
sudo systemctl restart caddy
```

Stop the application without deleting data with:

```console
sudo systemctl disable --now top-arena
```
