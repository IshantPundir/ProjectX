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
