-- =============================================================
-- Add `metadata` JSONB to organizational_units
--
-- Holds per-unit-type rich metadata that the v4 design surfaces
-- (region offices, division description + default panel, team
-- focus, etc.). Additive + nullable — existing rows are unaffected
-- and any application that ignores the column keeps working.
--
-- Shape is validated at the application layer per unit_type; the
-- DB keeps it opaque JSONB for the same schema-evolution reason
-- as `company_profile` (see 20260410000000_phase_2a_company_profile_reset.sql).
-- =============================================================

ALTER TABLE organizational_units
    ADD COLUMN IF NOT EXISTS metadata JSONB;
