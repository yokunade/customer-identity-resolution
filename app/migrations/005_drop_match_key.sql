-- Match keys are no longer stored. Shared keys are worked out in memory for the
-- few candidates blocking finds, from each record's match-key components.
-- match_key was over half the database and ~20 index writes per record.
--
-- Rows written before this migration have no components; they are derived
-- from the cleansed record when the row is next a candidate, so no back-fill
-- is needed.

ALTER TABLE source_record ADD COLUMN match_components jsonb NULL;
DROP TABLE match_key;
