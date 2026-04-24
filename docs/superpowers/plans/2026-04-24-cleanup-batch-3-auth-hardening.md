# Cleanup Batch 3 — Auth Hardening + Invite Flow

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Land section 7 of the 2026-04-24 cleanup spec — introduce a provider-agnostic `AuthProvider` boundary (single Supabase swap point), rewrite the invite-acceptance flow to be backend-owned, add client-side session validation via `SessionGuard`, and add a global 401 handler via TanStack Query.

**Architecture:** Isolate all Supabase Admin API calls behind a new `app/modules/auth/admin/` module — an `AuthProvider` protocol plus a `SupabaseAuthProvider` concrete implementation. Business logic imports only the protocol. A new public endpoint `POST /api/auth/accept-invite` drives the invite flow end-to-end: verify invite → provision auth user (create or update) → sign in → write app user row with compensation. The frontend invite page never touches `supabase.auth.signUp` again — it POSTs password + raw invite token and calls `setSession` with the tokens the backend returns. A `SessionGuard` client component validates the session on mount + visibility-change; a `QueryCache.onError` / `MutationCache.onError` handler catches 401s globally and redirects to `/login`.

**Tech Stack:** FastAPI, Python 3.12, SQLAlchemy async 2.x, pydantic v2, `httpx`, pytest. Frontend: Next.js 16 (App Router), React 19, TypeScript strict, `@supabase/ssr`, TanStack Query v5, `sonner`.

---

## Design deviation from spec

The spec's section 7.3 calls for a single `app/modules/auth/admin_client.py` wrapper. The user's vendor-lock-in concern (root `CLAUDE.md` "Auth Abstraction — Load-Bearing") applies: we should design the boundary so a future Cognito/Keycloak swap is a config change, not a file search. This plan places the module at **`backend/nexus/app/modules/auth/admin/`** with three files:

- `base.py` — `AuthProvider` protocol, `UserIdentity` + `SessionTokens` dataclasses, `UserAlreadyExistsError`.
- `supabase.py` — `SupabaseAuthProvider` concrete implementation (direct HTTP via `httpx`; no Supabase SDK).
- `__init__.py` — `get_auth_provider()` factory that reads `settings.auth_provider` and returns a singleton.

Business logic imports only from `app.modules.auth.admin` — never `supabase`. `settings.auth_provider` defaults to `"supabase"`; adding a second provider is `settings.auth_provider = "cognito"` plus a new concrete class.

The spec's section 7.2 says `getFreshSupabaseToken` should keep its current `getSession()`-only implementation, and section 7.1 lists `lib/auth/tokens.ts` as a file that changes under B3.1. Those reconcile as: the architectural fix is `SessionGuard` (validates the session OOB); `tokens.ts` stays fast on the hot path. This plan treats B3.1 as a comment-only change to `tokens.ts` documenting the new SessionGuard invariant.

---

## File Structure

| File | Role | Status |
|---|---|---|
| `backend/nexus/app/modules/auth/admin/__init__.py` | Factory `get_auth_provider()` + module re-exports | Create |
| `backend/nexus/app/modules/auth/admin/base.py` | `AuthProvider` protocol, `UserIdentity`, `SessionTokens`, `UserAlreadyExistsError` | Create |
| `backend/nexus/app/modules/auth/admin/supabase.py` | `SupabaseAuthProvider` via direct `httpx` to Supabase Admin API + token endpoint | Create |
| `backend/nexus/app/config.py` | Settings | Modify (add `auth_provider: str = "supabase"`) |
| `backend/nexus/app/modules/settings/service.py` | Migrate `_delete_auth_user` → `provider.delete_user` | Modify (delete direct httpx block + keep callers via provider) |
| `backend/nexus/app/modules/settings/router.py` | Caller of `_delete_auth_user` | Modify (swap import path to use provider) |
| `backend/nexus/app/modules/auth/schemas.py` | Add `AcceptInviteRequest` / `AcceptInviteResponse` | Modify |
| `backend/nexus/app/modules/auth/router.py` | Replace `complete_invite` with `accept_invite`; remove old endpoint | Modify |
| `backend/nexus/app/middleware/auth.py` | Add `/api/auth/accept-invite` to `_PUBLIC_PREFIXES` | Modify |
| `backend/nexus/app/database.py` | Docstrings mention `complete-invite`; rename to `accept-invite` | Modify |
| `backend/nexus/tests/test_auth_admin_supabase.py` | NEW — unit tests for `SupabaseAuthProvider` (mocked httpx) | Create |
| `backend/nexus/tests/test_auth_endpoints.py` | Remove `test_complete_invite_no_auth`; add `test_accept_invite_no_body_fields` | Modify |
| `backend/nexus/tests/test_accept_invite.py` | NEW — integration tests for the full invite-acceptance flow | Create |
| `frontend/app/lib/api/auth.ts` | Extend `authApi` with `acceptInvite()` | Modify |
| `frontend/app/app/(auth)/invite/page.tsx` | Rewrite — no more `supabase.auth.signUp` / `signInWithPassword`; call backend + `setSession` | Modify |
| `frontend/app/lib/auth/tokens.ts` | Comment-only — document SessionGuard invariant | Modify |
| `frontend/app/lib/auth/handle-error.ts` | NEW — global 401 handler `handleAuthError(err, router)` | Create |
| `frontend/app/components/dashboard/SessionGuard.tsx` | NEW — client component: `getUser()` on mount + visibility-change, onAuthStateChange SIGNED_OUT | Create |
| `frontend/app/components/dashboard/providers.tsx` | Wire `QueryCache` + `MutationCache` onError; mount `SessionGuard` | Modify |
| `frontend/app/app/(dashboard)/profile/page.tsx` | `getSession()` → `getFreshSupabaseToken()` | Modify |
| `frontend/app/app/onboarding/page.tsx` | `getSession()` → `getFreshSupabaseToken()` | Modify |
| `frontend/app/tests/auth/session-guard.test.tsx` | NEW — SessionGuard unit test | Create |
| `frontend/app/tests/auth/handle-error.test.ts` | NEW — handleAuthError unit test | Create |
| `frontend/app/tests/auth/invite-page.test.tsx` | NEW — invite page uses new endpoint; no signup calls | Create |

---

## Pre-flight

- [ ] **P.1:** Confirm working tree is on branch `cleanup/batch-3-auth-hardening` at worktree `.worktrees/cleanup-batch-3`.
  ```bash
  git -C /home/ishant/Projects/ProjectX/.worktrees/cleanup-batch-3 status
  git -C /home/ishant/Projects/ProjectX/.worktrees/cleanup-batch-3 branch --show-current
  ```
  Expected: clean tree, branch = `cleanup/batch-3-auth-hardening`.

- [ ] **P.2:** Backend baseline green.
  ```bash
  cd /home/ishant/Projects/ProjectX/.worktrees/cleanup-batch-3/backend/nexus
  docker compose up -d postgres redis
  docker compose run --rm nexus pytest -x \
    --deselect tests/test_auth_service.py::TestVerifyAccessToken::test_valid_token_returns_payload \
    --deselect tests/test_auth_service.py::TestVerifyAccessToken::test_projectx_admin_token \
    --deselect tests/test_auth_service.py::TestVerifyAccessToken::test_empty_custom_claims_returns_defaults \
    --deselect tests/test_session_schemas.py::test_pre_check_response_round_trips
  ```
  Expected: 472 passed, 4 deselected (known pre-existing failures documented in the B3 prompt).

- [ ] **P.3:** Frontend baseline green.
  ```bash
  cd /home/ishant/Projects/ProjectX/.worktrees/cleanup-batch-3/frontend/app
  npm install
  npm run type-check && npm run lint && npm run test && npm run build
  ```
  Expected: all green.

- [ ] **P.4:** Confirm env var `SUPABASE_SERVICE_ROLE_KEY` is present in `backend/nexus/.env`. Required for Admin API calls.
  ```bash
  grep -q '^SUPABASE_SERVICE_ROLE_KEY=' /home/ishant/Projects/ProjectX/backend/nexus/.env && echo OK || echo MISSING
  ```
  Expected: `OK`. If missing, stop and populate before proceeding (the user has this locally; the worktree inherits the same `.env`).

---

## Phase 1 — AuthProvider abstraction (Tasks 1–4)

## Task 1: AuthProvider protocol + shared types

**Goal:** Create the provider-agnostic boundary. Any code that needs to manage auth users, sign in, or delete users imports from this module only — never from `supabase` or `httpx`. This is the single swap point for a future Cognito/Keycloak migration.

**Files:**
- Create: `backend/nexus/app/modules/auth/admin/__init__.py`
- Create: `backend/nexus/app/modules/auth/admin/base.py`

- [ ] **Step 1.1: Create `base.py`**

  File: `backend/nexus/app/modules/auth/admin/base.py`
  ```python
  """Provider-agnostic auth admin boundary.

  Business logic imports only this module (or `app.modules.auth.admin`).
  Swapping Supabase for Cognito/Keycloak is a single-file change: add a
  new concrete provider class that satisfies the `AuthProvider` protocol,
  switch `settings.auth_provider`.

  Every method here is async. Errors that callers must handle raise typed
  exceptions; transport errors bubble as `AuthProviderError`.
  """
  from __future__ import annotations

  from typing import Protocol

  from pydantic import BaseModel


  class UserIdentity(BaseModel):
      """A provider-created auth principal. `id` is opaque to business logic."""

      id: str
      email: str


  class SessionTokens(BaseModel):
      """Tokens returned by `sign_in_with_password`. Provider-agnostic shape.

      `access_token` — short-lived JWT for API calls.
      `refresh_token` — long-lived token the browser client uses to rotate.
      `expires_in` — seconds until `access_token` expires.
      """

      access_token: str
      refresh_token: str
      expires_in: int


  class AuthProviderError(Exception):
      """Base class for provider transport/protocol errors."""


  class UserAlreadyExistsError(AuthProviderError):
      """Raised by `create_user` when the email is already registered."""


  class UserNotFoundError(AuthProviderError):
      """Raised by `update_user_password` / `delete_user` / `find_user_by_email`
      when the target user does not exist."""


  class InvalidCredentialsError(AuthProviderError):
      """Raised by `sign_in_with_password` on bad password."""


  class AuthProvider(Protocol):
      """Minimal surface every auth provider must satisfy.

      Methods are narrowly scoped and map cleanly to Supabase / Cognito /
      Keycloak admin APIs. Do not add provider-specific concepts here; if
      a feature doesn't port across at least two providers, keep it in the
      concrete class as a private helper.
      """

      async def create_user(self, email: str, password: str) -> UserIdentity:
          """Create a new auth user. Raises `UserAlreadyExistsError` if
          the email is already registered."""
          ...

      async def find_user_by_email(self, email: str) -> UserIdentity | None:
          """Return the matching user, or None if not found. Used for the
          "already exists" fallback in the invite flow."""
          ...

      async def update_user_password(self, user_id: str, password: str) -> None:
          """Set a new password for an existing user. Raises
          `UserNotFoundError` if the user does not exist."""
          ...

      async def sign_in_with_password(
          self, email: str, password: str
      ) -> SessionTokens:
          """Exchange credentials for session tokens. Raises
          `InvalidCredentialsError` on wrong password, `UserNotFoundError`
          if no such user."""
          ...

      async def delete_user(self, user_id: str) -> None:
          """Delete an auth user. Idempotent — returns normally if the user
          is already absent. Raises `AuthProviderError` on transport failure."""
          ...
  ```

- [ ] **Step 1.2: Create `__init__.py` stub**

  File: `backend/nexus/app/modules/auth/admin/__init__.py`
  ```python
  """Auth provider factory + re-exports.

  Business logic imports:

      from app.modules.auth.admin import get_auth_provider, AuthProvider
  """
  from __future__ import annotations

  from app.modules.auth.admin.base import (
      AuthProvider,
      AuthProviderError,
      InvalidCredentialsError,
      SessionTokens,
      UserAlreadyExistsError,
      UserIdentity,
      UserNotFoundError,
  )

  __all__ = [
      "AuthProvider",
      "AuthProviderError",
      "InvalidCredentialsError",
      "SessionTokens",
      "UserAlreadyExistsError",
      "UserIdentity",
      "UserNotFoundError",
      "get_auth_provider",
  ]


  def get_auth_provider() -> AuthProvider:
      """Return the configured auth provider singleton.

      Reads `settings.auth_provider` (defaults to "supabase"). Instantiates
      once and caches for process lifetime.
      """
      # Lazy import avoids a circular: supabase.py imports config which may
      # trigger app.* imports that transitively want admin/__init__.
      from app.modules.auth.admin._factory import _get_provider_singleton

      return _get_provider_singleton()
  ```

