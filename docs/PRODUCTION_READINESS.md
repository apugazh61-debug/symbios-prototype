# SYMBIOS — Production Readiness & Deployment Checklist

> [!WARNING]
> **Pre-Deployment Security & Infrastructure Checklist**  
> Before deploying SYMBIOS to any staging or factory production environment, the following infrastructure, network security, and orchestration requirements must be addressed.

---

## 1. Network Security: Mandatory TLS / HTTPS Termination

> [!CAUTION]
> **Plain HTTP Transport (Development Only)**  
> This system currently runs over plain HTTP. Before any real deployment, TLS/HTTPS must be added (e.g., via a reverse proxy like nginx/Caddy with a real certificate, or a load balancer that terminates TLS), otherwise login credentials and JWT bearer tokens travel in cleartext on the network.

- **Risk**: In plain HTTP mode, HTTP Basic/Bearer auth tokens, supervisor credentials, and video frame payloads can be intercepted on the local factory LAN or Wi-Fi.
- **Remediation**:
  - Deploy behind an edge reverse proxy (e.g. NGINX, Caddy, Traefik, or AWS ALB) enforcing HTTP/2 and TLS 1.3.
  - Set `Secure` and `SameSite=Strict` attributes on session cookies if cookie authentication is enabled.
  - Enforce HSTS (`Strict-Transport-Security`).

---

## 2. Horizontal Scaling & Distributed Rate Limiting

- **Current State**: The rate limiter protecting `POST /api/v1/auth/token` (`InMemoryRateLimiter` in `core/security.py`) is an in-memory, per-process sliding window tracker.
- **Limitation**: In a multi-worker production deployment (e.g., multiple Uvicorn workers behind Gunicorn, or multiple Kubernetes pods behind an Ingress), each process maintains its own independent counter. An attacker could bypass the 5-attempt threshold by distributing attempts across workers.
- **Remediation**: Before scaling horizontally across replicas, replace the in-memory dictionary with a shared, distributed Redis-backed rate limiter (e.g. Redis `INCR` + `EXPIRE` or a sliding log using Redis Sorted Sets `ZADD`/`ZREMRANGEBYSCORE`).

---

## 3. Cobot Fleet Orchestration: Single-Cobot Assignment & Lack of Fallback Pooling

- **Current State**: The decision engine calls `cobot_scheduler.get_context()`, which selects the first `IDLE` cobot in the fleet. If that cobot becomes `ACTIVE` or rejects the transition during `trigger_reassignment()`, the dispatch is declared failed.
- **Known Limitation (Single-Cobot Assumption, No Fallback Pooling)**:
  - If the fleet has multiple cobots (e.g., `cobot-cell-01`, `cobot-cell-02`, `cobot-cell-03`), the scheduler currently does not attempt an alternative available idle cobot before declaring the dispatch failed.
  - The dispatch rejection is safely handled (a critical `AuditLog` entry is recorded, a supervisor `SafetyAlert` is created, and an immediate WebSocket notification is broadcast requiring manual supervisor intervention), but automatic failover to a standby cobot in the same workcell is not yet implemented.
- **Roadmap Item**: Implement a workcell-aware cobot pool manager that queries all eligible idle cobots in proximity, ranks them by distance/reachability, and attempts fallback assignments down the priority list before escalating to a human supervisor.

---

## 4. Hardware Driver Integration (RTDE / PLC / OPC-UA)

- In the current prototype, the cobot state machine transitions (`IDLE` → `ASSIGNED` → `ACTIVE` → `RETURNING`) are managed in-memory and synchronized to relational database tables with simulated asynchronous progression.
- Prior to live factory commissioning, the `CobotScheduler` state machine must be coupled to real hardware controllers via:
  - **Universal Robots RTDE** (`ur_rtde`) for collaborative UR arms.
  - **OPC-UA / Modbus TCP** for industrial PLCs (Siemens S7, Allen-Bradley GuardLogix).
  - Physical hardware E-Stop dual-channel safety relays (ISO 13849-1 Category 4 / PLe).
