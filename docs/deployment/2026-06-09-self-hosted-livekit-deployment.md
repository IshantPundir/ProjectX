# Self-Hosted LiveKit — Production Deployment Path

**Date:** 2026-06-09
**Status:** Documented, not yet built.
**Complements:** `docs/superpowers/specs/2026-06-09-self-hosted-livekit-egress-design.md`

This document records the production path for the LiveKit plane (SFU + Egress +
LiveKit-Redis). It is not an implementation spec; it is the deployment blueprint to
work from when the infrastructure is actually built. It complements the design spec
(above) and aligns with the staged deployment-architecture direction in
`docs/deployment/2026-06-03-deployment-architecture-research.md`.

---

## 1. Why Self-Hosted

The LiveKit Cloud Build plan caps Egress at **60 transcode-minutes/month as a hard
cap**, shared across recording and stream-import, with a maximum of 2 concurrent
exports. Approximately 4 of our ~15-minute interviews exhaust that monthly budget —
and when the cap is hit, egress requests are silently rejected with no error surfacing
in application code. Paying for a higher Cloud tier (Ship/Scale) raises the cap but
keeps recording costs metered and gives no control over transport scaling or security.

A self-hosted Egress service cannot drive LiveKit Cloud: Egress load-balances with
the SFU over a **shared Redis** that Cloud does not expose. Fixing the egress quota
therefore forces self-hosting the SFU as well. This is the decision: self-host both,
remove the quota permanently, and gain full control over scaling and security — which
is a product requirement for an enterprise B2B platform. This advances the "Trigger B"
milestone from the deployment-architecture research earlier than originally planned,
driven by egress economics rather than raw scale.

---

## 2. Plane Split

The system splits into three deployment planes that scale and live separately:

| Plane | Services | Prod target | Why |
|---|---|---|---|
| **App planes** (`nexus` image: API / engine-agents / async CPU workers) | `nexus`, `nexus-engine`, `nexus-worker` | **ECS Fargate** | Stateless or queue-driven; Fargate handles per-second autoscaling and task scale-in protection for long-lived agent calls without managing nodes |
| **GPU plane** (vision/reel worker, `nexus-vision` image) | `nexus-vision-worker` | **Modal** or GPU EC2 | Scale-to-zero on queue depth; GPU workloads need per-function granularity |
| **LiveKit plane** (SFU + Egress + LiveKit-Redis) | `livekit-server`, `livekit-egress`, `livekit-redis` | **EC2 / EKS with host networking** | Cannot run on Fargate — see below |

### Why the LiveKit plane cannot run on ECS Fargate

This is an AWS-platform consequence of LiveKit's documented requirements — not a
product choice:

- The **SFU requires host networking** for optimal performance and to expose the
  wide UDP port range (50000–60000) that WebRTC needs. Fargate's `awsvpc` networking
  model cannot provide host networking.
- **Egress requires `--cap-add=SYS_ADMIN`** since v1.7.6 (needed for Chrome's
  sandbox inside the RoomComposite recorder). Fargate forbids adding Linux
  capabilities.

The engine agent (Plane B) connects **outbound** to the SFU's `wss://` URL, so it
remains on Fargate; only the SFU and Egress are pinned to EC2 or EKS nodes with host
networking.

---

## 3. Tier 1 — First Production Cut (Recommended Start)

**Scope:** one compute-optimized EC2 VM running the SFU + 1–2 Egress instances +
LiveKit-Redis, using LiveKit's official config generator.

### Setup

Use LiveKit's `livekit/generate` Docker image to produce a ready-to-run stack:

```bash
docker pull livekit/generate
docker run --rm -it -v$PWD:/output livekit/generate
```

The generator produces:

| File | Purpose |
|---|---|
| `docker-compose.yaml` | SFU + Egress + Redis services with host networking |
| `caddy.yaml` | Caddy reverse proxy config (TLS termination + TURN/TLS) |
| `livekit.yaml` | SFU config (API key/secret, Redis, RTC ports) |
| `redis.conf` | LiveKit-Redis config |
| cloud-init / init script | VM bootstrap |

**Caddy auto-provisions TLS** via Let's Encrypt or ZeroSSL for:
- `wss://livekit.<domain>` — the primary SFU WebSocket endpoint
- `turn.<domain>` — the TURN server domain

### Required Ports / Firewall Rules

