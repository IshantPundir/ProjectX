"""Test fixtures — integration tests against a real PostgreSQL database.

Uses connection-level transaction rollback so each test is fully isolated
without needing to truncate tables.
"""

import os

# Mark the environment as `test` BEFORE importing app.config / app.main so
# the candidate_jwt_secret field validator skips its required-in-non-test
# check. Production deployments must set CANDIDATE_JWT_SECRET explicitly.
os.environ.setdefault("ENVIRONMENT", "test")

# Provide a dummy engine JWT secret for tests so the _engine_secret_required
# validator (Phase 3C.2) doesn't fire when ENVIRONMENT is already set to
# "development" in the container .env. The validator only skips in ENVIRONMENT=test;
# if the container .env wins over os.environ.setdefault above, the validator
# would raise before conftest finishes loading. Setting a non-empty dummy
# value here sidesteps the check unconditionally — safe in tests because
# the secret is never used against a real interview-engine worker.
os.environ.setdefault("INTERVIEW_ENGINE_JWT_SECRET", "test-engine-secret-placeholder-32chars")

# Force DB_RUNTIME_ROLE empty in tests regardless of what .env says. The
# test database uses SQLAlchemy Base.metadata.create_all (see _create_tables
# below), not real alembic migrations, so the `nexus_app` role that
# migration 0010 creates doesn't have GRANTs on test tables. If a test
# happens to exercise the real session helpers (most tests override them
# via dependency_overrides, but not all) the helper would try
# `SET LOCAL ROLE nexus_app` and subsequent queries would fail with
# "permission denied". Empty string disables the role switch unconditionally.
os.environ["DB_RUNTIME_ROLE"] = ""

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import dramatiq
import pytest
import pytest_asyncio
import sqlalchemy
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.database import Base
from app.main import app
from app.modules.auth.models import User
from app.modules.auth.service import create_candidate_token
from app.modules.candidates.models import (
    Candidate,
    CandidateJobAssignment,
)
from app.modules.jd.models import JobPosting
from app.modules.org_units.models import (
    Client,
    OrganizationalUnit,
)
from app.modules.pipelines.models import (
    JobPipelineInstance,
    JobPipelineStage,
)
from app.modules.session.models import CandidateSessionToken, Session as SessionRow

# Default targets host.docker.internal so that `docker compose run --rm nexus pytest`
# Just Works without an env var override. The host alias is provided by the
# docker-compose.yml `extra_hosts` block. Tests run outside Docker can override
# via TEST_DATABASE_URL=postgresql+asyncpg://postgres:postgres@127.0.0.1:54322/projectx_test
TEST_DATABASE_URL = os.environ.get(
    "TEST_DATABASE_URL",
    "postgresql+asyncpg://postgres:postgres@host.docker.internal:54322/projectx_test",
)

test_engine = create_async_engine(TEST_DATABASE_URL, echo=False)


@pytest.fixture(scope="session")
def event_loop_policy():
    """Use default event loop policy for the whole test session."""
    import asyncio

    return asyncio.DefaultEventLoopPolicy()


@pytest_asyncio.fixture(scope="session", loop_scope="session")
async def _create_tables():
    """Create all tables once at test session start."""
    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield
    # Drop all tables via raw SQL with CASCADE to handle circular FK between
    # clients.super_admin_id → users and users.tenant_id → clients.
    async with test_engine.begin() as conn:
        table_names = ", ".join(Base.metadata.tables.keys())
        await conn.execute(sqlalchemy.text(f"DROP TABLE IF EXISTS {table_names} CASCADE"))
    await test_engine.dispose()


@pytest_asyncio.fixture(loop_scope="session")
async def db(_create_tables: None):
    """Per-test database session with automatic rollback.

    The session is bound to a connection-level transaction.
    Everything the test does — including flushes and internal commits
    by service functions — is rolled back after the test.
    """
    async with test_engine.connect() as conn:
        txn = await conn.begin()
        session = AsyncSession(bind=conn, expire_on_commit=False)
        try:
            yield session
        finally:
            await session.close()
            await txn.rollback()