- [ ] **Step 1.3: Create `_factory.py` for lazy singleton**

  File: `backend/nexus/app/modules/auth/admin/_factory.py`
  ```python
  """Lazy singleton factory for the configured AuthProvider."""
  from __future__ import annotations

  from functools import lru_cache

  from app.config import settings
  from app.modules.auth.admin.base import AuthProvider


  @lru_cache(maxsize=1)
  def _get_provider_singleton() -> AuthProvider:
      provider_name = settings.auth_provider.lower()
      if provider_name == "supabase":
          from app.modules.auth.admin.supabase import SupabaseAuthProvider

          return SupabaseAuthProvider()
      raise RuntimeError(
          f"Unknown auth_provider: {provider_name!r}. "
          f"Supported: 'supabase'. "
          f"Add a new class satisfying app.modules.auth.admin.AuthProvider "
          f"and wire it here."
      )
  ```

- [ ] **Step 1.4: Add `auth_provider` setting**

  Modify `backend/nexus/app/config.py`. Find the existing `supabase_service_role_key` definition and add below it:
  ```python
      # Auth provider selector — controls which concrete AuthProvider
      # implementation `get_auth_provider()` returns. Defaults to Supabase;
      # swap to "cognito"/"keycloak"/etc. when adding a new provider class.
      auth_provider: str = "supabase"
  ```

  Place in alphabetical-ish proximity to other auth settings (near `supabase_*`).

- [ ] **Step 1.5: Verify import wiring**

  ```bash
  cd /home/ishant/Projects/ProjectX/.worktrees/cleanup-batch-3/backend/nexus
  docker compose run --rm nexus python -c "from app.modules.auth.admin import AuthProvider, UserIdentity, SessionTokens, UserAlreadyExistsError; print('OK')"
  ```
  Expected: prints `OK` with no import errors.

- [ ] **Step 1.6: Commit**

  ```bash
  cd /home/ishant/Projects/ProjectX/.worktrees/cleanup-batch-3
  git add backend/nexus/app/modules/auth/admin/ backend/nexus/app/config.py
  git commit -m "feat(auth): introduce provider-agnostic AuthProvider boundary"
  ```

---

## Task 2: SupabaseAuthProvider implementation + tests

**Goal:** Concrete implementation of `AuthProvider` against Supabase's REST-over-httpx surface — no `supabase-py` SDK, same `httpx` pattern the existing `_delete_auth_user` uses. Covers: `create_user`, `find_user_by_email`, `update_user_password`, `sign_in_with_password`, `delete_user`.

**Supabase API reference (verified via context7 at plan-time):**
- `POST /auth/v1/admin/users` with `{email, password, email_confirm: true}` → 200 returns `{id, email, ...}`. 422 `{"code":"email_exists", ...}` on already-registered.
- `GET /auth/v1/admin/users?email=<email>` → 200 returns `{users: [...]}` (empty array if no match).
- `PUT /auth/v1/admin/users/{id}` with `{password}` → 200 returns updated user. 404 if user missing.
- `POST /auth/v1/token?grant_type=password` with `{email, password}` and `apikey` header → 200 returns `{access_token, refresh_token, expires_in, ...}`. 400 on bad credentials.
- `DELETE /auth/v1/admin/users/{id}` → 200/204 on success. 404 if user already absent.

**Files:**
- Create: `backend/nexus/app/modules/auth/admin/supabase.py`
- Create: `backend/nexus/tests/test_auth_admin_supabase.py`

- [ ] **Step 2.1: Write failing tests**

  File: `backend/nexus/tests/test_auth_admin_supabase.py`
  ```python
  """Unit tests for SupabaseAuthProvider. httpx is mocked — no network calls."""
  from __future__ import annotations

  from unittest.mock import AsyncMock, patch

  import httpx
  import pytest

  from app.modules.auth.admin.base import (
      InvalidCredentialsError,
      SessionTokens,
      UserAlreadyExistsError,
      UserIdentity,
      UserNotFoundError,
  )
  from app.modules.auth.admin.supabase import SupabaseAuthProvider


  def _make_response(
      status_code: int, json_body: dict | None = None
  ) -> httpx.Response:
      return httpx.Response(
          status_code=status_code,
          json=json_body if json_body is not None else {},
          request=httpx.Request("POST", "http://test"),
      )


  @pytest.fixture
  def provider(monkeypatch):
      monkeypatch.setattr(
          "app.modules.auth.admin.supabase.settings.supabase_url",
          "http://supabase.local",
      )
      monkeypatch.setattr(
          "app.modules.auth.admin.supabase.settings.supabase_service_role_key",
          "test-service-role-key",
      )
      return SupabaseAuthProvider()


  class TestCreateUser:
      @pytest.mark.asyncio
      async def test_happy_path_returns_identity(self, provider):
          resp = _make_response(
              200,
              {
                  "id": "11111111-1111-1111-1111-111111111111",
                  "email": "new@example.com",
              },
          )
          with patch.object(
              httpx.AsyncClient, "post", new=AsyncMock(return_value=resp)
          ):
              identity = await provider.create_user(
                  "new@example.com", "hunter2hunter2"
              )
          assert identity == UserIdentity(
              id="11111111-1111-1111-1111-111111111111",
              email="new@example.com",
          )

      @pytest.mark.asyncio
      async def test_email_exists_raises_user_already_exists(self, provider):
          resp = _make_response(
              422,
              {
                  "code": "email_exists",
                  "msg": "A user with this email already exists",
              },
          )
          with patch.object(
              httpx.AsyncClient, "post", new=AsyncMock(return_value=resp)
          ):
              with pytest.raises(UserAlreadyExistsError):
                  await provider.create_user(
                      "existing@example.com", "hunter2hunter2"
                  )

      @pytest.mark.asyncio
      async def test_transport_error_bubbles(self, provider):
          with patch.object(
              httpx.AsyncClient,
              "post",
              new=AsyncMock(side_effect=httpx.ConnectError("boom")),
          ):
              with pytest.raises(httpx.ConnectError):
                  await provider.create_user("x@example.com", "hunter2hunter2")


  class TestFindUserByEmail:
      @pytest.mark.asyncio
      async def test_returns_identity_when_found(self, provider):
          resp = _make_response(
              200,
              {
                  "users": [
                      {
                          "id": "22222222-2222-2222-2222-222222222222",
                          "email": "found@example.com",
                      }
                  ]
              },
          )
          with patch.object(
              httpx.AsyncClient, "get", new=AsyncMock(return_value=resp)
          ):
              identity = await provider.find_user_by_email("found@example.com")
          assert identity == UserIdentity(
              id="22222222-2222-2222-2222-222222222222",
              email="found@example.com",
          )

      @pytest.mark.asyncio
      async def test_returns_none_when_not_found(self, provider):
          resp = _make_response(200, {"users": []})
          with patch.object(
              httpx.AsyncClient, "get", new=AsyncMock(return_value=resp)
          ):
              identity = await provider.find_user_by_email("nope@example.com")
          assert identity is None


  class TestUpdateUserPassword:
      @pytest.mark.asyncio
      async def test_happy_path_returns_none(self, provider):
          resp = _make_response(200, {"id": "33333333-3333-3333-3333-333333333333"})
          with patch.object(
              httpx.AsyncClient, "put", new=AsyncMock(return_value=resp)
          ):
              await provider.update_user_password(
                  "33333333-3333-3333-3333-333333333333", "newpass123"
              )

      @pytest.mark.asyncio
      async def test_missing_user_raises_not_found(self, provider):
          resp = _make_response(404, {"msg": "user not found"})
          with patch.object(
              httpx.AsyncClient, "put", new=AsyncMock(return_value=resp)
          ):
              with pytest.raises(UserNotFoundError):
                  await provider.update_user_password("missing-id", "newpass123")


  class TestSignInWithPassword:
      @pytest.mark.asyncio
      async def test_happy_path_returns_session_tokens(self, provider):
          resp = _make_response(
              200,
              {
                  "access_token": "at-123",
                  "refresh_token": "rt-456",
                  "expires_in": 3600,
              },
          )
          with patch.object(
              httpx.AsyncClient, "post", new=AsyncMock(return_value=resp)
          ):
              tokens = await provider.sign_in_with_password(
                  "user@example.com", "hunter2hunter2"
              )
          assert tokens == SessionTokens(
              access_token="at-123",
              refresh_token="rt-456",
              expires_in=3600,
          )

      @pytest.mark.asyncio
      async def test_bad_credentials_raises_invalid_credentials(self, provider):
          resp = _make_response(
              400, {"error": "invalid_grant", "error_description": "Invalid login"}
          )
          with patch.object(
              httpx.AsyncClient, "post", new=AsyncMock(return_value=resp)
          ):
              with pytest.raises(InvalidCredentialsError):
                  await provider.sign_in_with_password(
                      "user@example.com", "wrong"
                  )


  class TestDeleteUser:
      @pytest.mark.asyncio
      async def test_happy_path_returns_none(self, provider):
          resp = _make_response(204)
          with patch.object(
              httpx.AsyncClient, "delete", new=AsyncMock(return_value=resp)
          ):
              await provider.delete_user("some-id")

      @pytest.mark.asyncio
      async def test_missing_user_is_idempotent(self, provider):
          """404 on delete is treated as idempotent success."""
          resp = _make_response(404)
          with patch.object(
              httpx.AsyncClient, "delete", new=AsyncMock(return_value=resp)
          ):
              # Must not raise
              await provider.delete_user("already-gone")
  ```

- [ ] **Step 2.2: Run tests — expect ALL to fail with ImportError**

  ```bash
  cd /home/ishant/Projects/ProjectX/.worktrees/cleanup-batch-3/backend/nexus
  docker compose run --rm nexus pytest tests/test_auth_admin_supabase.py -xvs
  ```
  Expected: FAIL. Every test errors with `ModuleNotFoundError: No module named 'app.modules.auth.admin.supabase'`.

