# Deployment & Scaling Architecture — Research & Direction

> **STATUS: DRAFT / DIRECTION — NOT FINALIZED.** Captured 2026-06-03 from a
> research + decision session. This records the findings, the reasoning, the
> decisions we *leaned* toward, and the open questions still to resolve. It is
> **not** an implementation spec. Revisit and finalize when deployment work
> actually begins. The two-tier doctrine summary lives in
> [`../tech-stack.md`](../tech-stack.md); this doc is the deeper scaling/hosting
> reasoning behind the "Enterprise" column.

---

## Why this exists

The product must eventually handle **spikes of 1000s of candidates joining
screenings at once**, scale **down to near-zero when idle** (bursty traffic, not
constant peak), stay **cost-efficient**, and be **operable by a very small team
(effectively solo today)**. Current hosting is LiveKit Cloud (transport +
agents) + Supabase Cloud + S3/R2 + a compose/Railway-style runtime. This doc is
the plan for getting from there to a state-of-the-art, autoscaling, mostly-
managed architecture **without a rewrite** — promoting the existing single
Docker image (run as 4 commands) into independently-scaled deployments.

---

## Core reframe: the backend is already 4 scaling planes

`docker-compose.yml` already separates the runtime into exactly the planes that
need to scale independently. This 1:1 mapping is the reason the migration is a
*hosting* change, not a rewrite.

| Plane | Compose service | Workload | Scaling driver | Scale to zero? |
|---|---|---|---|---|
| **A. Stateless API** | `nexus` (`uvicorn`) | recruiter + candidate HTTP | request rate / CPU | **No** — floor ≥2, must answer instantly |
| **B. Realtime agents** | `nexus-engine` | long-lived, latency-critical LiveKit calls | concurrent interviews | **No — warm floor**, cold start (30s–3min) blows the candidate-join latency budget |
| **C. Async CPU workers** | `nexus-worker` (Dramatiq) | JD, bank-gen, report scoring | Redis queue depth | **Yes** (KEDA on queue depth) |
| **D. Async GPU workers** | `nexus-vision-worker` | ONNX gaze + ffmpeg reel | Redis queue depth + GPU | **Yes** — biggest scale-to-zero win |

**Honest caveat on "scale to zero":** only C and D truly scale to zero. B scales
*down to a small warm floor*, not zero — a realtime voice agent cannot
cold-start on the candidate's click. A never sleeps. Anyone promising a
zero-to-thousands realtime agent is hiding a latency cliff.

---

## LiveKit findings

### Cloud-only features (the lock-in to remove)
- **Adaptive interruption handling is genuinely Cloud-only.** The model runs in
  LiveKit's datacenters and is "impractical to bundle into agent containers."
  Even a self-hosted agent on Cloud transport is documented as Cloud-deploy-only
  for this feature. **→ Must engineer this out** (drop to `turn_handling
  mode="vad"` + our own barge-in; we already have `turn_taking/{floor,eou,pacing}`
  and `is_backchannel(min_words=...)`).
- **Enhanced noise cancellation is NOT a hard blocker.** We use the `ai_coustics`
  plugin (QUAIL_L), not Krisp. ai-coustics works self-hosted with our **own
  license key** (`auth` param); DTLN is an open-source drop-in fallback.

### The two caps (these are often conflated)
| Cloud plan | Concurrent **agent sessions** (agent ON LiveKit Cloud) | Concurrent **connections** (any WebRTC participant) |
|---|---|---|
| Build | 5 | 100 |
| Ship ($50) | 20 | 1,000 |
| Scale ($500) | **up to 600** (req. more) | **5,000** |
| Enterprise | Custom | Custom |

- The **600** is the *agent-session* cap — only applies to agents deployed on
  LiveKit's managed agent hosting.
- **Hybrid (self-host the agent, keep Cloud transport) removes the 600 cap** —
  but you're then bound by the *connection* cap. Each interview = 2 participants
  (candidate + agent), so Scale's 5,000 connections ≈ **~2,500 concurrent
  interviews** (also raise-able / Enterprise). Self-hosted agents still count
  against WebRTC participant minutes.
- **Fully self-hosting the SFU removes both caps** — ceiling becomes your own
  hardware/bandwidth/TURN capacity. For our 2-person rooms one SFU node holds
  thousands of rooms, so the SFU itself is not the bottleneck.

### How hard is self-hosting LiveKit? (Moderate-to-significant — the SFU is the easy part)
- ✅ Maintained **Helm chart** for EKS/GKE/DOKS.
- ⚠️ **Host networking → one SFU pod per node** (WebRTC needs direct UDP). Coarse
  autoscaling (whole nodes).
