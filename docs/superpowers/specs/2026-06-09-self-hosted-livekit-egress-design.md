# Self-Hosted LiveKit (SFU + Egress) — Design

**Date:** 2026-06-09
**Status:** Approved (brainstorming) — ready for implementation plan
**Author:** Ishant + Claude
**Related:** `docs/deployment/2026-06-03-deployment-architecture-research.md`,
`docs/superpowers/specs/2026-06-04-self-hosted-audio-turn-taking-design.md`,
memories `project_livekit_egress_quota`, `project_session_recording`,
`project_deployment_architecture`

---

## 1. Problem

Interview-session recording stopped mid-month. Root cause (confirmed via the
LiveKit dashboard + docs, 2026-06-08): the free **Build** plan caps Egress at
**60 transcode-minutes/month as a hard cap** — once exhausted, new egress
requests are rejected at creation, so no recording is produced and no error
surfaces in our code. ~4 of our ~15-min interviews exhaust the month.

Paying for a higher LiveKit Cloud tier (Ship/Scale) raises the cap but gives us
no control over transport scaling and keeps recording costs metered. ProjectX is
an enterprise-grade B2B SaaS where **full control over scaling and security is a
product requirement**. The decision: **self-host LiveKit** (both the SFU and the
Egress service), fixing the quota issue permanently and unlocking full control.

### 1.1 The coupling that forces the full decision

A self-hosted Egress service **cannot** drive LiveKit Cloud. Egress load-balances
with the LiveKit server over a **shared Redis** that Cloud does not expose. So
self-hosting egress **requires** self-hosting the SFU too. Fixing the egress
quota therefore forces the full SFU-self-host decision — which is exactly the
control we want, but is a larger move than "just egress." This advances the
"Trigger B" (self-host the SFU) milestone from the deferred deployment-arch
research earlier than originally leaned, driven by egress economics rather than
raw scale. That revision is deliberate.

### 1.2 What "implement our own egress" means

It means **run LiveKit's open-source Egress service ourselves** — the same
battle-tested service LiveKit Cloud runs internally — **not** write a custom
recorder. A hand-rolled recorder (an SDK participant subscribing to tracks and
muxing A/V with ffmpeg) would re-invent GStreamer compositing, A/V sync, and
upload resilience: a large surface with many new failure modes. Rejected.

**Consequence:** our existing recording application code is portable essentially
unchanged. `session/livekit.py::build_room_egress` already builds a RoomComposite
auto-egress with an `S3Upload` target; auto-egress is just the `egress` field on
`CreateRoom`. Self-hosting changes *where* the egress runs, not *how we request
it*.

---

## 2. Scope

**In scope (build now):**
- Self-hosted LiveKit **SFU + Egress + a dedicated LiveKit-Redis** in local
  docker-compose, for the local development environment.
- Make all backend config + frontend CSP **environment-driven** so dev defaults
  to self-hosted and LiveKit Cloud remains a one-env-var fallback.
- **Reliability hardening** of the recording reconcile path (known-needed work in
  exactly the code we touch).
- An enterprise-grade **Docker/compose restructure** (clean dev/prod separation,
  LiveKit as its own plane).

**In scope (document now, build later):**
- Production deployment path for the LiveKit plane (EC2/Caddy tier → EKS/Helm
  autoscaling tier), written to `docs/deployment/`.

**Out of scope:**
- Building the production deployment (IaC, autoscaling fleet, TURN, multi-region).
  That remains the deferred deployment-architecture effort; this spec documents
  the path and makes the code ready for it.
- Any change to recording storage (stays on Cloudflare R2 — zero migration).
- Any change to the interview engine, audio path, report, reel, or vision
  modules.

---

## 3. Architecture

### 3.1 Components

Three new local services, all infrastructure:

1. **`livekit-server`** (`livekit/livekit-server`) — **the SFU**. The WebRTC
   Selective Forwarding Unit that brokers audio/video between the candidate
   browser and the engine agent. Replaces LiveKit Cloud as the transport. Runs
   with **host networking** on Linux. Configured via `config/livekit/livekit.yaml`
   (API key/secret, Redis, RTC ports) — **not** bare `--dev` mode, because egress
   needs Redis-coordinated communication with the server.
