-- Rollback for 0016_stage_type_v5_and_participants.
-- LOSSY: deleted offer rows are not restored; ai_screening -> ai_interview
-- only works if no rows carry the new value.

BEGIN;

-- 1. Drop v5 CHECK constraints first so the rename below doesn't violate them.
ALTER TABLE pipeline_template_stages DROP CONSTRAINT IF EXISTS ck_template_stages_stage_type;
ALTER TABLE job_pipeline_stages DROP CONSTRAINT IF EXISTS ck_job_pipeline_stages_stage_type;

-- 2. If any rows carry ai_screening, rename them back before the CHECK swap.
UPDATE pipeline_template_stages SET stage_type = 'ai_interview'
  WHERE stage_type = 'ai_screening';
UPDATE job_pipeline_stages SET stage_type = 'ai_interview'
  WHERE stage_type = 'ai_screening';

-- 3. Swap CHECK constraints back to v4 allowlist.
ALTER TABLE pipeline_template_stages
  ADD CONSTRAINT ck_template_stages_stage_type CHECK (stage_type IN (
    'phone_screen','ai_interview','human_interview','panel_interview',
    'take_home','intake','recruiter','debrief','offer'
  ));

ALTER TABLE job_pipeline_stages
  ADD CONSTRAINT ck_job_pipeline_stages_stage_type CHECK (stage_type IN (
    'phone_screen','ai_interview','human_interview','panel_interview',
    'take_home','intake','recruiter','debrief','offer'
  ));

-- 4. Drop the participants table.
DROP TABLE IF EXISTS pipeline_stage_participants;

COMMIT;