@pytest.fixture(autouse=True)
def _no_real_broker_enqueue(monkeypatch):
    """Stop any actor ``.send()`` in tests from enqueuing to the real Redis broker.

    The test suite has no StubBroker, so ``.send()`` would otherwise hit the
    shared dev Redis. Tests that assert enqueue patch the actor's ``.send``
    directly (that still works — it shadows this no-op); this is the backstop so
    un-patched sends (e.g. ``record_session_evidence``'s report-scoring enqueue)
    don't leave orphan messages that a worker later drains.
    """
    monkeypatch.setattr(
        dramatiq.get_broker(), "enqueue", lambda message, *, delay=None: message
    )


@pytest.fixture
async def client() -> AsyncClient:
    """Async HTTP test client for FastAPI (kept for existing tests)."""
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as ac:
        yield ac


# ---------------------------------------------------------------------------
# Factory helpers — sensible defaults, tests override only what matters
# ---------------------------------------------------------------------------

_counter = 0


def _next_id() -> int:
    global _counter
    _counter += 1
    return _counter


async def create_test_client(db: AsyncSession, **kwargs) -> Client:
    """Create a Client row with sensible defaults."""
    n = _next_id()
    defaults = {
        "name": f"Test Company {n}",
        "domain": f"test{n}.com",
        "industry": "Technology",
        "plan": "trial",
        "onboarding_complete": False,
    }
    defaults.update(kwargs)
    client = Client(**defaults)
    db.add(client)
    await db.flush()
    return client


async def create_test_user(db: AsyncSession, client_id: uuid.UUID, **kwargs) -> User:
    """Create a User row with sensible defaults."""
    n = _next_id()
    now = datetime.now(UTC)
    defaults = {
        "auth_user_id": uuid.uuid4(),
        "tenant_id": client_id,
        "email": f"user{n}@test.com",
        "full_name": f"Test User {n}",
        "is_active": True,
        "created_at": now,
        "updated_at": now,
    }
    defaults.update(kwargs)
    user = User(**defaults)
    db.add(user)
    await db.flush()
    return user


async def create_test_ats_user(
    db: AsyncSession, client_id: uuid.UUID, **kwargs,
) -> User:
    """Create an ATS-imported User row (auth_user_id=None, is_active=False).

    Mirrors the orchestrator's `_insert_ats_user` shape but lets tests
    pre-seed rows that the email-collision matrix should resolve against.

    Defaults:
      - source='ats_ceipal'
      - external_id is required (`source LIKE 'ats_%'` CHECK constraint);
        a random UUID-hex is used if not provided.
      - auth_user_id=None; is_active=False
    """
    n = _next_id()
    now = datetime.now(UTC)
    defaults = {
        "auth_user_id": None,
        "tenant_id": client_id,
        "email": f"ats-user{n}@test.com",
        "full_name": f"ATS User {n}",
        "is_active": False,
        "source": "ats_ceipal",
        "external_id": uuid.uuid4().hex,
        "external_source_metadata": None,
        "created_at": now,
        "updated_at": now,
    }
    defaults.update(kwargs)
    user = User(**defaults)
    db.add(user)
    await db.flush()
    return user


async def create_test_org_unit(
    db: AsyncSession, client_id: uuid.UUID, **kwargs
) -> OrganizationalUnit:
    """Create an OrganizationalUnit row with sensible defaults."""
    n = _next_id()
    now = datetime.now(UTC)
    defaults = {
        "client_id": client_id,
        "name": f"Test Unit {n}",
        "unit_type": "division",
        "created_at": now,
        "updated_at": now,
    }
    defaults.update(kwargs)
    unit = OrganizationalUnit(**defaults)
    db.add(unit)
    await db.flush()
    return unit


# ---------------------------------------------------------------------------
# pub/sub capture fixture — shared across all event-emission tests
# ---------------------------------------------------------------------------

@dataclass
class CapturedPublish:
    channel: str
    event: str
    payload: dict
    correlation_id: str


@pytest.fixture
def capture_publishes(monkeypatch) -> list[CapturedPublish]:
    """Replace pubsub.publish with a capturing stub.

    Returns a list that accumulates every publish call made during the test.
    The stub is installed BEFORE the request so that BackgroundTasks.add_task
    captures the stubbed reference at enqueue time. FastAPI runs background
    tasks after the response is sent, so by the time `await client.post(...)`
    returns the list is already populated.
    """
    from app import pubsub

    captured: list[CapturedPublish] = []

    async def stub_publish(channel, event, payload, *, correlation_id):
        captured.append(CapturedPublish(
            channel=channel,
            event=event,
            payload=payload,
            correlation_id=correlation_id,
        ))

    monkeypatch.setattr(pubsub, "publish", stub_publish)
    return captured