2. **`livekit-egress`** (`livekit/egress`) — headless **Chrome + GStreamer**
   recorder. Host networking + **`--cap-add=SYS_ADMIN`** (required since egress
   v1.7.6 for the Chrome sandbox; without it, RoomComposite fails with
   `chrome failed to start`). Config points at the **same Redis** + the server's
   `ws_url`. Uploads the MP4 directly to R2 via the S3 protocol (unchanged).
3. **`livekit-redis`** — a **dedicated** Redis the SFU and egress share to
   coordinate. Kept **separate from the Dramatiq broker Redis** so a flush or
   LiveKit churn never touches the job queue. On its own host port (`6380`) to
   avoid clashing with the Dramatiq Redis on `6379`.

### 3.2 Data flow (behaviorally unchanged; only the SFU + egress move in-house)

```
POST /start
  └─ create_room(egress=build_room_egress(...))   → self-hosted SFU (was Cloud)
  └─ mint candidate token, dispatch engine agent
Candidate browser  ── ws://localhost:7880 (livekit_public_url) ─→ SFU
Engine agent       ── ws://host.docker.internal:7880 (livekit_url) ─→ SFU
Egress  ── auto-starts on first participant ─→ RoomComposite (Chrome+GStreamer)
        ── uploads MP4 ─→ Cloudflare R2 (S3Upload, unchanged)
Report page ── get_recording_status() polls OUR server ─→ reconcile ─→ presigned R2 GET
```

### 3.3 Local network topology (Linux / host networking)

