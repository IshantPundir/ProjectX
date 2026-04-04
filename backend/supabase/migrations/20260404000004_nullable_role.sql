-- Allow users and invites to have no role assigned.
-- Role is assigned later when the user is placed into an org unit.
-- "Unassigned" means the user has an account but no role yet.

-- users: make role nullable, add 'Unassigned' to CHECK
ALTER TABLE public.users ALTER COLUMN role DROP NOT NULL;
ALTER TABLE public.users DROP CONSTRAINT users_role_check;
ALTER TABLE public.users ADD CONSTRAINT users_role_check
  CHECK (role IS NULL OR role IN (
    'Company Admin', 'Admin', 'Recruiter',
    'Hiring Manager', 'Interviewer', 'Observer'
  ));

-- user_invites: make role nullable, add 'Unassigned' to CHECK
ALTER TABLE public.user_invites ALTER COLUMN role DROP NOT NULL;
ALTER TABLE public.user_invites DROP CONSTRAINT user_invites_role_check;
ALTER TABLE public.user_invites ADD CONSTRAINT user_invites_role_check
  CHECK (role IS NULL OR role IN (
    'Company Admin', 'Admin', 'Recruiter',
    'Hiring Manager', 'Interviewer', 'Observer'
  ));
