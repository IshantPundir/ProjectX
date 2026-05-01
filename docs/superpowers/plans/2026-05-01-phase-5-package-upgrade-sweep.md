# Phase 5 — Package Upgrade Sweep Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Bump every backend and frontend dependency to the latest patch/minor on its current major. No speculative majors. Verified with `pip-audit` / `npm audit` clean and the full test matrix preserved.

**Architecture:** Single PR off `feat/phase-5-package-upgrade-sweep` (branched from `main` after PR #5 merged). Mechanical sweep that finishes the umbrella modular-monolith spec. Lands as 5 sub-commits per spec § Phase 5: 5a (backend bulk bump), 5b (frontend/app bump), 5c (frontend/admin bump), 5d (audit gates), 5e (manual browser smoke attestation in PR body).

**Tech Stack:** Backend `pyproject.toml` + `uv.lock`, frontend `package.json` + `package-lock.json` (both apps). No new runtime deps; only version bumps.

**Spec reference:** `docs/superpowers/specs/2026-05-01-drop-langfuse-modular-monolith-design.md` § Phase 5 (lines 619–704).

**Locked discipline (per spec Q4):**
- Patch/minor only.
- No speculative majors. Not crossed: `next` 16, `react` 19, `@livekit/components-react` 2, `vitest` 4, `eslint` 9, `typescript` 5, `pydantic` 2, `sqlalchemy` 2.
- **Already bumped in earlier phases — do NOT re-do**: `openai`, `instructor`, `python`, `livekit-*` family (livekit-agents and plugins are tightly coupled to the OTel cluster pins), `opentelemetry-*` (load-bearing for livekit-agents 1.5.x transitive constraints — see comments in `pyproject.toml`).
- **Hard contract freeze (Q5)**: zero changes to public URLs, response shapes, headers, SSE events, LiveKit attributes. Phase 5 only touches dep versions + lockfiles.

**Sub-commit map:**

| Sub-commit | Subject | Stage |
|---|---|---|
| 1 | `chore(deps): bump backend pins to latest patch/minor (Phase 5a)` | A |
| 2 | `chore(deps): bump frontend/app pins to latest patch/minor (Phase 5b)` | B |
| 3 | `chore(deps): bump frontend/admin pins to latest patch/minor (Phase 5c)` | C |
| 4 | `chore(security): pip-audit + npm audit clean for both apps (Phase 5d)` | D — only if any actual fix is required; verification-only otherwise rolled into 5a-5c |
| 5 | (PR body attests the manual browser smoke result — no commit) | E |

PR body MUST include: `uv pip list --outdated` delta + `npm outdated` delta showing bumped vs not-bumped versions per spec line 703.

---

## Stage A — 5a: Backend bulk bump

### Task 1: Snapshot baselines + branch confirmation

**Files:** none (verification)

- [ ] **Step 1.1: Confirm git state**

```bash
cd /home/ishant/Projects/ProjectX
git status
git rev-parse --abbrev-ref HEAD
git log --oneline -3
```

Expected:
- Working tree clean (or only this plan file staged).
- Current branch `feat/phase-5-package-upgrade-sweep`.
- HEAD `1c80828 Merge pull request #5 …` (Phase 4 merge commit).

- [ ] **Step 1.2: Snapshot pytest baseline**

```bash
cd /home/ishant/Projects/ProjectX/backend/nexus
docker compose run --rm nexus pytest --tb=no -q 2>&1 | tail -5
docker compose run --rm nexus pytest --tb=no -q 2>&1 | grep -E "^FAILED" | sort > /tmp/phase5_baseline_fails.txt
wc -l /tmp/phase5_baseline_fails.txt
```

Expected: `645 passed, 9 failed`. The 9 failures are the same environment-driven set carried since Phase 3 (missing OPENAI_API_KEY, S3 creds, etc.) — they are NOT introduced by this branch and must be preserved exactly.

- [ ] **Step 1.3: Snapshot `uv pip list --outdated`**

```bash
cd /home/ishant/Projects/ProjectX/backend/nexus
docker compose run --rm nexus pip list --outdated --format columns 2>&1 | tee /tmp/phase5_outdated_backend.txt
```

This is the input to Stage A's bump set. Save the output for the PR body (verification gate per spec line 703).

### Task 2: Update `pyproject.toml` — backend pin bumps

**Files:**
- Modify: `backend/nexus/pyproject.toml`

The 23 in-scope pins (livekit-* and opentelemetry-* are explicitly OUT of scope per spec line 636 — "Already bumped in earlier phases (do not re-do)"). For each, look up the latest patch/minor on the current major via context7 (`mcp__context7__resolve-library-id` then `mcp__context7__query-docs`) or PyPI directly (`curl -sL https://pypi.org/pypi/<pkg>/json | python -c "import sys,json; print(json.load(sys.stdin)['info']['version'])"`). Update the lower-bound specifier to that version, keep the upper bound unchanged.

| Pin | Current | Bump action |
|---|---|---|
| `fastapi` | `>=0.115,<1` | bump minor lower bound to current latest 0.x |
| `uvicorn[standard]` | `>=0.34,<1` | bump minor |
| `pydantic[email]` | `>=2.10,<3` | bump minor |
| `pydantic-settings` | `>=2.7,<3` | bump minor |
| `sqlalchemy[asyncio]` | `>=2.0,<3` | bump minor |
| `asyncpg` | `>=0.30,<1` | bump minor |
| `alembic` | `>=1.14,<2` | bump minor |
| `dramatiq[redis]` | `>=1.17,<2` | bump minor |
| `redis` | `>=5.2,<6` | bump minor |
| `orjson` | `>=3.10,<4` | bump minor |
| `PyJWT[crypto]` | `>=2.10,<3` | bump minor |
| `httpx` | `>=0.28,<1` | bump minor |
| `boto3` | `>=1.35,<2` | bump minor |
| `resend` | `>=2.0,<3` | bump minor |
| `jinja2` | `>=3.1,<4` | bump minor |
| `structlog` | `>=24.4,<25` | **major check** — current is 25.x; if so, change pin to `>=25.X,<26` and call it out in the commit body |
| `sentry-sdk[fastapi]` | `>=2.19,<3` | bump minor |
| `sse-starlette` | `>=2.1,<3` | bump minor |
| `pytest` | `>=8.3,<9` | bump minor (in `[project.optional-dependencies].dev`) |
| `pytest-asyncio` | `>=0.25,<1` | bump minor |
| `pytest-cov` | `>=6.0,<7` | bump minor |
| `ruff` | `>=0.8,<1` | bump minor |
| `mypy` | `>=1.14,<2` | bump minor |

**OUT OF SCOPE — do NOT touch:**
- `openai>=2.10,<3` — Phase 2 baseline.
- `instructor>=1.15,<2` — Phase 2 baseline (no instructor 2.x exists).
- `livekit-api>=1.0,<2` — coupled to LiveKit cluster.
- `livekit-agents[silero,turn-detector]>=1.5.4,<2` — load-bearing OTel transitive.
- `livekit-plugins-*` — all coupled to livekit-agents.
- `opentelemetry-api>=1.39,<1.40.dev0` + `-sdk` + `-exporter-otlp` + `-instrumentation-fastapi>=0.60b0,<0.61` — LIVEKIT-AGENTS TRANSITIVE PIN. The comment block above these lines in `pyproject.toml` documents the constraint. Bumping breaks livekit-agents.
- `requires-python = ">=3.13"` — Phase 2 floor.
- `httpx` (test dep) — listed without pin, leave alone.
- `setuptools>=75` (build dep) — build-system, leave alone.

- [ ] **Step 2.1: For each in-scope pin, look up the current latest stable on its major**

You can use either context7 or direct PyPI:

```bash
for pkg in fastapi uvicorn pydantic pydantic-settings sqlalchemy asyncpg alembic dramatiq redis orjson PyJWT httpx boto3 resend jinja2 structlog sentry-sdk sse-starlette pytest pytest-asyncio pytest-cov ruff mypy; do
  echo -n "$pkg: "
  curl -sL "https://pypi.org/pypi/$pkg/json" | python -c "import sys,json; print(json.load(sys.stdin)['info']['version'])"
done
```

Save the output to `/tmp/phase5_pypi_latest.txt`. For each result, update the corresponding lower bound in `pyproject.toml` to that version (keep the upper bound).

- [ ] **Step 2.2: Edit `pyproject.toml`**

For each in-scope pin, change `>=X.Y,<UPPER` to `>=X.Y_latest,<UPPER`. Use the Edit tool one pin at a time so the diff is reviewable. Preserve the existing comments above each block.

For example:
```toml
"fastapi>=0.115,<1",
```
becomes (assuming PyPI shows 0.118.0):
```toml
"fastapi>=0.118,<1",
```

If a package has had no minor bump since the current pin (rare), leave it alone. Note in the commit body which pins were already current.

For `structlog`: if the latest is 25.x, change the pin to `>=25.X,<26` and call out the `<25 → <26` change explicitly in the commit body.

### Task 3: Regenerate `uv.lock`

**Files:**
- Modify: `backend/nexus/uv.lock` (regenerated)

- [ ] **Step 3.1: Lock**

```bash
cd /home/ishant/Projects/ProjectX/backend/nexus
uv lock 2>&1 | tail -20
```

Expected: `Resolved N packages` with no errors.

If `uv lock` reports `error: no solution found`, the most likely cause is a transitive constraint via `livekit-agents` or `instructor`. Inspect:
```bash
uv lock --verbose 2>&1 | tail -40
```

If a specific dep resolution conflicts with our floor, drop that dep's lower bound back to the prior pin and call it out in the commit body. Do NOT raise an upper bound or downgrade an unrelated dep.

- [ ] **Step 3.2: Sanity-grep critical resolved versions**

```bash
cd /home/ishant/Projects/ProjectX/backend/nexus
grep -E '^name = "(opentelemetry-api|opentelemetry-instrumentation-fastapi|livekit-agents|openai|instructor|wrapt)"' -A 1 uv.lock
```

Expected:
- `opentelemetry-api` resolves to `1.39.X`
- `opentelemetry-instrumentation-fastapi` resolves to `0.60bX`
- `livekit-agents` resolves to `1.5.X` (`>=1.5.4`)
- `openai` resolves to `2.X` (`>=2.10`)
- `instructor` resolves to `1.15.X`
- `wrapt` stays `1.X.Y` (NOT 2.x — the OTel auto-instrumentor breaks against wrapt 2.x)

If `wrapt` jumped to `2.x`, STOP — investigate which transitive moved.

### Task 4: Rebuild image + run pytest

**Files:** none (verification)

- [ ] **Step 4.1: Rebuild the nexus image**

```bash
cd /home/ishant/Projects/ProjectX/backend/nexus
docker compose build --no-cache nexus 2>&1 | tail -10
```

Expected: clean build. If a wheel build fails (e.g. asyncpg + new Python toolchain), inspect `--build-log` and resolve.

- [ ] **Step 4.2: Run full pytest**

```bash
cd /home/ishant/Projects/ProjectX/backend/nexus
docker compose run --rm nexus pytest --tb=no -q 2>&1 | tail -5
docker compose run --rm nexus pytest --tb=no -q 2>&1 | grep -E "^FAILED" | sort > /tmp/phase5_5a_fails.txt
diff /tmp/phase5_baseline_fails.txt /tmp/phase5_5a_fails.txt && echo "(zero diff vs baseline)"
```

Expected: `645 passed, 9 failed`. `diff` shows zero output (no NEW failures introduced by the bumps). If a NEW test failure appears, isolate the culprit pin via bisection — drop the lower bound on the suspected pin back to the prior, re-lock, re-test.

- [ ] **Step 4.3: Boot smoke**

```bash
docker compose run --rm nexus python -c "import app.main; print('app.main: ok')"
docker compose run --rm nexus python -c "import app.worker; print('app.worker: ok')"
docker compose run --rm nexus python -c "import app.modules.interview_engine; print('interview_engine: ok')"
```

Expected: all three print `ok` with no exceptions. The `Base.registry.configure()` call in lifespan + the model imports should still resolve.

- [ ] **Step 4.4: pip-audit gate**

```bash
cd /home/ishant/Projects/ProjectX/backend/nexus
docker compose run --rm nexus pip-audit 2>&1 | tail -20
```

Expected: zero CRITICAL CVEs. Treat HIGH CVEs as a "document in PR body, do not block merge" item per CLAUDE.md → "Supply Chain Security".

If `pip-audit` is not installed in the dev image, install ad-hoc:
```bash
docker compose run --rm nexus pip install pip-audit && docker compose run --rm nexus pip-audit 2>&1 | tail -20
```

(Adding `pip-audit` to `dev` deps in `pyproject.toml` is optional — could be added in this same commit if the install dance is awkward. Default: no, keep `dev` deps minimal.)

### Task 5: Commit sub-commit 1 (5a)

**Files:** none (verification + commit)

- [ ] **Step 5.1: Stage + commit**

```bash
cd /home/ishant/Projects/ProjectX
git add backend/nexus/pyproject.toml backend/nexus/uv.lock
git status
git diff --cached --stat
```

Expected: 2 files changed, modest diff in pyproject.toml (~23 lines), large diff in uv.lock (regenerated lockfile).

Commit:
```bash
git commit -m "$(cat <<'EOF'
chore(deps): bump backend pins to latest patch/minor (Phase 5a)

Phase 5a of the umbrella modular-monolith spec
(`docs/superpowers/specs/2026-05-01-drop-langfuse-modular-monolith-design.md`).

Patch/minor only, no speculative majors. Verified clean with pytest
(645 passed / 9 failed environment-driven baseline preserved) and
pip-audit (zero criticals).

Bumped (lower bound only; upper bound unchanged):
  fastapi, uvicorn, pydantic, pydantic-settings, sqlalchemy, asyncpg,
  alembic, dramatiq, redis, orjson, PyJWT, httpx, boto3, resend,
  jinja2, sentry-sdk, sse-starlette, pytest, pytest-asyncio,
  pytest-cov, ruff, mypy.

structlog 24.x → 25.x (allowed under "current major" rule; called
out explicitly here so it's not a stealth change).

OUT OF SCOPE — left untouched:
  openai 2.10.x (Phase 2 baseline; livekit-agents requires >=2),
  instructor 1.15.x (no instructor 2.x exists),
  livekit-api / livekit-agents / livekit-plugins-* (transitive lock
    via OTel cluster — see comments in pyproject.toml),
  opentelemetry-api 1.39 + sdk + exporter-otlp + instrumentation-fastapi
    0.60b (LOAD-BEARING for livekit-agents 1.5.x; bumping breaks
    livekit-agents resolution),
  python 3.13 (Phase 2 floor).

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
git log --oneline -1
```

---

## Stage B — 5b: Frontend `app/` bulk bump

### Task 6: Snapshot frontend/app baseline

**Files:** none (verification)

- [ ] **Step 6.1: Snapshot `npm outdated`**

```bash
cd /home/ishant/Projects/ProjectX/frontend/app
npm outdated 2>&1 | tee /tmp/phase5_outdated_app.txt
```

`npm outdated` output has 4 columns: `Current`, `Wanted`, `Latest`, `Location`. **Wanted** is the highest version that satisfies the existing `^` semver — that's our target for patch/minor bumps. **Latest** would be a major bump and is OUT OF SCOPE.

- [ ] **Step 6.2: Snapshot vitest baseline**

```bash
cd /home/ishant/Projects/ProjectX/frontend/app
npm test 2>&1 | tail -10
```

Capture pass/fail count. The plan preserves this baseline.

### Task 7: Bump frontend/app deps

**Files:**
- Modify: `frontend/app/package.json`
- Modify: `frontend/app/package-lock.json` (regenerated)

The recipe per spec § Frontend pin sweep: bump every `^X.Y.Z` to its current `Wanted` value. Major-locked: `next` (16), `react` (19), `react-dom` (19), `@livekit/components-react` (2), `vitest` (4), `eslint` (9), `typescript` (5), `@types/react` (19), `@types/react-dom` (19), `@types/node` (20).

- [ ] **Step 7.1: Identify the bump set**

For each row in `/tmp/phase5_outdated_app.txt`, decide:
- If `Wanted == Current`: skip (already at the latest patch/minor on current major).
- If `Wanted > Current` AND `Wanted < Latest`: bump to `Wanted`.
- If `Wanted == Latest` AND `Wanted` is on the same major as `Current`: bump to `Wanted`.
- If `Wanted` would cross a major (rare; check by comparing the leading digit of `Current` and `Wanted`): SKIP per Q5 freeze.

- [ ] **Step 7.2: Apply bumps**

Run `npm update` to bump every `^` constraint to the highest satisfying version:

```bash
cd /home/ishant/Projects/ProjectX/frontend/app
npm update 2>&1 | tail -10
```

This regenerates `package-lock.json` automatically. Verify the new state:

```bash
cd /home/ishant/Projects/ProjectX/frontend/app
npm outdated 2>&1 | tee /tmp/phase5_outdated_app_after.txt
```

After running `npm update`, the `Wanted` column should equal `Current` for every row. The `Latest` column will still show major-version targets — those stay out of scope.

If specific packages need explicit version bumps in `package.json` (some packages have a `^X.Y.Z` constraint where Y is already the latest minor — `npm update` won't update them), edit `package.json` directly and re-run `npm install`.

- [ ] **Step 7.3: Verify `next` and `react` did NOT cross majors**

```bash
cd /home/ishant/Projects/ProjectX/frontend/app
grep -E '"(next|react|react-dom|@livekit/components-react|vitest|eslint)"' package.json
```

Expected:
- `next`: still `16.x.y`
- `react`: still `19.x.y`
- `react-dom`: still `19.x.y`
- `@livekit/components-react`: still `^2.x.y`
- `vitest`: still `^4.x.y`
- `eslint`: still `^9.x.y`

If any crossed (e.g. `next` shows `17.x`), STOP and revert that specific package via `npm install <pkg>@<previous-major-latest>`.

### Task 8: Verify frontend/app — vitest + build + audit

**Files:** none (verification)

- [ ] **Step 8.1: Vitest**

```bash
cd /home/ishant/Projects/ProjectX/frontend/app
npm test 2>&1 | tail -10
```

Expected: pass count matches Step 6.2 baseline. Zero NEW failures.

- [ ] **Step 8.2: Build**

```bash
cd /home/ishant/Projects/ProjectX/frontend/app
npm run build 2>&1 | tail -15
```

Expected: clean Next.js build, no warnings beyond what was there before.

- [ ] **Step 8.3: Lint**

```bash
cd /home/ishant/Projects/ProjectX/frontend/app
npm run lint 2>&1 | tail -10
```

Expected: clean (or matches pre-existing baseline if lint had pre-existing warnings).

- [ ] **Step 8.4: npm audit gate**

```bash
cd /home/ishant/Projects/ProjectX/frontend/app
npm audit --omit=dev 2>&1 | tail -15
```

Expected: zero CRITICAL CVEs. HIGH CVEs are documented in the PR body per CLAUDE.md → "Supply Chain Security". If a CRITICAL surfaces, run `npm audit fix --omit=dev` (it tries to bump the offending package to a non-vulnerable patch); if that fails, STOP and escalate.

### Task 9: Commit sub-commit 2 (5b)

**Files:** none (verification + commit)

- [ ] **Step 9.1: Stage + commit**

```bash
cd /home/ishant/Projects/ProjectX
git add frontend/app/package.json frontend/app/package-lock.json
git status
git diff --cached --stat
```

Commit:
```bash
git commit -m "$(cat <<'EOF'
chore(deps): bump frontend/app pins to latest patch/minor (Phase 5b)

Phase 5b of the umbrella modular-monolith spec.

Patch/minor only, no speculative majors. Verified clean with vitest
(baseline preserved), `npm run build` (Next.js production build
clean), and `npm audit --omit=dev` (zero criticals).

Major-locked, untouched per spec Q5:
  next 16.x, react 19.x, react-dom 19.x,
  @livekit/components-react 2.x, vitest 4.x, eslint 9.x,
  typescript 5.x, @types/{react,react-dom,node}.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
git log --oneline -1
```

---

## Stage C — 5c: Frontend `admin/` bulk bump

### Task 10: Snapshot frontend/admin baseline

**Files:** none (verification)

- [ ] **Step 10.1: Snapshot `npm outdated`**

```bash
cd /home/ishant/Projects/ProjectX/frontend/admin
npm outdated 2>&1 | tee /tmp/phase5_outdated_admin.txt
```

The admin app is much smaller than `frontend/app`. The bump set is correspondingly small (next, react, react-dom, eslint, typescript, supabase, tailwind).

### Task 11: Bump frontend/admin deps

**Files:**
- Modify: `frontend/admin/package.json`
- Modify: `frontend/admin/package-lock.json` (regenerated)

- [ ] **Step 11.1: Apply bumps**

```bash
cd /home/ishant/Projects/ProjectX/frontend/admin
npm update 2>&1 | tail -10
```

- [ ] **Step 11.2: Verify majors NOT crossed**

```bash
cd /home/ishant/Projects/ProjectX/frontend/admin
grep -E '"(next|react|react-dom|eslint|typescript)"' package.json
```

Expected: `next` still `16.x`, `react` still `19.x`, `react-dom` still `19.x`, `eslint` still `^9.x`, `typescript` still `^5.x`.

### Task 12: Verify frontend/admin — build + audit

The admin app does not have a vitest test suite (per `package.json` — no `test` script). Skip vitest.

- [ ] **Step 12.1: Build**

```bash
cd /home/ishant/Projects/ProjectX/frontend/admin
npm run build 2>&1 | tail -15
```

Expected: clean build.

- [ ] **Step 12.2: Lint**

```bash
cd /home/ishant/Projects/ProjectX/frontend/admin
npm run lint 2>&1 | tail -10
```

Expected: clean.

- [ ] **Step 12.3: npm audit**

```bash
cd /home/ishant/Projects/ProjectX/frontend/admin
npm audit --omit=dev 2>&1 | tail -15
```

Expected: zero criticals.

### Task 13: Commit sub-commit 3 (5c)

```bash
cd /home/ishant/Projects/ProjectX
git add frontend/admin/package.json frontend/admin/package-lock.json
git status
git diff --cached --stat
git commit -m "$(cat <<'EOF'
chore(deps): bump frontend/admin pins to latest patch/minor (Phase 5c)

Phase 5c of the umbrella modular-monolith spec.

Patch/minor only, no speculative majors. Verified clean with
`npm run build` and `npm audit --omit=dev` (zero criticals). The
admin app has no vitest suite — verification is build + lint + audit.

Major-locked, untouched per spec Q5: next 16.x, react 19.x,
react-dom 19.x, eslint 9.x, typescript 5.x.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
git log --oneline -1
```

---

## Stage D — 5d: Audit attestation

If sub-commits 5a, 5b, and 5c each passed their pip-audit / npm audit gates with zero criticals, **no separate audit commit is required**. The audit results are captured in those commits' messages.

If a critical CVE surfaced in any stage AND was patched as part of the bump (e.g. `npm audit fix --omit=dev` updated a transitive), document the CVE number + resolution in this stage's commit. Otherwise skip.

### Task 14: Document any high CVEs in PR body

**Files:** none (notes only — PR body)

If `pip-audit` or either `npm audit --omit=dev` reported any HIGH-severity (not just critical) findings, capture them now:

```bash
cd /home/ishant/Projects/ProjectX/backend/nexus
docker compose run --rm nexus pip-audit --strict 2>&1 | tee /tmp/phase5_pip_audit_full.txt

cd /home/ishant/Projects/ProjectX/frontend/app
npm audit --omit=dev 2>&1 | tee /tmp/phase5_audit_app.txt

cd /home/ishant/Projects/ProjectX/frontend/admin
npm audit --omit=dev 2>&1 | tee /tmp/phase5_audit_admin.txt
```

Note any HIGH severities and their resolution status (patched / accepted as known issue / awaiting upstream fix). Per CLAUDE.md → "Supply Chain Security": "Critical CVE → block merge. High CVE → document in PR body."

---

## Stage E — 5e: Manual browser smoke (USER-GATED)

This stage requires a human in the loop. Phase 5 cannot ship without an attestation that the candidate full flow + recruiter dashboard + admin app login all work end-to-end against the bumped dependencies.

### Task 15: Manual browser smoke checklist

The implementing agent should:

1. Boot the dev stack:
   ```bash
   cd /home/ishant/Projects/ProjectX/backend/nexus
   docker compose up --build -d
   ```
   Wait for `nexus`, `nexus-worker`, and `nexus-engine` to all report healthy.

2. Boot both frontends:
   ```bash
   cd /home/ishant/Projects/ProjectX/frontend/app && npm run dev &
   cd /home/ishant/Projects/ProjectX/frontend/admin && npm run dev &
   ```

3. Hand off to the user with this checklist for them to execute:

   - [ ] **Admin app login** — `localhost:3001` → log in with provisioning admin → page renders.
   - [ ] **Recruiter dashboard** — `localhost:3000` → log in as a tenant Super Admin → kanban + JD list render → settings → org units render.
   - [ ] **Candidate full flow** — provision a candidate invite from the recruiter dashboard → open the invite URL → pre-check screen → consent → OTP request + verify → `/start` → LiveKit room joins → engine asks first question → candidate responds → engine ends session → completion screen renders.
   - [ ] **Spot-check** — confirm `session_outcome` LiveKit attribute is published before engine shutdown (visible in browser console / LiveKit dashboard).

4. Once the user attests success, proceed to PR creation. If anything fails, isolate the regression to a specific dep bump via bisection (revert sub-commit 5a / 5b / 5c independently to find the culprit) and roll back that specific pin.

**The implementing agent should NOT attempt the full candidate flow autonomously via Claude-in-Chrome unless explicitly instructed.** The flow involves real OTP delivery + real LiveKit Cloud rooms; running it via headless automation has a real-world cost (LiveKit charges, email delivery to real addresses).

---

## Stage F — Final verification + PR

### Task 16: Final pytest pass-fail diff vs baseline

**Files:** none

```bash
cd /home/ishant/Projects/ProjectX/backend/nexus
docker compose run --rm nexus pytest --tb=no -q 2>&1 | tail -5
docker compose run --rm nexus pytest --tb=no -q 2>&1 | grep -E "^FAILED" | sort > /tmp/phase5_final_fails.txt
diff /tmp/phase5_baseline_fails.txt /tmp/phase5_final_fails.txt && echo "(zero diff)"
```

Expected: `645 passed, 9 failed`. `diff` shows zero output.

### Task 17: Sub-commit summary + PR

**Files:** none

```bash
cd /home/ishant/Projects/ProjectX
git log --oneline main..HEAD
```

Expected: 3 sub-commits (5a, 5b, 5c), or 4 if a 5d commit was needed for a CVE patch.

```bash
git push -u origin feat/phase-5-package-upgrade-sweep
gh pr create --base main --title "chore(deps): Phase 5 — package upgrade sweep" --body "$(cat <<'EOF'
## Summary

Phase 5 of the umbrella modular-monolith spec
(`docs/superpowers/specs/2026-05-01-drop-langfuse-modular-monolith-design.md`):
backend + frontend dependency bumps to latest patch/minor on each
current major. No speculative majors; hard contract freeze (Q5)
preserved through this PR.

- **5a — Backend** (`backend/nexus/pyproject.toml` + `uv.lock`): 23
  pins bumped. structlog crossed 24 → 25 (called out explicitly).
  livekit-* and opentelemetry-* left at their Phase 3 / Phase 4
  baselines because they're load-bearing transitives for
  livekit-agents 1.5.x.
- **5b — Frontend `app/`** (`frontend/app/package.json` +
  `package-lock.json`): bulk `npm update` within `^` constraints.
  next/react/livekit-components-react/vitest/eslint major-locked.
- **5c — Frontend `admin/`** (`frontend/admin/package.json` +
  `package-lock.json`): same recipe.

## `uv pip list --outdated` delta (per spec line 703)

(paste before/after delta from `/tmp/phase5_outdated_backend.txt`)

## `npm outdated` delta — frontend/app

(paste before/after delta from `/tmp/phase5_outdated_app.txt`)

## `npm outdated` delta — frontend/admin

(paste before/after delta from `/tmp/phase5_outdated_admin.txt`)

## Test plan

- [x] `pytest --tb=no -q` matches baseline: 645 passed / 9 failed,
      same environment-driven failure set.
- [x] `npm run build` clean for `frontend/app/`.
- [x] `npm run build` clean for `frontend/admin/`.
- [x] vitest baseline preserved for `frontend/app/`.
- [x] `npm run lint` clean for both apps.
- [x] `pip-audit` zero criticals.
- [x] `npm audit --omit=dev` zero criticals for both apps.
- [x] `_assert_rls_completeness` passes at app boot.
- [x] `nexus`, `nexus-worker`, `nexus-engine` boot cleanly from
      the same image.
- [ ] **Manual browser smoke (USER GATE)** — recruiter dashboard,
      candidate full flow (invite → pre-check → consent → OTP →
      start → engine → end → completion screen), admin app login.
      Attestation: TBD.

## Hard-contract verification (per spec Q5)

This PR touches:
- Backend `pyproject.toml` + `uv.lock`
- Frontend `package.json` + `package-lock.json` (both apps)

It does NOT touch:
- Public API URLs / shapes / headers / SSE event names
- LiveKit room / token / participant attributes
- Database schema (no Alembic migration in this PR)
- Application code

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

Expected: PR URL printed (likely #6).

---

## Phase 5 done — umbrella spec complete.

After PR #6 merges, the modular-monolith uplift is complete:
- Phase 1: langfuse → OTel ✅ (PR #1)
- Phase 2: openai 2.x + instructor 1.15.x + Python 3.13 ✅ (PR #2)
- Phase 3: engine merged into nexus ✅ (PR #3 + #4)
- Phase 4: per-module models + public-API discipline ✅ (PR #5)
- Phase 5: dep upgrade sweep ✅ (PR #6)

The codebase is now a clean, defensible modular monolith with vendor-neutral observability, current dependencies, and a single Docker image running three command-variants. Future work picks up wherever the team wants — no further architectural cleanup is gating any product feature.