- [ ] **Step 2.3: Implement `SupabaseAuthProvider`**

  File: `backend/nexus/app/modules/auth/admin/supabase.py`
  ```python
  """Supabase concrete implementation of AuthProvider.

  Talks directly to Supabase's GoTrue REST API via httpx — no SDK. Same
  transport pattern used elsewhere in the codebase (see the legacy
  `_delete_auth_user` that this module subsumes).

  Configuration:
      settings.supabase_url              — e.g. http://127.0.0.1:54321
      settings.supabase_service_role_key — Admin API bearer token

  Both must be set. Calls silently no-op (log + return) if either is
  missing — matching existing behavior so local `test` environments that
  omit the keys don't crash on auth-touching code paths.
  """
  from __future__ import annotations

  from typing import Any

  import httpx
  import structlog

  from app.config import settings
  from app.modules.auth.admin.base import (
      AuthProviderError,
      InvalidCredentialsError,
      SessionTokens,
      UserAlreadyExistsError,
      UserIdentity,
      UserNotFoundError,
  )

  logger = structlog.get_logger()

  _JSON = {"Content-Type": "application/json"}


  def _missing_config() -> bool:
      return not (settings.supabase_url and settings.supabase_service_role_key)


  def _admin_headers() -> dict[str, str]:
      key = settings.supabase_service_role_key
      return {
          "apikey": key,
          "Authorization": f"Bearer {key}",
          **_JSON,
      }


  def _anon_headers() -> dict[str, str]:
      # Token endpoint uses service role key as apikey (same as admin surface).
      # The `grant_type=password` flow signs the user in and is the same path
      # the frontend would hit — we just call it server-side with admin key.
      key = settings.supabase_service_role_key
      return {"apikey": key, **_JSON}


  def _identity_from_user_json(body: dict[str, Any]) -> UserIdentity:
      return UserIdentity(id=str(body["id"]), email=str(body["email"]))


  class SupabaseAuthProvider:
      """AuthProvider backed by Supabase GoTrue REST."""

      async def create_user(
          self, email: str, password: str
      ) -> UserIdentity:
          if _missing_config():
              raise AuthProviderError(
                  "Supabase URL or service_role_key not configured"
              )
          url = f"{settings.supabase_url}/auth/v1/admin/users"
          payload = {
              "email": email,
              "password": password,
              "email_confirm": True,
          }
          async with httpx.AsyncClient(timeout=10.0) as client:
              resp = await client.post(url, headers=_admin_headers(), json=payload)
          if resp.status_code == 200:
              return _identity_from_user_json(resp.json())
          if resp.status_code == 422:
              body = _safe_json(resp)
              code = body.get("code") or body.get("error_code") or ""
              msg = body.get("msg") or body.get("message") or ""
              if code == "email_exists" or "already" in msg.lower():
                  raise UserAlreadyExistsError(email)
          logger.error(
              "auth.admin.create_user.failed",
              status=resp.status_code,
              body=_safe_json(resp),
          )
          raise AuthProviderError(
              f"Supabase create_user failed ({resp.status_code})"
          )

      async def find_user_by_email(
          self, email: str
      ) -> UserIdentity | None:
          if _missing_config():
              raise AuthProviderError(
                  "Supabase URL or service_role_key not configured"
              )
          url = f"{settings.supabase_url}/auth/v1/admin/users"
          async with httpx.AsyncClient(timeout=10.0) as client:
              resp = await client.get(
                  url,
                  headers=_admin_headers(),
                  params={"email": email},
              )
          if resp.status_code != 200:
              logger.error(
                  "auth.admin.find_user.failed",
                  status=resp.status_code,
                  body=_safe_json(resp),
              )
              raise AuthProviderError(
                  f"Supabase find_user_by_email failed ({resp.status_code})"
              )
          body = resp.json()
          users = body.get("users") or []
          if not users:
              return None
          # Defensive: Supabase may return users whose emails case-differ;
          # enforce exact-match.
          for user in users:
              if (user.get("email") or "").lower() == email.lower():
                  return _identity_from_user_json(user)
          return None

      async def update_user_password(
          self, user_id: str, password: str
      ) -> None:
          if _missing_config():
              raise AuthProviderError(
                  "Supabase URL or service_role_key not configured"
              )
          url = f"{settings.supabase_url}/auth/v1/admin/users/{user_id}"
          async with httpx.AsyncClient(timeout=10.0) as client:
              resp = await client.put(
                  url, headers=_admin_headers(), json={"password": password}
              )
          if resp.status_code == 200:
              return
          if resp.status_code == 404:
              raise UserNotFoundError(user_id)
          logger.error(
              "auth.admin.update_password.failed",
              status=resp.status_code,
              body=_safe_json(resp),
          )
          raise AuthProviderError(
              f"Supabase update_user_password failed ({resp.status_code})"
          )

      async def sign_in_with_password(
          self, email: str, password: str
      ) -> SessionTokens:
          if _missing_config():
              raise AuthProviderError(
                  "Supabase URL or service_role_key not configured"
              )
          url = f"{settings.supabase_url}/auth/v1/token"
          async with httpx.AsyncClient(timeout=10.0) as client:
              resp = await client.post(
                  url,
                  headers=_anon_headers(),
                  params={"grant_type": "password"},
                  json={"email": email, "password": password},
              )
          if resp.status_code == 200:
              body = resp.json()
              return SessionTokens(
                  access_token=str(body["access_token"]),
                  refresh_token=str(body["refresh_token"]),
                  expires_in=int(body.get("expires_in", 3600)),
              )
          if resp.status_code == 400:
              # GoTrue returns 400 with error=invalid_grant on wrong password.
              raise InvalidCredentialsError(email)
          logger.error(
              "auth.admin.sign_in.failed",
              status=resp.status_code,
              body=_safe_json(resp),
          )
          raise AuthProviderError(
              f"Supabase sign_in_with_password failed ({resp.status_code})"
          )

      async def delete_user(self, user_id: str) -> None:
          if _missing_config():
              logger.warning(
                  "auth.admin.delete_user.skipped",
                  reason="supabase_url or service_role_key not configured",
                  user_id=user_id,
              )
              return
          url = f"{settings.supabase_url}/auth/v1/admin/users/{user_id}"
          async with httpx.AsyncClient(timeout=10.0) as client:
              resp = await client.delete(url, headers=_admin_headers())
          if resp.status_code in (200, 204, 404):
              # 404 means the user is already gone — idempotent success.
              if resp.status_code == 404:
                  logger.info(
                      "auth.admin.delete_user.already_absent",
                      user_id=user_id,
                  )
              return
          logger.error(
              "auth.admin.delete_user.failed",
              user_id=user_id,
              status=resp.status_code,
              body=_safe_json(resp),
          )
          raise AuthProviderError(
              f"Supabase delete_user failed ({resp.status_code})"
          )


  def _safe_json(resp: httpx.Response) -> dict[str, Any]:
      try:
          data = resp.json()
          if isinstance(data, dict):
              return data
      except Exception:
          pass
      return {}
  ```

- [ ] **Step 2.4: Run tests — expect PASS**

  ```bash
  docker compose run --rm nexus pytest tests/test_auth_admin_supabase.py -xvs
  ```
  Expected: all 11 tests pass.

- [ ] **Step 2.5: Commit**

  ```bash
  git add backend/nexus/app/modules/auth/admin/supabase.py backend/nexus/tests/test_auth_admin_supabase.py
  git commit -m "feat(auth): add SupabaseAuthProvider concrete implementation"
  ```

---

## Task 3: Migrate `_delete_auth_user` to provider

**Goal:** Remove the last direct Supabase Admin HTTP call from business logic. `settings/service.py::_delete_auth_user` becomes a thin wrapper around `provider.delete_user`. This proves the boundary works and retires the legacy code that the backend CLAUDE.md literally calls out as *"the single swap point."*

**Files:**
- Modify: `backend/nexus/app/modules/settings/service.py`
- Modify: `backend/nexus/app/modules/settings/router.py` (if it calls `_delete_auth_user` directly)

- [ ] **Step 3.1: Identify all callers**

  ```bash
  cd /home/ishant/Projects/ProjectX/.worktrees/cleanup-batch-3
  rg -n "_delete_auth_user" backend/nexus/
  ```
  Expected output (used in the plan; re-run to confirm):
  ```
  backend/nexus/app/modules/settings/service.py:317:async def _delete_auth_user(auth_user_id: str) -> None:
  backend/nexus/app/modules/settings/router.py:<line>:       background_tasks.add_task(_delete_auth_user, auth_user_id)
  ```

- [ ] **Step 3.2: Replace `_delete_auth_user` body**

  Modify `backend/nexus/app/modules/settings/service.py` — replace the entire `_delete_auth_user` function (lines 317-343 in the baseline) with a provider-backed shim:
  ```python
  async def _delete_auth_user(auth_user_id: str) -> None:
      """Delete a user from the configured auth provider.

      Kept as a thin shim so existing BackgroundTask call sites in
      settings/router.py stay one-line. New code should call
      `get_auth_provider().delete_user(...)` directly.
      """
      from app.modules.auth.admin import AuthProviderError, get_auth_provider

      provider = get_auth_provider()
      try:
          await provider.delete_user(auth_user_id)
      except AuthProviderError as err:
          logger.error(
              "settings.auth_delete_failed",
              auth_user_id=auth_user_id,
              error=str(err),
          )
  ```

  Remove the `import httpx` at the top of the function — it's now unused inside this shim. (Do NOT remove the module-level `from app.config import settings` import: it's still used by the rest of this file.)

- [ ] **Step 3.3: Run the existing settings tests**

  ```bash
  docker compose run --rm nexus pytest tests/test_settings_service.py tests/test_settings_router.py -xvs 2>&1 | tail -60
  ```
  Expected: all existing tests pass. If any test mocked `httpx.AsyncClient.delete` directly, update it to mock `get_auth_provider().delete_user` instead — see the test patch pattern in Task 2's test file for reference.

- [ ] **Step 3.4: Commit**

  ```bash
  git add backend/nexus/app/modules/settings/service.py
  git commit -m "refactor(auth): route _delete_auth_user through AuthProvider"
  ```

---

## Task 4: Wire middleware public prefix for `/api/auth/accept-invite`

**Goal:** The new public endpoint needs to be reachable without a bearer JWT. The raw invite token is proof of possession. Only touches middleware — zero behavior change for anything else.

**Files:**
- Modify: `backend/nexus/app/middleware/auth.py`

- [ ] **Step 4.1: Add the prefix**

  Modify `backend/nexus/app/middleware/auth.py`. Find `_PUBLIC_PREFIXES` (around line 24):
  ```python
  _PUBLIC_PREFIXES: tuple[str, ...] = (
      "/api/auth/verify-invite",  # Public — invite token verification
  )
  ```
  Replace with:
  ```python
  _PUBLIC_PREFIXES: tuple[str, ...] = (
      "/api/auth/verify-invite",  # Public — invite token verification
      "/api/auth/accept-invite",  # Public — raw invite token proves possession
  )
  ```

- [ ] **Step 4.2: Run middleware tests**

  ```bash
  docker compose run --rm nexus pytest tests/test_middleware.py tests/test_auth_endpoints.py -xvs
  ```
  Expected: all existing tests pass (the change is purely additive).

- [ ] **Step 4.3: Commit**

  ```bash
  git add backend/nexus/app/middleware/auth.py
  git commit -m "feat(auth): whitelist /api/auth/accept-invite in middleware"
  ```

---

## Phase 2 — Accept-invite endpoint (Tasks 5–8)

## Task 5: Accept-invite schemas

**Goal:** Pydantic request/response shapes. Kept small and purpose-built — callers only need `redirect_to` + tokens for `setSession`. No `user_id`/`tenant_id` leakage.

**Files:**
- Modify: `backend/nexus/app/modules/auth/schemas.py`

- [ ] **Step 5.1: Add schemas**

  Modify `backend/nexus/app/modules/auth/schemas.py`. Append after the existing `CompleteInviteResponse` class:
  ```python
  class AcceptInviteRequest(BaseModel):
      """Body for POST /api/auth/accept-invite.

      Public endpoint. `raw_token` is the single-use invite proof;
      `password` is what the new auth user will be created with.
      """

      raw_token: str
      password: str


  class AcceptInviteResponse(BaseModel):
      """Success response for POST /api/auth/accept-invite.

      `access_token` + `refresh_token` are what the browser client feeds
      into `supabase.auth.setSession(...)` to install the cookie session.
      `expires_in` is seconds until the access_token expires.
      `redirect_to` is a same-origin relative path (validated client-side
      to avoid open-redirect).
      """

      access_token: str
      refresh_token: str
      expires_in: int
      redirect_to: str
  ```

  Leave the existing `CompleteInviteRequest` / `CompleteInviteResponse` alone for now — they're removed in Task 7 after the new handler and tests land.

- [ ] **Step 5.2: Verify import**

  ```bash
  docker compose run --rm nexus python -c "from app.modules.auth.schemas import AcceptInviteRequest, AcceptInviteResponse; print('OK')"
  ```
  Expected: `OK`.

- [ ] **Step 5.3: Commit**

  ```bash
  git add backend/nexus/app/modules/auth/schemas.py
  git commit -m "feat(auth): add accept-invite request/response schemas"
  ```

---

## Task 6: Accept-invite handler

**Goal:** The new handler. Public (no bearer JWT). Provider-agnostic — imports `AuthProvider` only. Uses compensating actions: if DB writes fail after the auth user was created, delete the auth user before re-raising.

**Flow:**
1. Verify invite (read-only, by `raw_token` hash).
2. Provision auth user via provider (`create_user` → on `UserAlreadyExistsError` → `find_user_by_email` + `update_user_password`).
3. Sign in with password via provider → tokens.
4. DB writes: atomic claim + create `User` row + optional root org unit creation (super-admin path). Wrapped in try/except — on exception, call `provider.delete_user` as compensation, then re-raise.
5. Return response.

**Files:**
- Modify: `backend/nexus/app/modules/auth/router.py`

