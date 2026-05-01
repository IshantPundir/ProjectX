# frontend/session

Candidate interview surface for ProjectX. Token-gated single-use sessions.

See `CLAUDE.md` for architecture, rules, and dev commands.

## Quick start

```bash
cp .env.local.example .env.local
npm install
npm run dev   # localhost:3002
```

## Tests

```bash
npm run test
npm run test:coverage   # enforces 100% branch on candidate-session paths
```
