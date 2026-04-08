-- =============================================================
-- Phase 2A — Company Profile hard cutover
-- Adds tracking columns and nulls any existing company_profile
-- that doesn't match the new 4-field shape (about, industry,
-- company_stage, hiring_bar).
-- =============================================================

ALTER TABLE organizational_units
    ADD COLUMN company_profile_completed_at TIMESTAMPTZ,
    ADD COLUMN company_profile_completed_by UUID REFERENCES users(id);

-- Hard cutover: null any profile that doesn't carry all four new fields.
-- Pre-MVP dev data only. App-layer validation enforces character limits
-- and enum values (see app/modules/org_units/company_profile.py in Task 21).
UPDATE organizational_units
   SET company_profile = NULL
 WHERE company_profile IS NOT NULL
   AND NOT (
        company_profile ? 'about'
    AND company_profile ? 'industry'
    AND company_profile ? 'company_stage'
    AND company_profile ? 'hiring_bar'
   );

-- We deliberately do NOT add a CHECK constraint on the JSONB structure.
-- Constraints on JSONB are too brittle for future schema evolution.