- [ ] **Step 6.1: Write the handler**

  Modify `backend/nexus/app/modules/auth/router.py`. Add the new handler above the existing `complete_invite` (which stays in place until Task 7 deletes it):
  ```python
  @router.post("/accept-invite", response_model=AcceptInviteResponse)
  async def accept_invite(
      data: AcceptInviteRequest,
      request: Request,
      db: AsyncSession = Depends(get_bypass_db),
  ) -> AcceptInviteResponse:
      """Backend-owned invite acceptance.

      Replaces the legacy 2-call frontend flow (signUp + complete-invite).
      Public endpoint: the raw invite token is proof of possession; no
      bearer JWT required (see middleware _PUBLIC_PREFIXES).

      Compensation on DB failure: any auth user created inside this
      handler is deleted if the subsequent DB writes fail.
      """
      from app.modules.auth.admin import (
          AuthProviderError,
          InvalidCredentialsError,
          UserAlreadyExistsError,
          get_auth_provider,
      )

      provider = get_auth_provider()

      # Phase 1: verify the invite (read-only). We re-check inside the
      # atomic-claim UPDATE below, but failing fast here gives a crisp
      # 401 without touching the auth provider at all.
      token_hash = hashlib.sha256(data.raw_token.encode()).hexdigest()
      verify_result = await db.execute(
          select(UserInvite, Client)
          .join(Client, UserInvite.tenant_id == Client.id)
          .where(
              UserInvite.token_hash == token_hash,
              UserInvite.status == "pending",
              UserInvite.expires_at > sqlalchemy.func.now(),
          )
      )
      verify_row = verify_result.first()
      if not verify_row:
          raise HTTPException(status_code=401, detail="Invalid or expired invite")
      invite_row, _client_row = verify_row
      email = invite_row.email

      # Phase 2: provision the auth user.
      try:
          identity = await provider.create_user(email, data.password)
      except UserAlreadyExistsError:
          existing = await provider.find_user_by_email(email)
          if existing is None:
              logger.error(
                  "auth.accept_invite.provider_inconsistent",
                  email=email,
              )
              raise HTTPException(
                  status_code=500,
                  detail="Auth provider is in an inconsistent state",
              )
          await provider.update_user_password(existing.id, data.password)
          identity = existing
      except AuthProviderError as err:
          logger.error("auth.accept_invite.provision_failed", error=str(err))
          raise HTTPException(
              status_code=502, detail="Could not create account"
          )

      auth_user_id = uuid_mod.UUID(identity.id)

      # Phase 3: sign in (external). Password was just set, so failure here
      # is a provider issue — compensate by deleting the just-created user.
      try:
          tokens = await provider.sign_in_with_password(email, data.password)
      except (InvalidCredentialsError, AuthProviderError) as err:
          logger.error("auth.accept_invite.sign_in_failed", error=str(err))
          await _safe_delete_auth_user(provider, identity.id)
          raise HTTPException(
              status_code=502,
              detail="Account created but sign-in failed; please try logging in.",
          )

      # Phase 4: DB writes. On any exception, compensate by deleting the
      # auth user (the DB transaction rolls back automatically via the
      # get_bypass_db context manager).
      try:
          claimed_result = await db.execute(
              sqlalchemy.text("""
                  UPDATE public.user_invites
                     SET status = 'accepted', accepted_at = NOW()
                   WHERE token_hash  = :token_hash
                     AND status      = 'pending'
                     AND expires_at  > NOW()
                  RETURNING id, tenant_id, email, invited_by, projectx_admin_id
              """),
              {"token_hash": token_hash},
          )
          claimed_row = claimed_result.first()
          if not claimed_row:
              # Race — between the verify read and this claim, another
              # process accepted the same invite.
              raise HTTPException(
                  status_code=409,
                  detail="Invite has already been accepted",
              )

          user = User(
              auth_user_id=auth_user_id,
              tenant_id=uuid_mod.UUID(str(claimed_row.tenant_id)),
              email=email,
          )
          db.add(user)
          await db.flush()

          is_super_admin = claimed_row.projectx_admin_id is not None
          root_unit_id = ""
          if is_super_admin:
              await db.execute(
                  sqlalchemy.text(
                      "UPDATE public.clients SET super_admin_id = :user_id "
                      "WHERE id = :tenant_id"
                  ),
                  {
                      "user_id": str(user.id),
                      "tenant_id": str(claimed_row.tenant_id),
                  },
              )
              from app.modules.org_units.service import (
                  create_org_unit as _create_root_unit,
              )

              root_unit = await _create_root_unit(
                  db=db,
                  client_id=uuid_mod.UUID(str(claimed_row.tenant_id)),
                  name="Company",
                  unit_type="company",
                  parent_unit_id=None,
                  created_by=user.id,
                  actor_email=email,
                  workspace_mode="enterprise",
                  company_profile=None,
              )
              root_unit_id = str(root_unit.id)

          redirect_to = "/onboarding" if is_super_admin else "/"

          await log_event(
              db,
              tenant_id=uuid_mod.UUID(str(claimed_row.tenant_id)),
              actor_id=user.id,
              actor_email=email,
              action=audit_actions.USER_INVITE_CLAIMED,
              resource="user",
              resource_id=user.id,
              payload={
                  "email": email,
                  "is_super_admin": is_super_admin,
                  "root_unit_id": root_unit_id,
              },
              ip_address=request.client.host if request.client else None,
          )

          logger.info(
              "auth.invite_accepted",
              user_id=str(user.id),
              tenant_id=str(claimed_row.tenant_id),
              is_super_admin=is_super_admin,
          )
      except HTTPException:
          await _safe_delete_auth_user(provider, identity.id)
          raise
      except Exception:
          logger.exception("auth.accept_invite.db_write_failed")
          await _safe_delete_auth_user(provider, identity.id)
          raise HTTPException(
              status_code=500,
              detail="Could not finalize account creation",
          )

      return AcceptInviteResponse(
          access_token=tokens.access_token,
          refresh_token=tokens.refresh_token,
          expires_in=tokens.expires_in,
          redirect_to=redirect_to,
      )


  async def _safe_delete_auth_user(provider, auth_user_id: str) -> None:
      """Best-effort compensation. Never raises — logs on failure and
      leaves the auth user orphaned (next invite retry self-heals via
      the already-exists fallback)."""
      try:
          await provider.delete_user(auth_user_id)
      except Exception as comp_err:
          logger.error(
              "auth.accept_invite.compensation_failed",
              auth_user_id=auth_user_id,
              error=str(comp_err),
          )
  ```

  Also add the imports at the top of the file (merge with the existing grouped imports):
  ```python
  from app.modules.auth.schemas import (
      AcceptInviteRequest,
      AcceptInviteResponse,
      CompleteInviteRequest,
      CompleteInviteResponse,
      MeResponse,
      RoleAssignmentResponse,
      VerifyInviteResponse,
  )
  ```

- [ ] **Step 6.2: Smoke-test handler imports**

  ```bash
  cd /home/ishant/Projects/ProjectX/.worktrees/cleanup-batch-3/backend/nexus
  docker compose run --rm nexus python -c "from app.modules.auth.router import accept_invite; print('OK')"
  ```
  Expected: `OK`.

- [ ] **Step 6.3: Commit**

  ```bash
  git add backend/nexus/app/modules/auth/router.py
  git commit -m "feat(auth): add POST /api/auth/accept-invite handler"
  ```

---

## Task 7: Remove legacy `complete-invite` endpoint

**Goal:** Delete the old endpoint and its single smoke test. No deprecation period — the user confirmed early-dev and no in-flight invites.

**Files:**
- Modify: `backend/nexus/app/modules/auth/router.py` (remove `complete_invite` handler)
- Modify: `backend/nexus/app/modules/auth/schemas.py` (remove `CompleteInviteRequest`, `CompleteInviteResponse`)
- Modify: `backend/nexus/tests/test_auth_endpoints.py` (remove `test_complete_invite_no_auth`)
- Modify: `backend/nexus/app/database.py` (docstring wording)

- [ ] **Step 7.1: Remove the handler**

  In `backend/nexus/app/modules/auth/router.py`, delete the entire `complete_invite` function (baseline lines 56-160). Also remove the now-unused imports from `app.modules.auth.schemas`:
  ```python
  from app.modules.auth.schemas import (
      AcceptInviteRequest,
      AcceptInviteResponse,
      MeResponse,
      RoleAssignmentResponse,
      VerifyInviteResponse,
  )
  ```
  (`CompleteInviteRequest` and `CompleteInviteResponse` are removed from the import list.)

- [ ] **Step 7.2: Remove the unused schemas**

  In `backend/nexus/app/modules/auth/schemas.py`, delete the `CompleteInviteRequest` and `CompleteInviteResponse` classes.

- [ ] **Step 7.3: Remove the legacy test**

  Modify `backend/nexus/tests/test_auth_endpoints.py` — delete `test_complete_invite_no_auth` (lines 17-20 in baseline). Add a replacement that smoke-tests the new endpoint shape:
  ```python
  @pytest.mark.asyncio
  async def test_accept_invite_missing_body_fields():
      async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
          resp = await client.post("/api/auth/accept-invite", json={})
      assert resp.status_code == 422  # pydantic validation


  @pytest.mark.asyncio
  async def test_accept_invite_bad_token_returns_401():
      async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
          resp = await client.post(
              "/api/auth/accept-invite",
              json={"raw_token": "does-not-exist", "password": "hunter2hunter2"},
          )
      assert resp.status_code == 401
      assert resp.json()["detail"] == "Invalid or expired invite"
  ```

- [ ] **Step 7.4: Update database.py docstrings**

  Modify `backend/nexus/app/database.py`. Find the two mentions of `complete-invite` in the `get_bypass_session` and `get_bypass_db` docstrings (around lines 103 and 119) and rename them to `accept-invite`. Example:
  ```python
  """Yield a session with RLS bypass enabled.

  Used ONLY for: admin routes, accept-invite, and onboarding completion.
  Never use on endpoints reachable by regular client users.
  """
  ```

- [ ] **Step 7.5: Also update the `CLAUDE.md` reference (if still valid)**

  ```bash
  rg -n "complete[-_]invite" backend/nexus/
  ```
  Expected output after the above changes: only matches in Alembic migration `0009_phase1_rls_full_command.py` (comment, immutable) and this plan file. If any live code still references `complete-invite`, fix it and re-run.

  Also update `backend/nexus/CLAUDE.md` line 61:
  ```
  │       │   └── router.py        ← /api/auth/* (verify-invite, accept-invite, me, onboarding/complete)
  ```

- [ ] **Step 7.6: Run the backend suite**

  ```bash
  docker compose run --rm nexus pytest -x \
    --deselect tests/test_auth_service.py::TestVerifyAccessToken::test_valid_token_returns_payload \
    --deselect tests/test_auth_service.py::TestVerifyAccessToken::test_projectx_admin_token \
    --deselect tests/test_auth_service.py::TestVerifyAccessToken::test_empty_custom_claims_returns_defaults \
    --deselect tests/test_session_schemas.py::test_pre_check_response_round_trips
  ```
  Expected: all green (baseline + the new smoke tests).

- [ ] **Step 7.7: Commit**

  ```bash
  git add backend/nexus/app/modules/auth/router.py backend/nexus/app/modules/auth/schemas.py \
          backend/nexus/tests/test_auth_endpoints.py backend/nexus/app/database.py \
          backend/nexus/CLAUDE.md
  git commit -m "refactor(auth): remove legacy complete-invite endpoint"
  ```

---

## Task 8: Accept-invite integration tests

**Goal:** Exercise the full `/api/auth/accept-invite` flow against a real DB + mocked `AuthProvider`. Covers: happy path (new user), already-exists fallback, invalid invite, expired invite, DB-write-failure compensation.

**Files:**
- Create: `backend/nexus/tests/test_accept_invite.py`