# ---------------------------------------------------------------------------
# Graph-builder helper — reusable across migration + interview-runtime tests
# ---------------------------------------------------------------------------


async def make_assignment_with_stage(
    db: AsyncSession,
    tenant: Client,
    user: User,
    *,
    otp_default: bool = False,
    stage_type: str = "ai_screening",
) -> tuple[CandidateJobAssignment, JobPipelineStage]:
    """Build the minimum graph to attach a session to.

    org_unit -> job_posting -> pipeline instance -> stage -> candidate -> assignment.
    Returns (assignment, stage). ``stage_type`` defaults to ``ai_screening`` (v5);
    pass ``"human_interview"`` etc. for negative-path tests.
    """
    org_unit = await create_test_org_unit(db, tenant.id)
    await db.flush()

    job = JobPosting(
        tenant_id=tenant.id,
        org_unit_id=org_unit.id,
        title="Senior Engineer",
        description_raw="R" * 60,
        created_by=user.id,
        status="draft",
    )
    db.add(job)
    await db.flush()

    instance = JobPipelineInstance(
        tenant_id=tenant.id,
        job_posting_id=job.id,
    )
    db.add(instance)
    await db.flush()

    stage_kwargs = dict(
        tenant_id=tenant.id,
        instance_id=instance.id,
        position=0,
        name="Phone Screen",
        stage_type=stage_type,
        duration_minutes=30,
        difficulty="medium",
        signal_filter={},
        pass_criteria={},
        advance_behavior="manual",
    )
    if otp_default:
        stage_kwargs["otp_required_default"] = True
    stage = JobPipelineStage(**stage_kwargs)
    db.add(stage)
    await db.flush()

    candidate = Candidate(
        tenant_id=tenant.id,
        name="Charlie",
        email=f"charlie-{uuid.uuid4()}@example.com",
        source="manual",
        created_by=user.id,
    )
    db.add(candidate)
    await db.flush()

    assignment = CandidateJobAssignment(
        tenant_id=tenant.id,
        candidate_id=candidate.id,
        job_posting_id=job.id,
        current_stage_id=stage.id,
        assigned_by=user.id,
    )
    db.add(assignment)
    await db.flush()

    return assignment, stage


# ---------------------------------------------------------------------------
# Minimal session seed — used by tests/session/test_transition_to_error.py
# ---------------------------------------------------------------------------


async def seed_minimal_session(
    db: AsyncSession,
    *,
    state: str = "active",
) -> tuple[SessionRow, uuid.UUID]:
    """Insert a sessions row (+ minimal FK chain) and return (session, tenant_id).

    Composes the existing helpers to avoid duplicating the graph-builder logic.
    The session state is set directly on the ORM row so tests can start from
    any state without running the real state-machine service functions.
    """
    tenant = await create_test_client(db)
    user = await create_test_user(db, tenant.id)
    assignment, stage = await make_assignment_with_stage(db, tenant, user)

    session = SessionRow(
        tenant_id=tenant.id,
        assignment_id=assignment.id,
        stage_id=stage.id,
        state=state,
        created_by=user.id,
    )
    db.add(session)
    await db.flush()

    return session, tenant.id


async def mint_candidate_session_token(
    db: AsyncSession,
    *,
    session_id: uuid.UUID,
    tenant_id: uuid.UUID,
    superseded: bool = False,
) -> str:
    """Insert a CandidateSessionToken row and return the signed JWT string.

    Shared by any test that needs a valid candidate JWT for an HTTP endpoint
    protected by the candidate-token middleware. The row is flushed (not
    committed) so it participates in the test's rollback-isolated transaction.

    The `superseded` flag lets callers simulate a revoked/resent token — the
    middleware will reject it with 401 TOKEN_SUPERSEDED.
    """
    jti = uuid.uuid4()
    expires = datetime.now(UTC) + timedelta(days=7)
    token_row = CandidateSessionToken(
        jti=jti,
        tenant_id=tenant_id,
        session_id=session_id,
        expires_at=expires,
        superseded_at=datetime.now(UTC) if superseded else None,
    )
    db.add(token_row)
    await db.flush()

    # candidate_id is not enforced as FK on this table — any UUID works.
    token_str, _exp = create_candidate_token(
        jti=jti,
        candidate_id=uuid.uuid4(),
        session_id=session_id,
        tenant_id=tenant_id,
    )
    return token_str