- 🚩 **"No serverless / private clusters"** — private clusters add NAT layers
  "unsuitable for WebRTC." Tension with the eventual all-in-one-VPC goal.
- 🚩 **You run your own TURN/TLS server** (own SSL certs). Load-bearing for
  candidates on locked-down **corporate firewalls** (TLS/443 fallback). Cloud
  provides + maintains TURN for free.
- 🚩 **Egress/recording must be self-hosted separately** — and it's heavy:
  RoomComposite = **headless Chrome, 2–6 CPUs, one room per instance**,
  `SYS_ADMIN`, seccomp. At 1000s concurrent that's a *second* large autoscaling
  fleet. Recordings feed reports + reels + proctoring, so egress is critical-path.
- 5-hour `terminationGracePeriodSeconds` for safe draining → slow rollouts.

### Capacity math
- LiveKit guidance: 4-core/8GB ≈ 10–25 concurrent jobs for a typical agent. Ours
  is heavier (ai-coustics NC + multilingual turn detector + 3-tier LLM) →
  budget **~6–10 interviews per 4c/8GB node**. ~100–160 nodes for 1,000
  concurrent. Connection draining + 10-min grace lets the fleet scale down safely.

### Cost intuition @ 1,000 concurrent
- Fully-managed Cloud agents: ~**$600/hr** agent-minutes + transport + inference.
- Hybrid (self-host agents on spot + Cloud transport): ~**$20–30/hr** compute +
  ~$30/hr transport + inference. **~10× cheaper compute, and removes the 600 cap.**

---

## The staged decision model (1 portability step + 2 hosting triggers)

We already run a hybrid-ish topology in dev: `nexus-engine` is **our own
container** registering outbound to LiveKit Cloud transport. So the journey is
"give the agent we already self-host a real autoscaler," not "become hybrid."

| Step / Trigger | Threshold | Action |
|---|---|---|
| **Step 1 — decouple Cloud *features*** | **Now**, scale-independent | Engineer out adaptive-interruption (→ VAD + own barge-in) + pin own ai-coustics key. Makes the agent portable; removes the lock-in. **The one near-term action.** |
| **Trigger A — agent fleet → autoscaling K8s/Fargate** | Routinely **>~400–500 concurrent** (nearing 600 cap), **OR** agent-minute spend **>~$1–2k/mo**, **OR** Railway/single-box can't absorb the spike | Self-hosted spot agent fleet w/ autoscaling; Cloud transport stays |
| **Trigger B — self-host the SFU** | A client contract **mandates A/V data in-VPC**, **OR** *sustained* (not occasional) **1000s-concurrent daily** | Run own SFU + TURN + egress fleet (EKS) |

**Occasional spikes alone never trip Trigger B** — they trip Trigger A at most.
Trigger B is about *sustained* load or *compliance*.

---

## Recommended hosting stack (next stage: AWS-native, pre-SFU-self-host)

Governing principle: **minimize what you operate at 3am; run Kubernetes only
when something forces it.** Zero self-managed stateful infra; every piece is
managed or scales to zero.

| Plane | Service | Hosting | Scaling |
|---|---|---|---|
| Stateless API | `nexus` | **ECS Fargate** | CPU autoscale, floor ≥2 |
| Realtime agents | `nexus-engine` | **ECS Fargate + task scale-in protection** | custom metric, warm floor |
| Async CPU workers | `nexus-worker` | **ECS Fargate** | scale-to-zero on queue depth |
| Async GPU workers | vision / reel | **Serverless GPU (Modal — see below)** | native scale-to-zero |
| Transport / SFU | LiveKit | **LiveKit Cloud** (until Trigger B) | managed |
| DB + Auth | Postgres + GoTrue | **Supabase Cloud Pro** | managed |
| Cache / broker | Redis | **Upstash serverless** | pay-per-use |
| Object storage | resumes / recordings | **AWS S3 + Cloudflare R2** (unchanged) | managed |
| Email / SMS | Resend / Twilio | managed (unchanged) | — |
| Observability | Grafana stack (dev) → AMP/AMG/CloudWatch (prod) | self-hosted / in-account | — |
| Errors | Sentry | SaaS w/ DPA or self-hosted | — |
| IaC | **OpenTofu** | — | — |

### Why ECS Fargate over EKS (for now)
- Nearly as low-ops as Railway (no cluster, no nodes), autoscales, per-second
  billing. The agent's long-call draining is handled by **ECS task scale-in
  protection** (task marks itself "busy" while interviews run; releases when
  idle) — works around Fargate's 120s stop cap.
