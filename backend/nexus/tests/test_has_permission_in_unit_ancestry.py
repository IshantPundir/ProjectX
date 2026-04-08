"""Day-1 verification: does has_permission_in_unit() inherit permissions
from ancestor units, or only check the exact unit ID?

The answer dictates whether require_job_access() in app/modules/jd/authz.py
needs to walk ancestry itself (primary) or can rely on the helper
(belt-and-braces). See spec Task 1."""

import uuid

import pytest

from app.modules.auth.context import RoleAssignment, UserContext
from tests.conftest import (
    create_test_client,
    create_test_org_unit,
    create_test_user,
)


@pytest.mark.asyncio
async def test_ancestry_inheritance_behavior(db):
    """Create a parent → child org unit hierarchy, grant a recruiter a role
    on the PARENT, and check whether has_permission_in_unit(child, ...)
    returns True (ancestry inheritance) or False (exact-match only)."""

    tenant = await create_test_client(db)
    await db.flush()

    user = await create_test_user(db, tenant.id)

    parent_unit = await create_test_org_unit(
        db, tenant.id, name="Parent Division", unit_type="division"
    )
    child_unit = await create_test_org_unit(
        db,
        tenant.id,
        name="Child Team",
        unit_type="team",
        parent_unit_id=parent_unit.id,
    )
    await db.flush()

    ctx = UserContext(
        user=user,
        is_super_admin=False,
        assignments=[
            RoleAssignment(
                org_unit_id=parent_unit.id,
                org_unit_name=parent_unit.name,
                role_id=uuid.uuid4(),
                role_name="Recruiter",
                permissions=["jobs.view"],
            ),
        ],
    )

    # Grant is on parent_unit only
    assert ctx.has_permission_in_unit(parent_unit.id, "jobs.view") is True

    # Does it inherit to child_unit?
    inherits = ctx.has_permission_in_unit(child_unit.id, "jobs.view")

    # This test is a probe, not an assertion. It always passes — we read
    # its print output to learn the answer.
    print("\n\n=== DAY-1 TASK 1 RESULT ===")
    print(f"has_permission_in_unit(child, 'jobs.view') = {inherits}")
    print(f"Parent grant inherits to child: {inherits}")
    print("==========================\n")
