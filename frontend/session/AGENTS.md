# This is NOT the Next.js you know

This version has breaking changes — APIs, conventions, and file structure may all differ from your training data. Read the relevant guide in `node_modules/next/dist/docs/` before writing any code. Heed deprecation notices.

# One purpose

This app exists for one user (the candidate) and one task (joining a single live interview). Do not add recruiter features, admin features, account management, or anything that requires Supabase auth. If a feature touches anything other than the candidate's own session, it belongs in `frontend/app/` or `frontend/admin/`, not here.

# Token discipline

The candidate JWT is in the URL path. Never log it, never store it, never send it to any third party. When Sentry is wired (future PR), its `beforeSend` MUST scrub `/interview/[^/]+` paths.