- **EKS is deferred to Trigger B** (self-host SFU needs host-networking + the
  Helm chart). If we get there, EKS Auto Mode + KEDA + Karpenter, and fold the
  agent fleet onto it.

### GPU plane: RunPod vs Modal → lean **Modal**
- **RunPod:** best raw GPU $/hr; FlashBoot sub-200ms cold starts; per-second,
  scale-to-zero. **But** SOC2/HIPAA only on the pricier **Secure Cloud** tier,
  and it's Docker-container/handler-based (more glue vs our Dramatiq actors).
- **Modal (leaning toward):** Python-native `@app.function(gpu=...)` maps 1:1 to
  our Dramatiq actors; **SOC2 Type II + HIPAA-BAA out of the box**; per-function
  GPU granularity (vision=GPU, reel=CPU-only ffmpeg — don't pay GPU for ffmpeg).
- **Either way:** candidate video transits the provider → use the compliant tier
  + **add as a sub-processor in the DPIA/threat-model**. Pairs well with **R2's
  zero egress fees** (R2→GPU data path is free). Neither touches the realtime
  path, so cold starts are a non-issue.

### Why AWS (not GCP / DigitalOcean / Azure)
- **AWS wins on *data gravity* + *enterprise trust*, not technical superiority.**
  Supabase Cloud runs on AWS and S3 resumes are on AWS — compute elsewhere means
  every query crosses clouds (latency + egress). Enterprise procurement trusts AWS.
- **GCP is genuinely competitive** (Cloud Run simpler than Fargate in places;
  GKE is the best managed K8s for the eventual SFU) — would win if greenfield
  with no AWS data gravity. Revisit only if the DB ever leaves Supabase-on-AWS.
- **DigitalOcean: out** for the Fortune-500 wedge (compliance/brand). **Azure:**
  only if a specific client mandates it.
- Note: the stack is **already multi-cloud best-of-breed** (Supabase-on-AWS + S3
  + R2 + LiveKit Cloud + Modal/RunPod). "Primary cloud" = where compute sits →
  next to Supabase + S3 = **AWS**.

### DB: keep Supabase Cloud now; the fork is at the VPC trigger
- **RLS data layer is plain Postgres — trivially portable** (RDS/Aurora/self-host).
- **Auth (GoTrue + ES256 JWKS + auth hook + Admin API) is the sticky part.**
  Verification is already provider-agnostic (`verify_access_token`); the auth-hook
  + Admin-API bits would need rework off GoTrue.
- VPC-trigger fork (decide then): **self-hosted Supabase in-VPC** (least code
  change, more ops) **vs Aurora/RDS + Cognito** (AWS-managed auth, auth rework).
- **Do NOT move to plain RDS now** — that throws away managed auth to save money
  we aren't spending. Protect the abstraction (no Supabase SDK in business logic).

---

## Observability (we're missing a *destination*, not instrumentation)

Already emitted, just not visualized:
- **Logs** — structlog JSON + correlation IDs (need aggregation/search).
- **Metrics** — **Dramatiq's Prometheus middleware already runs** (need scrape +
  dashboard). API (`nexus`) needs a `/metrics` endpoint added
  (`prometheus-fastapi-instrumentator`).
- **Traces** — OpenTelemetry wired; **exporters off by default** (need a backend).
- **Errors** — `sentry-sdk[fastapi]` in deps (need DSN + activation + the
  candidate-session scrubbing rules).