- [ ] **Step 8.1: Write failing tests**

  File: `backend/nexus/tests/test_accept_invite.py`
  ```python
  """Integration tests for POST /api/auth/accept-invite.

  AuthProvider is mocked via a fake that records calls. The DB is real
  (via the standard bypass_db_session fixture) so the handler's DB
  writes and compensation paths are exercised end-to-end.
  """
  from __future__ import annotations

  import hashlib
  import secrets
  import uuid as uuid_mod
  from datetime import datetime, timedelta, timezone
  from unittest.mock import AsyncMock, patch

  import pytest
  from httpx import ASGITransport, AsyncClient
  from sqlalchemy import select, text

  from app.main import app
  from app.models import Client, User, UserInvite
  from app.modules.auth.admin.base import (
      AuthProviderError,
      SessionTokens,
      UserAlreadyExistsError,
      UserIdentity,
  )

  pytestmark = pytest.mark.asyncio


  # ---------- helpers ----------


  async def _seed_client_and_invite(
      db,
      *,
      email: str,
      expires_in_hours: float = 48,
      is_super_admin: bool = False,
  ) -> tuple[uuid_mod.UUID, str]:
      """Create a client row and a pending invite; return (tenant_id, raw_token)."""
      tenant_id = uuid_mod.uuid4()
      await db.execute(
          text(
              "INSERT INTO clients (id, name, workspace_mode) "
              "VALUES (:id, :name, 'enterprise')"
          ),
          {"id": tenant_id, "name": "Test Co"},
      )
      raw_token = secrets.token_urlsafe(32)
      token_hash = hashlib.sha256(raw_token.encode()).hexdigest()
      invite_id = uuid_mod.uuid4()
      projectx_admin_id = uuid_mod.uuid4() if is_super_admin else None
      await db.execute(
          text(
              "INSERT INTO user_invites "
              "(id, tenant_id, email, token_hash, status, expires_at, "
              " invited_by, projectx_admin_id) "
              "VALUES (:id, :tid, :email, :hash, 'pending', "
              "        NOW() + make_interval(hours => :hours), "
              "        :invited_by, :padmin)"
          ),
          {
              "id": invite_id,
              "tid": tenant_id,
              "email": email,
              "hash": token_hash,
              "hours": expires_in_hours,
              "invited_by": uuid_mod.uuid4(),
              "padmin": projectx_admin_id,
          },
      )
      await db.commit()
      return tenant_id, raw_token


  class _FakeProvider:
      """In-memory stand-in for SupabaseAuthProvider.

      `existing_users` pre-seeds the "find by email" path; calls are
      recorded in `calls` for assertion.
      """

      def __init__(
          self,
          *,
          existing_users: dict[str, UserIdentity] | None = None,
          sign_in_tokens: SessionTokens | None = None,
      ):
          self.existing_users = existing_users or {}
          self.sign_in_tokens = sign_in_tokens or SessionTokens(
              access_token="at-new", refresh_token="rt-new", expires_in=3600
          )
          self.calls: list[tuple[str, tuple]] = []

      async def create_user(self, email: str, password: str) -> UserIdentity:
          self.calls.append(("create_user", (email, password)))
          if email in self.existing_users:
              raise UserAlreadyExistsError(email)
          return UserIdentity(id=str(uuid_mod.uuid4()), email=email)

      async def find_user_by_email(self, email: str) -> UserIdentity | None:
          self.calls.append(("find_user_by_email", (email,)))
          return self.existing_users.get(email)

      async def update_user_password(self, user_id: str, password: str) -> None:
          self.calls.append(("update_user_password", (user_id, password)))

      async def sign_in_with_password(
          self, email: str, password: str
      ) -> SessionTokens:
          self.calls.append(("sign_in_with_password", (email, password)))
          return self.sign_in_tokens

      async def delete_user(self, user_id: str) -> None:
          self.calls.append(("delete_user", (user_id,)))


  # ---------- tests ----------


  async def test_accept_invite_happy_path_creates_super_admin(
      bypass_db_session,
  ):
      tenant_id, raw_token = await _seed_client_and_invite(
          bypass_db_session, email="new@co.com", is_super_admin=True
      )
      provider = _FakeProvider()

      with patch(
          "app.modules.auth.router.get_auth_provider", return_value=provider
      ):
          async with AsyncClient(
              transport=ASGITransport(app=app), base_url="http://test"
          ) as client:
              resp = await client.post(
                  "/api/auth/accept-invite",
                  json={"raw_token": raw_token, "password": "hunter2hunter2"},
              )
      assert resp.status_code == 200
      body = resp.json()
      assert body["access_token"] == "at-new"
      assert body["refresh_token"] == "rt-new"
      assert body["expires_in"] == 3600
      assert body["redirect_to"] == "/onboarding"

      # Provider call ordering: create → sign_in. No update_user_password
      # (user didn't exist). No delete_user (no compensation).
      methods = [c[0] for c in provider.calls]
      assert methods == ["create_user", "sign_in_with_password"]

      # DB state: invite accepted, user row exists, clients.super_admin_id set.
      async with bypass_db_session.begin():
          invite = (
              await bypass_db_session.execute(
                  select(UserInvite).where(UserInvite.tenant_id == tenant_id)
              )
          ).scalar_one()
          assert invite.status == "accepted"
          user_row = (
              await bypass_db_session.execute(
                  select(User).where(User.tenant_id == tenant_id)
              )
          ).scalar_one()
          assert user_row.email == "new@co.com"
          client_row = (
              await bypass_db_session.execute(
                  select(Client).where(Client.id == tenant_id)
              )
          ).scalar_one()
          assert client_row.super_admin_id == user_row.id


  async def test_accept_invite_already_existing_auth_user_updates_password(
      bypass_db_session,
  ):
      _, raw_token = await _seed_client_and_invite(
          bypass_db_session, email="existing@co.com"
      )
      existing_id = str(uuid_mod.uuid4())
      provider = _FakeProvider(
          existing_users={
              "existing@co.com": UserIdentity(id=existing_id, email="existing@co.com")
          }
      )

      with patch(
          "app.modules.auth.router.get_auth_provider", return_value=provider
      ):
          async with AsyncClient(
              transport=ASGITransport(app=app), base_url="http://test"
          ) as client:
              resp = await client.post(
                  "/api/auth/accept-invite",
                  json={"raw_token": raw_token, "password": "hunter2hunter2"},
              )
      assert resp.status_code == 200
      methods = [c[0] for c in provider.calls]
      assert methods == [
          "create_user",
          "find_user_by_email",
          "update_user_password",
          "sign_in_with_password",
      ]


  async def test_accept_invite_bad_token_returns_401():
      async with AsyncClient(
          transport=ASGITransport(app=app), base_url="http://test"
      ) as client:
          resp = await client.post(
              "/api/auth/accept-invite",
              json={"raw_token": "does-not-exist", "password": "hunter2hunter2"},
          )
      assert resp.status_code == 401
      assert resp.json()["detail"] == "Invalid or expired invite"


  async def test_accept_invite_expired_token_returns_401(bypass_db_session):
      _, raw_token = await _seed_client_and_invite(
          bypass_db_session,
          email="expired@co.com",
          expires_in_hours=-1,  # already expired
      )
      async with AsyncClient(
          transport=ASGITransport(app=app), base_url="http://test"
      ) as client:
          resp = await client.post(
              "/api/auth/accept-invite",
              json={"raw_token": raw_token, "password": "hunter2hunter2"},
          )
      assert resp.status_code == 401


  async def test_accept_invite_db_failure_triggers_compensation(
      bypass_db_session, monkeypatch
  ):
      _, raw_token = await _seed_client_and_invite(
          bypass_db_session, email="db-fail@co.com", is_super_admin=True
      )
      provider = _FakeProvider()

      # Force the root-unit creation to raise, after the auth user has
      # been created and signed in. The handler must call delete_user.
      async def _raise(*args, **kwargs):
          raise RuntimeError("simulated DB failure")

      with patch(
          "app.modules.auth.router.get_auth_provider", return_value=provider
      ), patch(
          "app.modules.org_units.service.create_org_unit", new=AsyncMock(side_effect=_raise)
      ):
          async with AsyncClient(
              transport=ASGITransport(app=app), base_url="http://test"
          ) as client:
              resp = await client.post(
                  "/api/auth/accept-invite",
                  json={"raw_token": raw_token, "password": "hunter2hunter2"},
              )
      assert resp.status_code == 500

      # Compensation was called.
      assert any(c[0] == "delete_user" for c in provider.calls)

      # DB state: invite is STILL pending (transaction rolled back).
      invite = (
          await bypass_db_session.execute(
              select(UserInvite).where(UserInvite.email == "db-fail@co.com")
          )
      ).scalar_one()
      assert invite.status == "pending"


  async def test_accept_invite_provider_exhaustion_propagates_502(
      bypass_db_session,
  ):
      _, raw_token = await _seed_client_and_invite(
          bypass_db_session, email="provider-err@co.com"
      )

      class _FailingProvider(_FakeProvider):
          async def create_user(self, email, password):
              self.calls.append(("create_user", (email, password)))
              raise AuthProviderError("upstream 503")

      provider = _FailingProvider()
      with patch(
          "app.modules.auth.router.get_auth_provider", return_value=provider
      ):
          async with AsyncClient(
              transport=ASGITransport(app=app), base_url="http://test"
          ) as client:
              resp = await client.post(
                  "/api/auth/accept-invite",
                  json={"raw_token": raw_token, "password": "hunter2hunter2"},
              )
      assert resp.status_code == 502
  ```

- [ ] **Step 8.2: Run tests — expect FAIL until test-setup foreign-key columns align**

  ```bash
  cd /home/ishant/Projects/ProjectX/.worktrees/cleanup-batch-3/backend/nexus
  docker compose run --rm nexus pytest tests/test_accept_invite.py -xvs
  ```
  Likely first failure: the `invited_by` FK on `user_invites` references `users(id)` which doesn't exist at seed time for the super-admin path (before there's any user). If that's the case, drop the FK seed value (use a nullable column if the schema permits) OR seed a placeholder user row first. Check existing `conftest.py` helpers in `backend/nexus/tests/` — if there's a `seed_invite` or `seed_pending_invite` helper already, prefer it over the inline `_seed_client_and_invite` above.

  If the test passes first-run, great — proceed. If not, adjust the seed helper (keep the test logic identical) until tests pass.

- [ ] **Step 8.3: Run tests — expect PASS**

  ```bash
  docker compose run --rm nexus pytest tests/test_accept_invite.py -xvs
  ```
  Expected: all 5 tests pass.

- [ ] **Step 8.4: Commit**

  ```bash
  git add backend/nexus/tests/test_accept_invite.py
  git commit -m "test(auth): integration tests for accept-invite flow"
  ```

---

## Phase 3 — Frontend invite flow (Tasks 9–10)

## Task 9: Extend `authApi` with `acceptInvite`

**Goal:** Typed API namespace entry matching the backend contract. The page only knows about the typed function.

**Files:**
- Modify: `frontend/app/lib/api/auth.ts`

- [ ] **Step 9.1: Add types + method**

  Modify `frontend/app/lib/api/auth.ts`. After the existing `MeResponse` interface:
  ```typescript
  export interface AcceptInviteRequest {
    raw_token: string
    password: string
  }

  export interface AcceptInviteResponse {
    access_token: string
    refresh_token: string
    expires_in: number
    redirect_to: string
  }
  ```

  Extend `authApi`:
  ```typescript
  export const authApi = {
    me: (
      token: string,
      opts?: { signal?: AbortSignal },
    ): Promise<MeResponse> =>
      apiFetch<MeResponse>('/api/auth/me', {
        token,
        signal: opts?.signal,
      }),

    acceptInvite: (
      body: AcceptInviteRequest,
      opts?: { signal?: AbortSignal },
    ): Promise<AcceptInviteResponse> =>
      apiFetch<AcceptInviteResponse>('/api/auth/accept-invite', {
        method: 'POST',
        body: JSON.stringify(body),
        signal: opts?.signal,
      }),
  }
  ```

- [ ] **Step 9.2: Verify type-check**

  ```bash
  cd /home/ishant/Projects/ProjectX/.worktrees/cleanup-batch-3/frontend/app
  npm run type-check
  ```
  Expected: 0 errors.

- [ ] **Step 9.3: Commit**

  ```bash
  git add frontend/app/lib/api/auth.ts
  git commit -m "feat(auth): add authApi.acceptInvite wrapper"
  ```

---

## Task 10: Rewrite `invite/page.tsx`

**Goal:** The page no longer touches `supabase.auth.signUp` / `signInWithPassword`. It POSTs `{raw_token, password}` to the backend and uses `supabase.auth.setSession` to install the cookie session from the tokens in the response. `router.refresh()` then revalidates server components with the new cookie.