- `livekit-server` + `livekit-egress` use **`network_mode: host`**. WebRTC needs
  a wide UDP range (50000–60000) reachable from the browser; host networking
  makes localhost media "just work" and matches the production recommendation
  (host networking is LiveKit's explicit prod guidance). Egress reaches the
  server + Redis on `localhost`.
- `livekit-redis` on host port **6380** (SFU + egress point at `localhost:6380`);
  Dramatiq Redis stays on `6379` on the compose network.
- The engine container (bridged, with `extra_hosts: host.docker.internal`)
  reaches the host-networked SFU at **`ws://host.docker.internal:7880`**.
- The candidate browser (on the host) reaches the SFU at **`ws://localhost:7880`**.

> **macOS caveat (not our case):** Docker Desktop host networking differs;
> use `host.docker.internal` / the `nslookup host.docker.internal` IP and
> `insecure: true` per the LiveKit "running locally" guide. We are on Linux —
> non-issue, recorded for completeness.

---

## 4. Code & config changes

### 4.1 Backend `nexus` — config/env (no logic change)

`app/config.py` already has the right fields; only **values + documentation**
change (plus `.env.example`):

| Setting | Dev value (self-hosted) | Notes |
|---|---|---|
| `livekit_url` | `ws://host.docker.internal:7880` | server-side: engine agent + RoomService calls |
| `livekit_public_url` | `ws://localhost:7880` | handed to the candidate browser in `/start` |
| `livekit_api_key` / `livekit_api_secret` | self-hosted devkey/secret | **must** match `config/livekit/livekit.yaml` |
| `recording_storage_*` | **unchanged** | stays Cloudflare R2 |

LiveKit Cloud remains reachable by setting these three back to Cloud values — a
config fallback, no code path removed.

### 4.2 Backend — recording reconcile hardening (the one real code change)

`app/modules/session/recording.py::_reconcile` today gives up silently when
`list_egress` returns empty, leaving `recording_status` stuck on `'recording'`
forever (eternal "processing" spinner on the report page — flagged in
`project_livekit_egress_quota`). Add two branches:

1. **Stuck-`recording` → `failed` timeout.** When the egress never appeared and a
   configurable grace period has elapsed since the session completed (e.g.
   `agent_completed_at` + `recording_stuck_timeout_seconds`), transition the row
   to `failed` so the UI shows an honest "unavailable" instead of spinning
   forever.
2. **R2 `head_object` fallback.** If LiveKit has no egress record (it purges old
   ones) but the MP4 exists in R2 at the deterministic key, mark `ready` from the
   object's existence + size. Recording is reproducible ground-truth in R2; its
   presence is authoritative even after LiveKit forgets the egress.

Both branches are pure logic, mockable at the `get_recording_status` +
`get_object_storage` boundaries → real unit-test deltas (test-coverage gate
satisfied).

New settings: `recording_stuck_timeout_seconds` (default e.g. 900).

### 4.3 Frontend `session` — CSP `connect-src` (Human-Review-Required file)

The candidate app's CSP lives in `proxy.ts` (per-request nonce). It currently
allows `wss://*.livekit.cloud`. Self-hosting changes the browser's WebRTC origin:

- **Dev:** add `ws://localhost:7880` (and `http://localhost:7880`) to
  `connect-src`, **dev CSP only**.
- **Prod:** parameterize the LiveKit origin via env → `wss://livekit.<domain>`
  (the `frontend/session/CLAUDE.md` already anticipates this: *"For self-hosted
  LiveKit deployments, parameterize the `wss://*.livekit.cloud` entries via
  env"*).

This touches a Human-Review-Required file (CSP boundary) and a third-party
`connect-src` origin → **requires a threat-model note** in `docs/security/`.
Test delta: assert the dev CSP includes the self-hosted origin.

### 4.4 What explicitly does NOT change

`build_room_egress`, `create_room`, `dispatch_agent`, the `S3Upload`/R2 path, the
agent worker registration/bootstrap in `agent.py`, the `app/storage/` layer, the
report viewer/playback, the audio path. The recording app code is portable by
design — that is precisely why R2-over-S3-protocol was chosen.

---

## 5. Enterprise-grade Docker structure

### 5.1 The hard infra fact

**LiveKit SFU + Egress cannot run on serverless (ECS Fargate).** The LiveKit-side
requirements are doc-confirmed; the Fargate incompatibility is the AWS-side
consequence:
- SFU needs **host networking** + a wide **UDP port range** (LiveKit:
  *"host networking should be used for optimal performance"*) — Fargate's
  `awsvpc` model cannot provide host networking.
- Egress needs **`--cap-add=SYS_ADMIN`** (LiveKit, egress v1.7.6+) — Fargate
  forbids added Linux capabilities.

So the system splits into **two deployment planes that scale and live
separately**:

| Plane | Services | Prod target | Scaling signal |
|---|---|---|---|
| **App planes** (`nexus` image) | A: API · B: engine/agents · C: async CPU workers | **ECS Fargate** | A: CPU/RPS · B: warm floor + scale-in protection (call drain) · C: queue depth → scale-to-zero |
| **GPU plane** | D: vision/reel worker (`nexus-vision` image) | **Modal** / GPU EC2 | queue depth |
| **LiveKit plane** | SFU · Egress · LiveKit-Redis | **EC2 / EKS (host networking)** | SFU: connection count · Egress: `livekit_egress_available` |

The engine (Plane B) connects **outbound** to the SFU's `wss://`, so it remains
on Fargate; only the SFU + Egress are pinned to EC2/EKS.

### 5.2 Compose restructure (clean, layered, environment-explicit)

```
backend/nexus/
├── docker-compose.yml            # BASE: env-neutral service defs (images, depends_on, healthchecks, restart)
├── docker-compose.override.yml   # DEV ONLY (auto-loaded): .:/app mounts, --reload, host.docker.internal→Supabase, port exposes
├── docker-compose.prod.yml       # PROD-LIKE: pinned image tags, NO source mounts, resource limits, replicas
├── docker-compose.livekit.yml    # LiveKit PLANE: server + egress + livekit-redis (host networking) — cleanly separable
├── config/livekit/
│   ├── livekit.yaml              # SFU config (keys via env, redis @ 6380, rtc ports) — dev values committed, secrets via env
│   └── egress.yaml               # Egress config (ws_url, shared redis @ 6380, R2 S3 sink via env)
├── Dockerfile                    # app image (A/B/C) — hardened multi-stage, non-root, pinned base
└── Dockerfile.vision             # GPU image (D)
```

Principles:
- **Base/override split** — `docker compose up` stays one command in dev (override
  auto-merges); `-f docker-compose.yml -f docker-compose.prod.yml` gives a
  prod-parity run with no source mounts. Config is env-driven, never baked.
- **LiveKit as its own compose file** — mirrors the prod boundary (it deploys to
  entirely different infra). Dev brings it up alongside via
  `-f docker-compose.livekit.yml`; prod never deploys it through this file.
- **One app image, many roles** — A/B/C already share `Dockerfile` with different
  `command`s. Keep it; vision (D) stays its own image. Small image matrix.
- **Dockerfile hardening (prod):** multi-stage (builder → slim runtime),
  non-root user, pinned base/digests, `HEALTHCHECK`, no dev tooling in runtime,
  audited `.dockerignore`.
- **Secrets:** never in committed YAML. Dev `.env` (gitignored); prod → Railway
  env / AWS Secrets Manager + KMS. LiveKit `api_key`/`secret` rotate on the same
  cadence as other service keys (rotation standard).
- **Resource limits** in `docker-compose.prod.yml` — egress 4 CPU / 4 GB floor
  (RoomComposite uses 2–6 CPU; one room per instance); vision keeps its `cpus: 4`
  cap.

### 5.3 Production LiveKit plane — two tiers (documented, not built)

- **Tier 1 (first prod cut, lowest ops — recommended start):** one
  compute-optimized **EC2 VM**, the `livekit/generate` tool's `docker-compose` +
  **Caddy auto-TLS** (Let's Encrypt/ZeroSSL), SFU + 1–2 egress + LiveKit-Redis
  co-located. Auto-provisions certs for `wss://livekit.<domain>` +
  `turn.<domain>`. Ports: 443 (HTTPS + TURN/TLS), 7881/TCP, 3478/UDP,
  50000–60000/UDP. **TURN/TLS on 443 is the enterprise must-have** — it gets
  candidates through corporate firewalls.
- **Tier 2 (scale-out, Trigger B):** **EKS + LiveKit Helm chart**; egress as its
  own autoscaling deployment (HPA on the `livekit_egress_available` custom metric
  via the Prometheus adapter; 4-CPU pods, one room each); distributed/multi-region
  SFU. Graduate here only at sustained 1000s/day or a VPC-compliance trigger.

To be captured in a `docs/deployment/` doc as part of implementation.

---

## 6. Verification & rollout

### 6.1 Testing

- **Unit (real deltas):** `_reconcile` hardening — both new branches
  (stuck-timeout, R2 `head_object` fallback) mocked at `get_recording_status` +
  `get_object_storage`.
- **Frontend:** dev CSP `connect-src` includes the self-hosted origin (extends
  existing `proxy.ts`/headers tests).
- **Live smoke test (manual, documented checklist) — the real acceptance gate:**
  bring up the stack → `livekit-cli` room sanity + health probes (SFU `:7880`,
  egress `livekit_egress_available`) → run one full interview end-to-end →
  confirm the MP4 lands in R2 and the report page plays it back.

### 6.2 Rollout order

1. Add `docker-compose.livekit.yml` + `config/livekit/{livekit,egress}.yaml`.
2. Flip backend env to self-hosted; document in `.env.example`.
3. Health-check the LiveKit plane.
4. Full interview smoke test → R2 → report playback.
5. Land the reconcile hardening + unit tests.
6. Frontend dev CSP origin + test + threat-model note.
7. Write the production deployment doc under `docs/deployment/`.
8. (Optional, separate) the base/override/prod compose restructure — can land
   incrementally without breaking the existing one-command dev flow.

Each step is independently verifiable; LiveKit Cloud remains a one-env-var
fallback throughout.

### 6.3 Risks / edge cases

- **Redis port clash** — host-networked LiveKit reaches Redis on `localhost`;
  Dramatiq Redis is `6379`. `livekit-redis` gets host port **6380**; SFU + egress
  point there. Two Redises stay cleanly isolated.
- **Engine→SFU resolution** — engine (bridged) → `ws://host.docker.internal:7880`
  (`livekit_url`); browser → `ws://localhost:7880` (`livekit_public_url`). Two
  fields, already separate.
- **Local recording CPU cost** — each RoomComposite recording is 2–6 cores on the
  dev machine now (no quota, but real local load). Fine for one dev interview;
  documented for concurrent testing.
- **Chrome cap** — egress without `SYS_ADMIN` fails `chrome failed to start`.
  Pinned in compose + spec.
- **TURN** — unnecessary locally (same host); required in prod (Tier 1 Caddy
  provides it). Documented, not built now.
- **macOS divergence** — host networking differs on Docker Desktop; we are on
  Linux. Noted for completeness.

---

## 7. Security & compliance notes

- LiveKit `api_key`/`secret` are credentials → env/secrets manager only, never in
  committed YAML; rotate on the standard cadence.
- Recording remains candidate PII on private R2; presigned-GET-only access is
  unchanged. Self-hosting the SFU **removes LiveKit Cloud as a media
  sub-processor** for transport + recording — a net compliance improvement
  (consistent with the self-host-sensitive-data posture).
- CSP `connect-src` change + new transport origin → threat-model update in
  `docs/security/` (candidate surface change trigger).
- No raw PII / tokens in any new logs or config.

---

## 8. Open questions (resolve during implementation)

- Exact `recording_stuck_timeout_seconds` default (start 900s; tune against a
  real recording's lead-in/upload lag).
- Whether to land the full base/override/prod compose restructure in this effort
  or as a fast-follow (it has no functional dependency on the LiveKit bring-up).
- Pin the `livekit/livekit-server` + `livekit/egress` image versions to specific
  tags (avoid `latest` in committed config).

---

## 9. Verification log (LiveKit docs MCP, 2026-06-09)

Every load-bearing claim cross-checked against the official docs:

| Claim | Status | Source |
|---|---|---|
| Self-hosting fully supports egress (same OSS service Cloud runs) | ✅ | `/transport/self-hosting/` (feature matrix: Egress "Full support" self-hosted) |
| Egress communicates with the server over **Redis**; egress Redis "must be the same redis address used by your livekit server" | ✅ | `/transport/self-hosting/egress/` |
| ⇒ Self-hosted egress can't drive Cloud (Cloud doesn't expose its Redis) | ✅ (logical consequence of the above) | derived from `/transport/self-hosting/egress/` |
| Server must run with Redis configured (bare `--dev` is single-node/in-memory, no Redis bus) | ✅ | `/transport/self-hosting/local/` + `/transport/self-hosting/distributed/` ("Redis is required as shared data store and message bus") |
| Egress needs `--cap-add=SYS_ADMIN` since v1.7.6 (else `chrome failed to start`) | ✅ | `/transport/self-hosting/egress/` |
| RoomComposite uses 2–6 CPU; recommend 4-CPU instances; one room per instance | ✅ | `/transport/self-hosting/egress/` |
| `livekit_egress_available` Prometheus metric for autoscaling | ✅ | `/transport/self-hosting/egress/` |
| Auto-egress = `egress` field on `CreateRoom` (our existing approach) | ✅ | `/transport/media/ingress-egress/egress/autoegress` |
| Egress S3 supports **Cloudflare R2**; set `force_path_style: true` for non-AWS (our code already does) | ✅ | `/transport/media/ingress-egress/egress/outputs/` |
| Host networking recommended for Dockerized prod | ✅ | `/transport/self-hosting/deployment` |
| Ports: 7880 API/WS, 7881 TCP, 50000–60000 UDP (or 7882 UDP mux), TURN/TLS 5349/443, TURN/UDP 3478 | ✅ | `/transport/self-hosting/ports-firewall` |
| TURN/TLS on 443 for corporate-firewall traversal | ✅ | `/transport/self-hosting/deployment` |
| Self-hosted agent connects via `wsUrl`/`LIVEKIT_URL` (our `livekit_url`) | ✅ | `/intro/basics/connect/`, `/deploy/custom/deployments/` |
| Tier-1 prod: `livekit/generate` → docker-compose + Caddy auto-TLS | ✅ | `/transport/self-hosting/vm` |
| Tier-2 prod: EKS + Helm; prereqs = Redis + SSL (primary + TURN/TLS) + autoscaling | ✅ | `/transport/self-hosting/kubernetes`, `/transport/self-hosting/egress/` |
| **Root cause:** Build plan = **60 transcode min/mo, shared across recording + stream import**; concurrent exports = 2 | ✅ | `get_pricing_info` (Recording and export) |
| Fargate cannot host SFU (no host networking) or Egress (no `SYS_ADMIN`) | ✅ (AWS-side consequence of doc-confirmed LiveKit requirements) | AWS Fargate platform limits + LiveKit reqs above |

No claim in this spec was contradicted by the docs. One nuance sharpened: the
"not Fargate" conclusion is an AWS-platform consequence of LiveKit's
(doc-confirmed) host-networking + `SYS_ADMIN` requirements, not a LiveKit doc
statement itself.