- **Realtime sessions** — **LiveKit Cloud Agent Observability is already
  available** (we're paying for it) — check the LiveKit console.

**Dev (quick win, ~hours):** add the **Grafana "LGTM" stack** to compose —
Prometheus (+ `redis_exporter`), Loki + Grafana Alloy, Tempo (point
`OTEL_EXPORTER_OTLP_ENDPOINT` at it), Grafana. Self-hosted = **operator-controlled
sink**, which satisfies our own rule that OTLP spans (carry candidate eval data)
must not go to a third-party SaaS without a sub-processor agreement.

**Queues:** no good Dramatiq UI (Flower-equivalent) — Prometheus + Grafana is the
answer. Panels: per-queue depth (`default`/`report_scoring`/`ats_poll`/`reel`/
`vision`), processing rate, retry rate, **DLQ-depth alarm** (SEV3 if >24h). The
queue-depth metric **doubles as the KEDA autoscale signal** later.

**Single-session forensics:** Loki + Tempo trace-to-logs pivot on
`correlation_id` → replay any candidate's session across nexus + engine + worker.

**Prod (AWS):** **Amazon Managed Prometheus + Amazon Managed Grafana + CloudWatch
Logs**, OTel Collector fanning out — managed *and* in-account (operator-controlled,
no third-party DPA needed for candidate-data spans). Same instrumentation feeds
both tiers; only the collector destination changes.

---

## How OpenTofu (IaC) helps — and what it does NOT do
- Manages the substrate across providers in one codebase: **AWS** (VPC, ECS,
  Fargate autoscaling, IAM, ALB, Secrets Manager, CloudWatch), **Supabase**,
  **Cloudflare** (R2), **Upstash** (Redis), later **LiveKit + Helm/K8s**.
- Buys a solo operator: one-command rebuild/teardown, identical dev/staging/prod,
  DR (`tofu apply` to a fresh region → serves RTO ≤ 4h), no click-ops drift.
- **Does NOT autoscale** — it provisions the *autoscalers* (ECS App Auto Scaling /
  KEDA / Karpenter); they do the runtime up/down/zero. IaC = blueprint, not thermostat.
- Prefer **OpenTofu** (MPL) over HashiCorp Terraform (BSL) for the enterprise posture.
- Pair: OpenTofu (substrate) → Helm/ArgoCD or CI (workloads) → autoscalers (runtime).

---

## Open questions to resolve before finalizing
1. **Realistic 12-month traffic shape** — "usually 0–20 concurrent, occasional
   500–1,000 batch spikes" vs "ramping to steady hundreds daily." Flips the
   Trigger-A timing and whether Scale ($500) vs Ship ($50) vs Enterprise is right.
2. **Start AWS-native (Fargate) now, or run on Railway until Trigger A?** (Lean:
   AWS-native to skip a migration, given GPU can't run on Railway anyway.)
3. **Modal vs RunPod** — confirm after a real cost + compliance comparison on the
   actual vision/reel job profile.
4. **Agent draining on Fargate** — validate ECS task scale-in protection holds a
   10-min interview reliably under scale-in, or whether B wants EKS sooner.
5. **Vision proctoring GA** — non-commercial weights are a GA blocker
   (independent of hosting); resolve before this plane goes to a paid GPU host.
6. **Observability backend choice for prod** — AMP/AMG vs self-hosted Grafana on
   ECS vs Grafana Cloud (w/ DPA). All satisfy the operator-sink rule differently.
7. **Multi-region?** — not needed for a single-region candidate base; revisit if
   candidate latency/data-residency requires it.

## Near-term concrete actions (when deployment work starts)
- [ ] **Step 1: decouple LiveKit Cloud features** (adaptive interruption → VAD +
  own barge-in; ai-coustics own key). *The only scale-independent prerequisite.*
- [ ] Stand up the **dev Grafana LGTM stack** + flip on the OTel exporter + add
  FastAPI `/metrics` + `redis_exporter`.
- [ ] Author the **OpenTofu** baseline (VPC, ECS Fargate services, IAM, Upstash,
  R2) — staging first.
- [ ] Decide the GPU host (Modal lean) + update DPIA/threat-model for the new
  sub-processor.

---

## Sources (captured 2026-06-03)
- LiveKit: [self-hosted deployments](https://docs.livekit.io/deploy/custom/deployments/),
  [distributed multi-region](https://docs.livekit.io/transport/self-hosting/distributed/),
  [Kubernetes/Helm](https://docs.livekit.io/transport/self-hosting/kubernetes/),
  [egress self-hosting](https://docs.livekit.io/transport/self-hosting/egress/),
  [adaptive interruption](https://livekit.com/blog/adaptive-interruption-handling),
  [noise cancellation](https://docs.livekit.io/transport/media/noise-cancellation/),
  LiveKit Cloud pricing/feature matrix (via docs MCP).
- GPU autoscaling: [KEDA GPU scaler (CNCF)](https://www.cncf.io/blog/2026/05/27/gpu-autoscaling-on-kubernetes-with-keda-building-an-external-scaler/),
  [scale GPU to zero with KEDA (CodeLink)](https://www.codelink.io/blog/post/cost-optimized-ml-on-production-autoscaling-gpu-nodes-on-kubernetes-to-zero-using-keda),
  [vLLM on K8s (PremAI)](https://blog.premai.io/deploying-llms-on-kubernetes-vllm-ray-serve-gpu-scheduling-guide-2026/).
- Serverless GPU: [RunPod pricing](https://docs.runpod.io/serverless/pricing),
  [Modal vs RunPod vs Replicate](https://www.buildmvpfast.com/blog/scale-to-zero-serverless-gpu-modal-runpod-ai-hosting-2026),
  [Modal secure Python infra](https://modal.com/resources/best-ai-infrastructure-platforms-secure-python-workloads),
  [RunPod vs Modal (Northflank)](https://northflank.com/blog/runpod-vs-modal).