**Files:**
- Modify: `frontend/app/app/(auth)/invite/page.tsx`
- Create: `frontend/app/tests/auth/invite-page.test.tsx`

- [ ] **Step 10.1: Write a failing test**

  File: `frontend/app/tests/auth/invite-page.test.tsx`
  ```tsx
  /** Invite page must call authApi.acceptInvite — never supabase.auth.signUp. */
  import { describe, it, expect, vi, beforeEach } from 'vitest'
  import { render, screen, fireEvent, waitFor } from '@testing-library/react'
  import userEvent from '@testing-library/user-event'

  const mockAcceptInvite = vi.fn()
  const mockSetSession = vi.fn()
  const mockSignUp = vi.fn()
  const mockSignInWithPassword = vi.fn()
  const mockRouterPush = vi.fn()
  const mockRouterRefresh = vi.fn()

  vi.mock('next/navigation', () => ({
    useSearchParams: () => new URLSearchParams({ token: 'raw-token-abc' }),
    useRouter: () => ({ push: mockRouterPush, refresh: mockRouterRefresh }),
  }))

  vi.mock('@/lib/api/client', () => ({
    apiFetch: vi.fn().mockImplementation(async (path: string) => {
      if (path.startsWith('/api/auth/verify-invite')) {
        return { email: 'user@example.com', client_name: 'TestCo' }
      }
      throw new Error(`Unexpected apiFetch call: ${path}`)
    }),
  }))

  vi.mock('@/lib/api/auth', () => ({
    authApi: {
      me: vi.fn(),
      acceptInvite: (...args: unknown[]) => mockAcceptInvite(...args),
    },
  }))

  vi.mock('@/lib/supabase/client', () => ({
    createClient: () => ({
      auth: {
        signUp: mockSignUp,
        signInWithPassword: mockSignInWithPassword,
        setSession: mockSetSession,
      },
    }),
  }))

  import InvitePage from '@/app/(auth)/invite/page'

  describe('InvitePage (B3)', () => {
    beforeEach(() => {
      mockAcceptInvite.mockReset()
      mockSetSession.mockReset()
      mockSignUp.mockReset()
      mockSignInWithPassword.mockReset()
      mockRouterPush.mockReset()
      mockRouterRefresh.mockReset()
    })

    it('submits to backend and calls setSession with returned tokens', async () => {
      mockAcceptInvite.mockResolvedValueOnce({
        access_token: 'at-xyz',
        refresh_token: 'rt-xyz',
        expires_in: 3600,
        redirect_to: '/onboarding',
      })

      render(<InvitePage />)

      await screen.findByText(/set up your account/i)
      const user = userEvent.setup()
      const inputs = screen.getAllByPlaceholderText(/enter a password|^$/i)
      // Fill both password + confirm
      await user.type(screen.getByLabelText(/set password/i), 'hunter2hunter2')
      await user.type(screen.getByLabelText(/confirm password/i), 'hunter2hunter2')
      await user.click(
        screen.getByRole('button', { name: /create account/i }),
      )

      await waitFor(() => {
        expect(mockAcceptInvite).toHaveBeenCalledWith({
          raw_token: 'raw-token-abc',
          password: 'hunter2hunter2',
        })
      })
      expect(mockSetSession).toHaveBeenCalledWith({
        access_token: 'at-xyz',
        refresh_token: 'rt-xyz',
      })
      expect(mockSignUp).not.toHaveBeenCalled()
      expect(mockSignInWithPassword).not.toHaveBeenCalled()
      expect(mockRouterPush).toHaveBeenCalledWith('/onboarding')
    })

    it('rejects open-redirect targets from the backend', async () => {
      mockAcceptInvite.mockResolvedValueOnce({
        access_token: 'at-xyz',
        refresh_token: 'rt-xyz',
        expires_in: 3600,
        redirect_to: 'https://evil.example.com/steal',
      })

      render(<InvitePage />)
      await screen.findByText(/set up your account/i)
      const user = userEvent.setup()
      await user.type(screen.getByLabelText(/set password/i), 'hunter2hunter2')
      await user.type(screen.getByLabelText(/confirm password/i), 'hunter2hunter2')
      await user.click(
        screen.getByRole('button', { name: /create account/i }),
      )

      await waitFor(() => {
        expect(mockRouterPush).toHaveBeenCalledWith('/')
      })
    })
  })
  ```

- [ ] **Step 10.2: Run test — expect FAIL**

  ```bash
  npm run test -- tests/auth/invite-page.test.tsx
  ```
  Expected: FAIL — current page calls `supabase.auth.signUp`.

- [ ] **Step 10.3: Rewrite the submit handler**

  Modify `frontend/app/app/(auth)/invite/page.tsx`. Replace the top-of-file imports:
  ```typescript
  import { Suspense, useEffect, useState } from "react";
  import { useSearchParams, useRouter } from "next/navigation";
  import { createClient } from "@/lib/supabase/client";
  import { apiFetch } from "@/lib/api/client";
  import { authApi } from "@/lib/api/auth";
  ```

  Replace the entire `handleSubmit` function (baseline lines 82-155) with:
  ```typescript
    async function handleSubmit(e: React.FormEvent) {
      e.preventDefault();
      setError("");

      if (password !== confirmPassword) {
        setError("Passwords do not match");
        return;
      }
      if (password.length < 8) {
        setError("Password must be at least 8 characters");
        return;
      }

      setState("submitting");

      try {
        const result = await authApi.acceptInvite({
          raw_token: rawToken,
          password,
        });

        // Install the session cookie client-side so middleware / server
        // components see the new auth state on the next navigation.
        const supabase = createClient();
        const { error: sessionError } = await supabase.auth.setSession({
          access_token: result.access_token,
          refresh_token: result.refresh_token,
        });
        if (sessionError) {
          throw new Error(sessionError.message);
        }

        // Open-redirect guard — the backend is trusted, but defense in
        // depth: only same-origin relative paths.
        const safeRedirect =
          result.redirect_to?.startsWith("/") &&
          !result.redirect_to.startsWith("//")
            ? result.redirect_to
            : "/";
        router.push(safeRedirect);
        router.refresh();
      } catch (err) {
        setError(
          err instanceof Error ? err.message : "Failed to create account",
        );
        setState("ready");
      }
    }
  ```

- [ ] **Step 10.4: Ensure the labels are associated with inputs**

  For the test `getByLabelText` queries to work, the form must use `<label htmlFor>` / `<input id>` pairs. Check the current form: labels are `<label className="px-label">Set password</label>` with no `htmlFor`. Add `htmlFor` + `id`:
  ```tsx
  <label className="px-label" htmlFor="invite-password">Set password</label>
  ...
  <input
    id="invite-password"
    type={showPassword ? "text" : "password"}
    ...
  />
  ```
  Same for the confirm password field (`id="invite-confirm-password"`).

  This is an accessibility improvement in addition to making the test work — label/input association is WCAG 1.3.1.

- [ ] **Step 10.5: Run tests — expect PASS**

  ```bash
  npm run test -- tests/auth/invite-page.test.tsx
  ```
  Expected: both tests pass.

- [ ] **Step 10.6: Run full type-check + lint + test**

  ```bash
  npm run type-check && npm run lint && npm run test
  ```
  Expected: all green.

- [ ] **Step 10.7: Commit**

  ```bash
  git add frontend/app/app/(auth)/invite/page.tsx frontend/app/tests/auth/invite-page.test.tsx
  git commit -m "feat(invite): rewrite acceptance flow to call backend accept-invite"
  ```

---

## Phase 4 — Frontend session hardening (Tasks 11–15)

## Task 11: Global 401 handler

**Goal:** A single function `handleAuthError(err, router)` that detects auth failures in any Query or Mutation error, signs the user out, toasts, and redirects to `/login`. Wired via TanStack Query's `QueryCache.onError` + `MutationCache.onError`.

**Files:**
- Create: `frontend/app/lib/auth/handle-error.ts`
- Create: `frontend/app/tests/auth/handle-error.test.ts`

- [ ] **Step 11.1: Write failing test**

  File: `frontend/app/tests/auth/handle-error.test.ts`
  ```ts
  import { describe, it, expect, vi, beforeEach } from 'vitest'
  import { ApiError } from '@/lib/api/client'
  import type { AppRouter } from '@/lib/auth/handle-error'

  const mockSignOut = vi.fn()
  const mockToast = vi.fn()

  vi.mock('@/lib/supabase/client', () => ({
    createClient: () => ({ auth: { signOut: mockSignOut } }),
  }))
  vi.mock('sonner', () => ({
    toast: { error: (...args: unknown[]) => mockToast(...args) },
  }))

  import { handleAuthError } from '@/lib/auth/handle-error'

  describe('handleAuthError', () => {
    const router: AppRouter = { push: vi.fn() }

    beforeEach(() => {
      mockSignOut.mockReset()
      mockToast.mockReset()
      ;(router.push as ReturnType<typeof vi.fn>).mockReset()
    })

    it('signs out, toasts, and redirects on 401 ApiError', async () => {
      const err = new ApiError('Unauthorized', 401)
      const matched = await handleAuthError(err, router)
      expect(matched).toBe(true)
      expect(mockSignOut).toHaveBeenCalledOnce()
      expect(mockToast).toHaveBeenCalledWith(
        'Session expired. Please log in again.',
      )
      expect(router.push).toHaveBeenCalledWith('/login')
    })

    it('matches "No active Supabase session" Error message', async () => {
      const err = new Error('No active Supabase session')
      const matched = await handleAuthError(err, router)
      expect(matched).toBe(true)
      expect(router.push).toHaveBeenCalledWith('/login')
    })

    it('no-op for unrelated errors', async () => {
      const err = new Error('Network timeout')
      const matched = await handleAuthError(err, router)
      expect(matched).toBe(false)
      expect(mockSignOut).not.toHaveBeenCalled()
      expect(router.push).not.toHaveBeenCalled()
    })

    it('ignores non-401 ApiErrors', async () => {
      const err = new ApiError('Server error', 500)
      const matched = await handleAuthError(err, router)
      expect(matched).toBe(false)
      expect(mockSignOut).not.toHaveBeenCalled()
    })
  })
  ```

- [ ] **Step 11.2: Run test — expect FAIL**

  ```bash
  npm run test -- tests/auth/handle-error.test.ts
  ```
  Expected: FAIL — module does not exist.

- [ ] **Step 11.3: Implement `handleAuthError`**

  File: `frontend/app/lib/auth/handle-error.ts`
  ```ts
  /**
   * Global auth-error sink. Any 401 bubbling out of a TanStack Query
   * fetcher or mutation ends up here. Single responsibility: sign out,
   * toast once, redirect to /login.
   *
   * `AppRouter` is a minimal shape of next/navigation's AppRouter — we
   * depend on `push` only.
   */
  import { toast } from 'sonner'

  import { ApiError } from '@/lib/api/client'
  import { createClient } from '@/lib/supabase/client'

  export interface AppRouter {
    push: (href: string) => void
  }

  let _redirectInFlight = false

  /** Returns true if the error matched and was handled. */
  export async function handleAuthError(
    err: unknown,
    router: AppRouter,
  ): Promise<boolean> {
    const isApi401 = err instanceof ApiError && err.status === 401
    const isTokenMissing =
      err instanceof Error && err.message === 'No active Supabase session'
    if (!isApi401 && !isTokenMissing) {
      return false
    }

    // Deduplicate across concurrent query/mutation failures.
    if (_redirectInFlight) {
      return true
    }
    _redirectInFlight = true
    try {
      const supabase = createClient()
      await supabase.auth.signOut()
    } catch {
      // signOut failures shouldn't block the redirect.
    }
    toast.error('Session expired. Please log in again.')
    router.push('/login')
    // Reset the lock on next tick so legitimate re-login triggers the
    // handler again if something goes wrong post-redirect.
    setTimeout(() => {
      _redirectInFlight = false
    }, 500)
    return true
  }
  ```

- [ ] **Step 11.4: Run test — expect PASS**

  ```bash
  npm run test -- tests/auth/handle-error.test.ts
  ```
  Expected: all 4 tests pass.

