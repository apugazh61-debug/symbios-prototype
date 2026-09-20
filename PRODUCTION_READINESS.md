# SYMBIOS Platform — Production Readiness Checklist

This document lists the known gaps between the current prototype state and a
production-safe deployment. It is intended to be kept up-to-date and reviewed
by the responsible EHS engineering team before any real-world rollout.

---

## 🔴 Critical — Must Fix Before Any Real Deployment

### 1. TLS / HTTPS Termination

> **Status: NOT implemented. Currently running on plain HTTP.**

The API server (`uvicorn main:app --host 0.0.0.0 --port 8000`) serves all
traffic, including authentication endpoints and JWT bearer tokens, over
**unencrypted HTTP**. This means:

- Login credentials (email + password) are transmitted in cleartext on the
  network and visible to any passive network observer on the same segment.
- JWT bearer tokens sent on every subsequent request are equally visible;
  anyone who captures one can impersonate that supervisor for the token's
  remaining lifetime (up to 12 hours).
- This is incompatible with OSHA/ISO 45001 audit trail integrity requirements,
  GDPR Article 32 (appropriate technical security measures), and basic
  enterprise security posture.

**Required remediation before deployment:**

Option A — Reverse proxy (recommended for on-premise):
```nginx
# nginx example (with Certbot / Let's Encrypt)
server {
    listen 443 ssl;
    ssl_certificate     /etc/letsencrypt/live/symbios.example.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/symbios.example.com/privkey.pem;
    ssl_protocols       TLSv1.2 TLSv1.3;
    ssl_prefer_server_ciphers on;

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    }
}
server {
    listen 80;
    return 301 https://$host$request_uri;
}
```

Option B — Caddy (automatic HTTPS via ACME):
```
symbios.example.com {
    reverse_proxy 127.0.0.1:8000
}
```

Option C — Cloud load balancer TLS termination (AWS ALB, GCP HTTPS LB, Azure
Application Gateway) — terminate TLS at the load balancer and pass plain HTTP
internally only on the private VPC network.

**Additionally**, add `Strict-Transport-Security` (HSTS) headers:
`Strict-Transport-Security: max-age=31536000; includeSubDomains`

---

### 2. In-Memory Rate Limiter — Does Not Scale Across Replicas

> **Status: In-memory per-process rate limiter only.**

The current brute-force protection in `backend/api/v1/auth.py` stores failed
login attempts in a Python `defaultdict` that lives in a single process's heap:

```python
_FAILED_LOGIN_ATTEMPTS: dict[str, list[float]] = defaultdict(list)
```

**This has two consequences in a multi-replica deployment:**

1. Each process has its own independent counter. An attacker making 5 attempts
   against replica A and 5 attempts against replica B sees **10 failed attempts
   total** but only **5 per process**, so neither ever triggers the 429 lockout.
   The rate limit is entirely bypassed across replicas.

2. A successful login that clears the IP/user counter on replica A does **not**
   clear it on replica B or C, so a shared NAT IP may remain locked out on
   other replicas despite a legitimate login having occurred.

**Required remediation before horizontal scaling:**

Replace the in-memory counters with a **Redis-backed sliding window** using
atomic INCR/EXPIRE operations:

```python
# Example using redis-py with a sorted-set sliding window
import redis, time

r = redis.Redis.from_url(settings.REDIS_URL)
WINDOW = 15 * 60   # 15 minutes
MAX_FAILURES = 5

def check_rate_limit(key: str) -> None:
    now = time.time()
    pipe = r.pipeline()
    pipe.zremrangebyscore(key, 0, now - WINDOW)  # Purge old entries
    pipe.zadd(key, {str(now): now})              # Record this attempt
    pipe.zcard(key)                              # Count active entries
    pipe.expire(key, WINDOW)
    _, _, count, _ = pipe.execute()
    if count > MAX_FAILURES:
        raise HTTPException(status_code=429, detail="Too many failed attempts")
```