| Port | Protocol | Purpose |
|---|---|---|
| 443 | TCP | HTTPS + TURN/TLS (corporate-firewall traversal path) |
| 80 | TCP | Certificate issuance (Let's Encrypt HTTP challenge) |
| 7881 | TCP | ICE/TCP fallback |
| 3478 | UDP | TURN/UDP |
| 50000–60000 | UDP | ICE/UDP (WebRTC media) |

**TURN/TLS on 443 is the enterprise-critical path** — it gets candidates through
corporate firewalls that block non-standard ports.

### Wiring to the Application

Once the LiveKit plane is running, two config changes wire the rest of the system:

**Backend** (`backend/nexus/.env`):
```
LIVEKIT_URL=wss://livekit.<domain>
LIVEKIT_PUBLIC_URL=wss://livekit.<domain>
LIVEKIT_API_KEY=<key>
LIVEKIT_API_SECRET=<secret>
```

**Candidate session frontend** (`frontend/session/.env`):
```
NEXT_PUBLIC_LIVEKIT_WS_URL=wss://livekit.<domain>
```

`NEXT_PUBLIC_LIVEKIT_WS_URL` was added to `frontend/session` specifically to support
this self-hosted configuration. The CSP `connect-src` origin in `proxy.ts` is already
parameterized via env for exactly this path.

No application code changes are required. `build_room_egress` and `create_room` are
portable by design.

---

## 4. Tier 2 — Scale-Out (Trigger B)

**Graduate here only at:** sustained 1000s of interviews/day, **or** a client contract
that mandates A/V data in-VPC. Occasional spikes do not trip this trigger. This
aligns with the "Trigger B" milestone in the 2026-06-03 deployment-architecture
research.

### EKS + LiveKit Helm Chart

```bash
helm repo add livekit https://helm.livekit.io
helm repo update
```

The Helm chart deploys the SFU as a DaemonSet (one pod per node, host networking),
and supports distributed multi-region SFU topologies.

### Egress Autoscaling

Egress runs as its own autoscaling Deployment, separate from the SFU:

- **Pod sizing:** 4-CPU pods. RoomComposite (headless Chrome + GStreamer) uses 2–6
  CPU per recording; the recommendation is one room per Egress instance.
- **HPA signal:** scale on the `livekit_egress_available` custom Prometheus metric
  via the Prometheus adapter. When all running Egress instances are occupied, the
  HPA adds pods.
- **Scaling direction:** scale out when `livekit_egress_available = 0`; scale in
  when instances are idle.

### Tier 2 Prerequisites

- A Redis instance accessible to both the SFU and the Egress Deployment (the shared
  coordination bus).
- SSL certificates for the primary domain (`wss://livekit.<domain>`) and the TURN/TLS
  domain.
- Distributed/multi-region SFU topology if latency or data-residency requires it.

When EKS is adopted for the LiveKit plane, the app-plane agent fleet (Plane B) can
also fold onto the same EKS cluster with KEDA + Karpenter, as noted in the
deployment-architecture research.

---

## 5. Recording Sink: Cloudflare R2 (Unchanged)

The recording storage path does not change at either tier. The backend's
`build_room_egress` constructs a `RoomComposite` auto-egress with an `S3Upload`
target pointing at Cloudflare R2. The self-hosted Egress service writes the MP4 to
R2 directly — it does not require S3/R2 credentials in its own config file.

Cloudflare R2 is an officially-supported egress S3 target. `force_path_style: true`
is required for non-AWS S3-compatible endpoints; the existing `build_room_egress`
code already sets this.

Access to recordings remains presigned-GET-only from the private R2 bucket.
Self-hosting the SFU removes LiveKit Cloud as a media sub-processor for transport
and recording — a net compliance improvement.

---

## 6. References

- Design spec (the primary source of truth for this deployment path):
  `docs/superpowers/specs/2026-06-09-self-hosted-livekit-egress-design.md`
  — in particular **§5.3** (production LiveKit plane, two tiers) and **§9**
  (verification log — every load-bearing claim in this document is cross-checked
  against the LiveKit docs there).
- Staged deployment-architecture research (broader hosting direction this aligns
  with): `docs/deployment/2026-06-03-deployment-architecture-research.md`
  — see the "Trigger A / Trigger B" staged decision model and the recommended
  hosting stack table.
