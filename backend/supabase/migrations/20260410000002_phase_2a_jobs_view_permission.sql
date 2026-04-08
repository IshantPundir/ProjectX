-- =============================================================
-- Phase 2A — Seed jobs.view into Admin, Recruiter, Hiring Manager
-- Mirrors the ALL_PERMISSIONS frozenset updated in Task 8.
-- Idempotent: re-running this migration is a no-op if the permission
-- is already in the role's permissions array.
-- =============================================================

UPDATE roles
   SET permissions = permissions || '["jobs.view"]'::jsonb
 WHERE is_system = TRUE
   AND name IN ('Admin', 'Recruiter', 'Hiring Manager')
   AND NOT (permissions ? 'jobs.view');