A Redis URL is already defined in `core/config.py` (`REDIS_URL`). A Redis 7.x
instance must be provisioned and `REDIS_URL` set before enabling this.

Alternatively, a dedicated API gateway (Kong, AWS API Gateway, Nginx Plus) with
its own cluster-aware rate-limiting plugin can handle this at the infrastructure
layer.

---

## 🟡 High Priority — Required for Compliance / Production Stability

### 3. Secret Key Rotation

`SECRET_KEY` in `core/config.py` has a hardcoded development default that is
visible in source control. This **must** be overridden by a cryptographically
random environment variable before deployment. All existing JWTs are invalidated
on key rotation.

**Recommended generation:**
```bash
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

Store the result in a secrets manager (AWS Secrets Manager, HashiCorp Vault,
Azure Key Vault) and inject it as an environment variable at deploy time.

### 4. Database — Migrate from SQLite to PostgreSQL

SQLite (`symbios_enterprise.db`) is a single file with no concurrent-write
support and no network access. It is unsuitable for production where:

- Multiple API replicas need to connect simultaneously.
- The DB needs to live on managed, HA storage (not the app server filesystem).
- WAL-mode SQLite will deadlock under concurrent writes (e.g., simultaneous
  fatigue decisions + audit writes).

Set `DATABASE_URL` to a PostgreSQL connection string:
```
DATABASE_URL=postgresql+psycopg2://symbios:STRONG_PW@db-host:5432/symbios_prod
```
Run Alembic migrations rather than the current `Base.metadata.create_all()`.

### 5. Bootstrap User Credentials Must Use a Secrets Manager

In development, `get_bootstrap_admin_password()` generates and prints a random
ephemeral password to stdout at startup. In production:

- Set `BOOTSTRAP_ADMIN_PASSWORD` and `BOOTSTRAP_SUPERVISOR_PASSWORD` via a
  secrets manager, injected as environment variables at deploy time.
- Rotate these passwords after initial seeding — bootstrap users should not
  remain the only accounts; create individual named accounts per operator.
- The startup sync routine re-hashes bootstrap passwords on every restart.
  Ensure the env vars are stable between restarts or operators will be locked out.

### 6. CORS Policy — Restrict Wildcard

`CORS_ORIGINS` currently includes `"*"` (wildcard), which allows any origin to
make cross-site requests to the API. In production, restrict to the exact
frontend origin:

```python
CORS_ORIGINS=["https://symbios.example.com"]
```

### 7. JWT Token Expiry and Refresh

Current access tokens expire after 12 hours (`ACCESS_TOKEN_EXPIRE_MINUTES = 720`).
There is no refresh token mechanism — once the token expires the user is silently
logged out. Before deployment:

- Reduce expiry to 1–2 hours.
- Implement refresh token rotation (short-lived opaque refresh token in an
  `HttpOnly` cookie, exchanged server-side for a new access token).
- Frontend should detect 401 responses mid-session and surface a
  re-authentication prompt rather than silently failing state-changing actions.

---

## 🟢 Medium Priority — Operational Concerns

### 8. Structured Logging and Log Aggregation

- Current logging writes to stdout only. Configure log shipping to a centralised
  SIEM (Splunk, Datadog, CloudWatch Logs) for tamper-evident audit trail storage.
- Replicate the `AuditLog` table to immutable storage (S3 Object Lock, Azure
  Immutable Blob Storage) to satisfy the 3-year OSHA retention requirement.

### 9. Liveness Probe with DB Connectivity Check

- The existing `GET /healthz` readiness probe returns 200 if the process is
  alive, but does not check database connectivity.
- Add a liveness probe that performs a lightweight DB ping and returns 503 if
  the database is unreachable, so the orchestrator can restart unhealthy pods.

### 10. Container Hardening

- Run as non-root user in Docker (`USER 1000`).
- Use a minimal base image (`python:3.11-slim` or distroless).
- Pin all Python dependencies to exact versions.
- Scan with `pip-audit` or `safety check` in CI before each build.

### 11. Broaden Rate Limiting Scope

- Currently only `POST /api/v1/auth/token` is rate-limited.
- Add global rate limits on all endpoints to prevent resource exhaustion from
  synthetic workloads (e.g., spamming `POST /decisions/analyze_frame`).

---

## 📋 Deferred Safety-Bug-Class Audit Items (Medium & Low)

The following items were identified during the Comprehensive Safety-Bug-Class Audit and deferred for future hardening sprints:

### Category 1: Audit Trail Persistence (Medium / Low)
- [ ] **Item 1.2 (Medium)**: Persist worker CRUD operations (`POST /api/v1/workers`, `PUT /api/v1/workers/{id}`) to `AuditLog` table on role/credential/station assignment changes.
- [ ] **Item 1.5 (Low)**: Persist LLM Safety Copilot queries, generated natural-language explanations, and supervisor incident Q&A to `AuditLog`.
- [ ] **Item 1.6 (Low)**: Persist Camera stream configuration changes (`POST/DELETE /api/v1/cameras`) to `AuditLog`.
- [ ] **Item 1.7 (Low)**: Persist supervisor real-time acknowledgment/dismissal actions received over WebSocket to `AuditLog`.

### Category 2: Authentication & Authorization (Medium / Low)
- [ ] **Item 2.3 (Medium)**: Add token-based authentication handshake to WebSocket endpoints (`/ws/telemetry`, `/ws/alerts`) to prevent unauthorized telemetry snooping.
- [ ] **Item 2.4 (Medium)**: Require mTLS or service-account API key authentication on `POST /api/v1/decisions/analyze_frame` to prevent unauthorized synthetic frame injections.
- [ ] **Item 2.5 (Low)**: Restrict read-only inspection endpoints (`GET /api/v1/workers`, `GET /api/v1/cobots`) to authenticated roles (`OPERATOR`, `SUPERVISOR`, `ADMIN`).

### Category 3: Multi-Entity Loops & Aggregation (Medium / Low)
- [ ] **Item 3.4 (Medium)**: Ensure health-check and dashboard metric calculations gracefully handle worker states with missing or `None` coordinates without skewing safety averages.
- [ ] **Item 3.5 (Low)**: In safety zone geometry validation, report all pairwise overlapping or invalid polygons in a single pass rather than failing on the first detected conflict.

### Category 4: Partial Writes, Concurrency & Fail-Safe State (Medium / Low)
- [ ] **Item 4.3 (Medium)**: Implement exponential backoff and persistent retry queue for alert escalation tasks during transient database connection drops.
- [ ] **Item 4.4 (Low)**: Introduce distributed pub/sub cache invalidation (Redis) for in-memory active alert caches when running in a multi-replica deployment.

---

## Summary Table

| Gap | Severity | Blocks Deployment? |
|-----|----------|--------------------|
| Plain HTTP / no TLS | 🔴 Critical | **Yes — always** |
| In-memory rate limiter (not HA) | 🔴 Critical | Yes if > 1 replica |
| `SECRET_KEY` default in code | 🟡 High | Yes |
| SQLite → PostgreSQL | 🟡 High | Yes for production load |
| Bootstrap passwords via secrets manager | 🟡 High | Yes |
| CORS wildcard `"*"` | 🟡 High | Yes |
| JWT expiry / refresh | 🟡 High | Recommended |
| Structured logging / SIEM | 🟢 Medium | No |
| Liveness probe / DB check | 🟢 Medium | No |
| Container hardening | 🟢 Medium | No |
| API-wide rate limiting | 🟢 Medium | No |

---

*Document maintained by: SYMBIOS EHS Platform Engineering*
*Last updated: 2026-09-19*
*Review required before: any non-development deployment*
