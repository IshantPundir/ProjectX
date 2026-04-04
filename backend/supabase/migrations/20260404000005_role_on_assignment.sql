-- Move role from users table to user_org_assignments.
-- A user can have different roles in different org units.
-- users.role stays for backward compat (Super Admin's global role)
-- but is no longer the primary role source — assignments are.

-- Add role column to user_org_assignments
ALTER TABLE public.user_org_assignments
  ADD COLUMN role TEXT
  CHECK (role IS NULL OR role IN (
    'Company Admin', 'Admin', 'Recruiter',
    'Hiring Manager', 'Interviewer', 'Observer'
  ));

-- Add permissions column to user_org_assignments
ALTER TABLE public.user_org_assignments
  ADD COLUMN permissions JSONB NOT NULL DEFAULT '[]'::jsonb;

-- Add is_admin column to user_org_assignments
ALTER TABLE public.user_org_assignments
  ADD COLUMN is_admin BOOLEAN NOT NULL DEFAULT FALSE;
