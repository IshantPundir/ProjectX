# ProjectX — Backend

The backend is a single FastAPI modular monolith, **Nexus**, at
[`nexus/`](./nexus/).

- Quick start, services, env vars, and structure: [`nexus/README.md`](./nexus/README.md)
- Architecture, module boundaries, RLS model, and the interview engine:
  [`nexus/CLAUDE.md`](./nexus/CLAUDE.md)

There is no second backend service — modules are extracted only when a real
client requirement triggers independent scaling. The live interview engine,
report scorer, Candidate Reel renderer, and vision proctoring all run from the
same image as separate Dramatiq/LiveKit workers (`nexus-worker`,
`nexus-engine`, `nexus-vision-worker`).

Real-time A/V + recording run on a **self-hosted LiveKit** plane (SFU + Egress,
replaced LiveKit Cloud 2026-06-09). Locally these come up via the
`docker-compose.livekit.yml` override; in production they deploy to EC2/EKS (not
Fargate). See [`nexus/README.md`](./nexus/README.md) → "Self-hosted LiveKit" and
`docs/deployment/2026-06-09-self-hosted-livekit-deployment.md`.
