# Top Arena on `bestia`

This deployment keeps the first production setup deliberately small:

- one systemd-managed Python process at `127.0.0.1:8910`;
- the system PostgreSQL instance, reachable only through its Unix socket;
- the existing EC2 instance role for S3 access;
- a separate Caddy site for `https://top-arena.54-90-214-165.sslip.io`.

The existing Caddy site and its port-8900 upstream are not replaced. Docker is
not required on `bestia`; the Dockerfile is available for reproducible builds
elsewhere.

## One-time host setup

Ubuntu 26.04's system packages are sufficient. Install PostgreSQL and the audio
runtime library, then verify the installed PostgreSQL major version (18 is the
expected Ubuntu 26.04 default):

```console
sudo apt update
sudo apt install --yes postgresql libsndfile1
psql --version
sudo systemctl enable --now postgresql
```

Use local peer authentication instead of introducing a database password. The
database role matches the service's `ubuntu` operating-system user:

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

No PostgreSQL TCP port needs to be opened.

## Install the locked application

The checkout is expected at `/home/ubuntu/top-bench`, with `uv` already at
`/home/ubuntu/.local/bin/uv`:

```console
cd /home/ubuntu/top-bench
/home/ubuntu/.local/bin/uv python install 3.14
/home/ubuntu/.local/bin/uv sync --locked --no-dev --package top-arena-leaderboard --python 3.14
```

Install the environment template and service. The environment file contains no
AWS keys: boto3 obtains short-lived credentials from the EC2 instance role.

```console
sudo install -d -o root -g ubuntu -m 0750 /etc/top-arena
sudo install -o root -g ubuntu -m 0640 \
  infra/systemd/top-arena.env.example /etc/top-arena/top-arena.env
sudo install -o root -g root -m 0644 \
  infra/systemd/top-arena.service /etc/systemd/system/top-arena.service
sudo systemctl daemon-reload
sudo systemctl enable --now top-arena
```

The service applies the checked-in Alembic migration before each start. Verify
the private upstream before exposing it through Caddy:

```console
systemctl status top-arena --no-pager
curl --fail --show-error http://127.0.0.1:8910/
journalctl -u top-arena -n 100 --no-pager
```

## Add the independent Caddy site

First compare the checked-in bestia configuration with the live file, then make
a recoverable copy. The checked-in file preserves the existing IP site and adds
only an import for independent files under `/etc/caddy/conf.d/`:

```console
sudo cp --archive /etc/caddy/Caddyfile /etc/caddy/Caddyfile.before-top-arena
sudo install -D -o root -g root -m 0644 \
  infra/caddy/top-arena.caddy /etc/caddy/conf.d/top-arena.caddy
diff --unified /etc/caddy/Caddyfile infra/caddy/Caddyfile.bestia || true
sudo install -o root -g root -m 0644 \
  infra/caddy/Caddyfile.bestia /etc/caddy/Caddyfile
sudo caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile
sudo systemctl reload caddy
curl --fail --show-error https://top-arena.54-90-214-165.sslip.io/
```

Caddy will obtain TLS for the sslip.io hostname while continuing to serve the
existing IP site. Port 8910 remains loopback-only.

## Updates and rollback

For an update, sync the new lock before restarting:

```console
cd /home/ubuntu/top-bench
/home/ubuntu/.local/bin/uv sync --locked --no-dev --package top-arena-leaderboard --python 3.14
sudo systemctl restart top-arena
```

To roll back only the public routing, restore the saved Caddyfile, validate it,
and reload Caddy. The old port-8900 service was never changed:

```console
sudo cp --archive /etc/caddy/Caddyfile.before-top-arena /etc/caddy/Caddyfile
sudo caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile
sudo systemctl reload caddy
```

Stopping the new app is equally contained:

```console
sudo systemctl disable --now top-arena
```

These rollback steps retain the PostgreSQL database, S3 objects, checkout, and
environment file so the service can be restored without data loss.