- [ ] **Step 11.5: Commit**

  ```bash
  git add frontend/app/lib/auth/handle-error.ts frontend/app/tests/auth/handle-error.test.ts
  git commit -m "feat(auth): add global 401 handler (handleAuthError)"
  ```

---

## Task 12: Wire global handler into `DashboardProviders`

**Goal:** Every query and mutation error flows through `handleAuthError`. We pass the router via the hook (`useRouter()`) into a stable closure that the `QueryCache` / `MutationCache` reference.

**Files:**
- Modify: `frontend/app/components/dashboard/providers.tsx`

- [ ] **Step 12.1: Rewrite `DashboardProviders`**

  Modify `frontend/app/components/dashboard/providers.tsx`:
  ```tsx
  'use client'

  import {
    MutationCache,
    QueryCache,
    QueryClient,
    QueryClientProvider,
  } from '@tanstack/react-query'
  import { ReactQueryDevtools } from '@tanstack/react-query-devtools'
  import { useRouter } from 'next/navigation'
  import { useRef, useState } from 'react'

  import { Toaster } from '@/components/px'
  import { handleAuthError, type AppRouter } from '@/lib/auth/handle-error'

  export function DashboardProviders({ children }: { children: React.ReactNode }) {
    const router = useRouter()
    // Hold the router in a ref so QueryCache/MutationCache callbacks
    // always see the current instance without re-creating QueryClient.
    const routerRef = useRef<AppRouter>(router)
    routerRef.current = router

    const [queryClient] = useState(() => {
      const onError = (err: unknown) => {
        void handleAuthError(err, routerRef.current)
      }
      return new QueryClient({
        queryCache: new QueryCache({ onError }),
        mutationCache: new MutationCache({ onError }),
        defaultOptions: {
          queries: {
            staleTime: 10_000,
            refetchOnWindowFocus: false,
          },
        },
      })
    })

    return (
      <QueryClientProvider client={queryClient}>
        {children}
        <Toaster />
        {process.env.NODE_ENV === 'development' && <ReactQueryDevtools />}
      </QueryClientProvider>
    )
  }
  ```

- [ ] **Step 12.2: Smoke-run dev server**

  ```bash
  npm run type-check && npm run lint && npm run build
  ```
  Expected: all green.

- [ ] **Step 12.3: Commit**

  ```bash
  git add frontend/app/components/dashboard/providers.tsx
  git commit -m "feat(auth): wire global 401 handler into QueryClient"
  ```

---

## Task 13: `SessionGuard` component

**Goal:** Client component mounted inside `DashboardProviders`. On mount, validates session via `supabase.auth.getUser()` (hits the auth server — NOT `getSession()` which reads the cookie only). Re-validates on `document.visibilitychange` when the tab becomes visible after being hidden. Listens to `supabase.auth.onAuthStateChange('SIGNED_OUT')` for cross-tab signout.

**Files:**
- Create: `frontend/app/components/dashboard/SessionGuard.tsx`
- Create: `frontend/app/tests/auth/session-guard.test.tsx`

- [ ] **Step 13.1: Write failing test**

  File: `frontend/app/tests/auth/session-guard.test.tsx`
  ```tsx
  import { describe, it, expect, vi, beforeEach } from 'vitest'
  import { render, waitFor } from '@testing-library/react'

  const mockGetUser = vi.fn()
  const mockRouterPush = vi.fn()
  let authStateCallback: ((event: string) => void) | null = null
  const mockOnAuthStateChange = vi.fn((cb) => {
    authStateCallback = cb
    return { data: { subscription: { unsubscribe: vi.fn() } } }
  })

  vi.mock('next/navigation', () => ({
    useRouter: () => ({ push: mockRouterPush }),
  }))
  vi.mock('@/lib/supabase/client', () => ({
    createClient: () => ({
      auth: {
        getUser: mockGetUser,
        onAuthStateChange: mockOnAuthStateChange,
      },
    }),
  }))

  import { SessionGuard } from '@/components/dashboard/SessionGuard'

  describe('SessionGuard', () => {
    beforeEach(() => {
      mockGetUser.mockReset()
      mockRouterPush.mockReset()
      mockOnAuthStateChange.mockReset()
      authStateCallback = null
      Object.defineProperty(document, 'visibilityState', {
        configurable: true,
        get: () => 'visible',
      })
    })

    it('redirects to /login if getUser returns null user', async () => {
      mockGetUser.mockResolvedValueOnce({ data: { user: null }, error: null })
      render(<SessionGuard />)
      await waitFor(() => {
        expect(mockRouterPush).toHaveBeenCalledWith('/login')
      })
    })

    it('does not redirect when getUser returns a user', async () => {
      mockGetUser.mockResolvedValueOnce({
        data: { user: { id: 'u1', email: 'u@x' } },
        error: null,
      })
      render(<SessionGuard />)
      // Give the effect a chance to run.
      await new Promise((r) => setTimeout(r, 10))
      expect(mockRouterPush).not.toHaveBeenCalled()
    })

    it('redirects on SIGNED_OUT auth event', async () => {
      mockGetUser.mockResolvedValueOnce({
        data: { user: { id: 'u1' } },
        error: null,
      })
      render(<SessionGuard />)
      await waitFor(() => expect(authStateCallback).not.toBeNull())
      authStateCallback!('SIGNED_OUT')
      await waitFor(() => {
        expect(mockRouterPush).toHaveBeenCalledWith('/login')
      })
    })
  })
  ```

- [ ] **Step 13.2: Run test — expect FAIL**

  ```bash
  npm run test -- tests/auth/session-guard.test.tsx
  ```
  Expected: FAIL — module does not exist.

- [ ] **Step 13.3: Implement `SessionGuard`**

  File: `frontend/app/components/dashboard/SessionGuard.tsx`
  ```tsx
  'use client'

  /**
   * Client-side session validation. Mounted inside DashboardProviders.
   *
   * Responsibilities:
   *  - On mount: call supabase.auth.getUser() once. getUser() contacts
   *    the auth server (NOT just the cookie). If the server says the
   *    session is invalid/expired, push to /login.
   *  - On `visibilitychange` → `visible` after having been hidden for
   *    5+ minutes: re-validate via getUser(). This catches the "user
   *    walked away and their session was revoked" case.
   *  - Subscribe to supabase.auth.onAuthStateChange; on SIGNED_OUT →
   *    push to /login (covers cross-tab sign-out).
   *
   * This is the OOB validator that lets `getFreshSupabaseToken()` stay
   * fast on the hot path (cookie read only). Invariant: by the time
   * getFreshSupabaseToken() returns, SessionGuard has validated within
   * the last visibility window or a fresh onAuthStateChange event.
   */
  import { useRouter } from 'next/navigation'
  import { useEffect, useRef } from 'react'

  import { createClient } from '@/lib/supabase/client'

  const REVALIDATE_AFTER_HIDDEN_MS = 5 * 60 * 1000  // 5 minutes

  export function SessionGuard() {
    const router = useRouter()
    const hiddenSinceRef = useRef<number | null>(null)

    useEffect(() => {
      const supabase = createClient()

      const validate = async () => {
        const { data, error } = await supabase.auth.getUser()
        if (error || !data.user) {
          router.push('/login')
        }
      }

      // Initial validation.
      void validate()

      // Cross-tab / onAuthStateChange.
      const { data: authSub } = supabase.auth.onAuthStateChange((event) => {
        if (event === 'SIGNED_OUT') {
          router.push('/login')
        }
      })

      // Visibility-based re-validation.
      const handleVisibility = () => {
        if (document.visibilityState === 'hidden') {
          hiddenSinceRef.current = Date.now()
          return
        }
        if (document.visibilityState === 'visible') {
          const hiddenSince = hiddenSinceRef.current
          hiddenSinceRef.current = null
          if (hiddenSince && Date.now() - hiddenSince >= REVALIDATE_AFTER_HIDDEN_MS) {
            void validate()
          }
        }
      }
      document.addEventListener('visibilitychange', handleVisibility)

      return () => {
        authSub.subscription.unsubscribe()
        document.removeEventListener('visibilitychange', handleVisibility)
      }
    }, [router])

    return null
  }
  ```

- [ ] **Step 13.4: Run test — expect PASS**

  ```bash
  npm run test -- tests/auth/session-guard.test.tsx
  ```
  Expected: all 3 tests pass.

- [ ] **Step 13.5: Commit**

  ```bash
  git add frontend/app/components/dashboard/SessionGuard.tsx frontend/app/tests/auth/session-guard.test.tsx
  git commit -m "feat(auth): add SessionGuard client component"
  ```

---

## Task 14: Mount `SessionGuard` in `DashboardProviders`

**Goal:** Have SessionGuard cover the dashboard surface. Invite and login pages are deliberately outside — they need to operate without a session.

**Files:**
- Modify: `frontend/app/components/dashboard/providers.tsx`

- [ ] **Step 14.1: Add the mount**

  Modify `frontend/app/components/dashboard/providers.tsx`. Import and render `SessionGuard` as a sibling inside `QueryClientProvider`:
  ```tsx
  import { SessionGuard } from '@/components/dashboard/SessionGuard'
  ```
  ```tsx
  return (
    <QueryClientProvider client={queryClient}>
      <SessionGuard />
      {children}
      <Toaster />
      {process.env.NODE_ENV === 'development' && <ReactQueryDevtools />}
    </QueryClientProvider>
  )
  ```

- [ ] **Step 14.2: Run tests + type-check**

  ```bash
  npm run type-check && npm run test
  ```
  Expected: all green.

- [ ] **Step 14.3: Commit**

  ```bash
  git add frontend/app/components/dashboard/providers.tsx
  git commit -m "feat(auth): mount SessionGuard inside DashboardProviders"
  ```

---

## Task 15: Migrate `profile/page.tsx` and `onboarding/page.tsx` to `getFreshSupabaseToken`

**Goal:** B3.2 sweep. Two small pages that still use raw `getSession()`. Migrating them to `getFreshSupabaseToken` routes every token fetch through one place, which future-proofs any additional validation we want to add.

**Files:**
- Modify: `frontend/app/app/(dashboard)/profile/page.tsx`
- Modify: `frontend/app/app/onboarding/page.tsx`

- [ ] **Step 15.1: Migrate profile page**

  Modify `frontend/app/app/(dashboard)/profile/page.tsx`. Find the `useEffect` load block (baseline lines 58-73) and replace:
  ```tsx
    useEffect(() => {
      async function load() {
        try {
          const token = await getFreshSupabaseToken();
          const data = await authApi.me(token);
          setMe(data);
        } catch {
          // SessionGuard + handleAuthError will handle 401 globally;
          // swallowing here prevents a double-redirect.
        } finally {
          setLoading(false);
        }
      }
      load();
    }, []);
  ```

  Add the import at the top of the file:
  ```tsx
  import { getFreshSupabaseToken } from "@/lib/auth/tokens";
  ```

  Remove the now-unused `createClient` import if no other code in the file uses it (verify by searching `createClient(` in the file; if the `handleSignOut` path still uses it, keep the import).

- [ ] **Step 15.2: Migrate onboarding page**

  Modify `frontend/app/app/onboarding/page.tsx`. Replace the `getToken` helper (baseline lines 35-45):
  ```tsx
    async function getToken(): Promise<string | null> {
      try {
        return await getFreshSupabaseToken();
      } catch {
        router.push("/login");
        return null;
      }
    }
  ```

  Add the import:
  ```tsx
  import { getFreshSupabaseToken } from "@/lib/auth/tokens";
  ```

  Remove the `createClient` import if no other callsite uses it in this file.

- [ ] **Step 15.3: Confirm no leftover raw getSession calls**

  ```bash
  rg -n "supabase.auth.getSession" frontend/app/app/
  ```
  Expected: zero matches inside `frontend/app/app/` (the lib/auth/tokens.ts mention stays — it's the one canonical call).

- [ ] **Step 15.4: Run type-check + tests**

  ```bash
  npm run type-check && npm run lint && npm run test
  ```
  Expected: all green.

- [ ] **Step 15.5: Commit**

  ```bash
  git add frontend/app/app/(dashboard)/profile/page.tsx frontend/app/app/onboarding/page.tsx
  git commit -m "refactor(auth): route profile/onboarding token reads through getFreshSupabaseToken"
  ```

---

## Task 16: Document SessionGuard invariant in `tokens.ts`

**Goal:** Comment-only. Leaves `getFreshSupabaseToken` fast on the hot path; documents *why* it doesn't self-validate — because `SessionGuard` does it out-of-band.

**Files:**
- Modify: `frontend/app/lib/auth/tokens.ts`

- [ ] **Step 16.1: Update the header comment**

  Modify `frontend/app/lib/auth/tokens.ts`. Replace lines 1-9 (current header) with:
  ```ts
  // Fetches the current Supabase access token, refreshing if necessary.
  // Used by every TanStack Query hook and typed API client.
  //
  // No in-memory cache: @supabase/ssr already caches the session in
  // cookies and auto-refreshes on expiry — re-calling getSession() is
  // cheap, and a second cache risks serving a stale token.
  //
  // No server round-trip: validation is out-of-band via `SessionGuard`
  // (components/dashboard/SessionGuard.tsx), which calls `getUser()` on
  // mount, on `visibilitychange` when the tab is revealed after being
  // hidden ≥5min, and on `onAuthStateChange('SIGNED_OUT')`. Invariant:
  // any token this function returns has been validated inside the
  // current SessionGuard window. Keeping the hot path on `getSession()`
  // preserves sub-millisecond token reads for every API call.
  ```

- [ ] **Step 16.2: Commit**

  ```bash
  git add frontend/app/lib/auth/tokens.ts
  git commit -m "docs(auth): document SessionGuard invariant on getFreshSupabaseToken"
  ```

---

## Phase 5 — Verification + human review

## Task 17: Final green-run gate

**Goal:** Before inviting human review, prove the whole batch is green end-to-end. No batch is merged without this gate.

- [ ] **Step 17.1: Backend full suite**

  ```bash
  cd /home/ishant/Projects/ProjectX/.worktrees/cleanup-batch-3/backend/nexus
  docker compose run --rm nexus pytest \
    --deselect tests/test_auth_service.py::TestVerifyAccessToken::test_valid_token_returns_payload \
    --deselect tests/test_auth_service.py::TestVerifyAccessToken::test_projectx_admin_token \
    --deselect tests/test_auth_service.py::TestVerifyAccessToken::test_empty_custom_claims_returns_defaults \
    --deselect tests/test_session_schemas.py::test_pre_check_response_round_trips
  ```
  Expected: 472 + new B3 tests pass, 4 pre-existing failures still deselected.

- [ ] **Step 17.2: Frontend full suite + build**

  ```bash
  cd /home/ishant/Projects/ProjectX/.worktrees/cleanup-batch-3/frontend/app
  npm run type-check && npm run lint && npm run test && npm run build
  ```
  Expected: all 4 commands exit 0.

- [ ] **Step 17.3: Grep guard — no direct Supabase signup in frontend code**

  ```bash
  rg -n "supabase.auth.(signUp|signInWithPassword)" frontend/app/ --glob '!node_modules'
  ```
  Expected: zero matches. If the login page still uses `signInWithPassword`, that's out-of-scope for B3 (login uses direct Supabase intentionally for MVP; B4 migrates the form to RHF+Zod). Only flag if the invite page still has it.

- [ ] **Step 17.4: Grep guard — no Supabase Admin HTTP in backend business logic**

  ```bash
  rg -n "/auth/v1/admin" backend/nexus/app/ --glob '!*/admin/supabase.py'
  ```
  Expected: zero matches. If `settings/service.py` still has a direct URL, Task 3 missed it.

- [ ] **Step 17.5: Stage + push**

  ```bash
  git status  # Confirm clean tree.
  git log --oneline origin/main..HEAD  # Review commit sequence.
  ```
  Expected: ~16 commits on the branch, in logical order per the tasks above. If anything looks out of order or unclean, fix before proceeding.

---

## Task 18: Browser smoke tests (manual)

**Goal:** B3 is the first batch where CLAUDE.md's "human review required" constraint binds on non-optional browser smokes. Every path below must be exercised successfully before merge.

**Pre-flight:**
- [ ] **Step 18.1: Boot the dev stack**
  ```bash
  # Terminal 1 — backend
  cd /home/ishant/Projects/ProjectX/.worktrees/cleanup-batch-3/backend/nexus
  docker compose up --build

  # Terminal 2 — backend worker (question bank generation & JD re-enrich)
  docker compose up nexus-worker

  # Terminal 3 — frontend
  cd /home/ishant/Projects/ProjectX/.worktrees/cleanup-batch-3/frontend/app
  npm run dev
  ```

- [ ] **Step 18.2: Smoke 1 — invite flow end-to-end**

  1. As the seeded ProjectX admin, provision a new client via `/api/admin/provision-client` (UI path or direct curl — use the existing flow).
  2. Check inbox (or dry-run email logs) for the invite link; it lands on `/invite?token=...`.
  3. Open the link in an incognito window. Confirm "Set up your account" renders with the correct email + client name.
  4. Enter a password + confirm password, click "Create account & continue →".
  5. Expect: redirect to `/onboarding`. No console errors. Browser cookies show a fresh Supabase session.
  6. Open DevTools → Application → Cookies; verify `sb-...-auth-token` cookie is set.
  7. Complete the onboarding wizard; land on the dashboard home.

- [ ] **Step 18.3: Smoke 2 — SessionGuard visibility-change**

  1. While logged in on `/jobs`, open DevTools → Application → Cookies and delete the `sb-...-auth-token` cookie (simulates cross-tab sign-out / server-side revocation).
  2. Hide the tab for 6+ minutes (switch tabs, leave, come back).
  3. Return to the tab. Expect: SessionGuard's `visibilitychange` handler fires, `getUser()` returns null, page redirects to `/login`.

- [ ] **Step 18.4: Smoke 3 — Global 401 handler on in-flight query**

  1. Log in; navigate to `/jobs`.
  2. Keep the tab open. In another terminal, revoke the session via Supabase SQL:
     ```sql
     DELETE FROM auth.refresh_tokens WHERE user_id = '<your-user-id>';
     UPDATE auth.users SET banned_until = NOW() + INTERVAL '1 hour' WHERE id = '<your-user-id>';
     ```
  3. In the UI, trigger a query that will fail — e.g., click on a job to navigate to `/jobs/[id]`.
  4. Expect: toast "Session expired. Please log in again.", redirect to `/login`. No component-level error overlays.

- [ ] **Step 18.5: Smoke 4 — Invite re-acceptance idempotency**

  Optional but recommended: verify the `UserAlreadyExistsError` → update-password path by running the same invite twice.
  1. Go through Smoke 1 to completion.
  2. Revoke the accepted invite manually in the DB (or generate a fresh invite for the same email via `/api/settings/team/resend`).
  3. Accept the new invite with a different password.
  4. Expect: same user, new password works on `/login`.

- [ ] **Step 18.6: Report smoke outcomes**

  If any smoke fails, STOP. Record the failure, diagnose, fix, re-run the failing smoke. Do not proceed to human review with a failed smoke.

---

## Task 19: Pre-merge human review gate

**Goal:** STOP before merging. Present the diff. Wait for user approval.

Per spec section 7.7 and CLAUDE.md "Human Review Required For", the following changes must have explicit user sign-off:
- `app/modules/auth/admin/` — new AuthProvider boundary.
- `app/modules/auth/router.py::accept_invite` — new endpoint.
- `app/modules/auth/router.py` — deletion of `complete_invite`.
- `lib/auth/tokens.ts` — comment-only, but still auth-layer surface.
- `lib/auth/handle-error.ts` — new global 401 sink.
- `components/dashboard/SessionGuard.tsx` — new client-side session validator.

- [ ] **Step 19.1: Produce the review diff**

  ```bash
  cd /home/ishant/Projects/ProjectX/.worktrees/cleanup-batch-3
  git log --oneline main..HEAD  # Commit list for reviewer.
  git diff main --stat           # Top-level file impact.
  git diff main -- backend/nexus/app/modules/auth/admin/  # Provider boundary.
  git diff main -- backend/nexus/app/modules/auth/        # Endpoint changes.
  git diff main -- frontend/app/lib/auth/                 # Token + error handling.
  git diff main -- frontend/app/components/dashboard/SessionGuard.tsx
  git diff main -- frontend/app/components/dashboard/providers.tsx
  ```

- [ ] **Step 19.2: Present to the user; wait for approval**

  Concrete message: "B3 implementation is complete on `cleanup/batch-3-auth-hardening`. All gates green, browser smokes pass. The auth-layer changes require your explicit review before merge per CLAUDE.md. Please review the diffs above and approve or request changes."

- [ ] **Step 19.3: ONLY on user approval — merge**

  ```bash
  cd /home/ishant/Projects/ProjectX
  git checkout main
  git merge --no-ff cleanup/batch-3-auth-hardening -m "Merge branch 'cleanup/batch-3-auth-hardening' — Batch 3 auth hardening"
  ```

- [ ] **Step 19.4: Post-merge — update spec status note**

  Open `docs/superpowers/specs/2026-04-24-frontend-backend-cleanup-design.md`. Find section 7 ("Batch 3 — Auth Hardening + Invite Flow"). Insert a status block immediately under the section heading, mirroring the B1/B2 pattern:

  ```markdown
  > **Status:** ✅ Completed 2026-04-25 on branch `cleanup/batch-3-auth-hardening` (worktree `.worktrees/cleanup-batch-3/`). <N> commits `<first>..<last>`, merged as `<merge-sha>` (no-ff). Final gates: backend pytest green (B3 tests + baseline), tsc clean, lint 0 errors, vitest green, `next build` clean. Browser smokes: invite flow, SessionGuard visibility revalidation, global 401 handler, invite re-acceptance idempotency — all passed. Per-task review + whole-batch human review per CLAUDE.md "Human Review Required" list.
  ```

  Commit separately:
  ```bash
  git add docs/superpowers/specs/2026-04-24-frontend-backend-cleanup-design.md
  git commit -m "docs(spec): mark Batch 3 complete"
  ```

- [ ] **Step 19.5: Post-merge — clean up worktree**

  ```bash
  cd /home/ishant/Projects/ProjectX
  git worktree remove .worktrees/cleanup-batch-3
  git branch -d cleanup/batch-3-auth-hardening
  ```

---

## Self-review checklist

**Spec coverage (section 7):**
- 7.1 B3.1 (tokens.ts validation) — Task 16 (comment-only; architectural fix is SessionGuard in Task 13).
- 7.1 B3.2 (profile.tsx, onboarding.tsx raw getSession) — Task 15.
- 7.1 B3.3 (invite flow backend rewrite) — Tasks 4–10.
- 7.1 B3.4 (global 401 handler) — Tasks 11–12.
- 7.2 SessionGuard — Tasks 13–14.
- 7.3 backend accept-invite endpoint + Admin API wrapper — Tasks 1–3, 5–8.
- 7.4 handleAuthError — Task 11.
- 7.5 Migration note (no data migration) — satisfied implicitly (early dev).
- 7.6 Acceptance criteria — Tasks 17–18.
- 7.7 Human review gate — Task 19.

**Deviation from spec:**
- `admin_client.py` split into `admin/` package (base.py + supabase.py + __init__.py factory). Documented in top-of-plan "Design deviation".
- `tokens.ts` treated as comment-only per 7.2 wording. Documented in top-of-plan.
- Response shape of `AcceptInviteResponse` is `{access_token, refresh_token, expires_in, redirect_to}` — dropped `user_id`/`tenant_id`/`root_unit_id` from legacy `complete-invite`. Frontend never used them. YAGNI.

**Placeholder scan:** none.

**Type consistency:** `UserIdentity` / `SessionTokens` / `UserAlreadyExistsError` / `UserNotFoundError` / `InvalidCredentialsError` / `AuthProviderError` named identically across `base.py`, `supabase.py`, tests, router. `AppRouter` shape defined once in `handle-error.ts`, reused by providers.tsx and the test file.

**Sequencing:** enforced — Tasks 1–3 land the boundary, Task 4 carves middleware, Tasks 5–8 deliver the new endpoint + tests + legacy deletion, Tasks 9–10 migrate the frontend caller, Tasks 11–16 add session hardening, Tasks 17–19 verify + gate.
