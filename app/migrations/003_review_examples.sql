-- Labelled examples for a future AI reviewer.
--
-- One row per labelled case. `snapshot` is the case exactly as it was
-- presented (both customers, every source record, the comparison), captured
-- before a merge changes the tables, so the example stays reproducible.

CREATE TABLE review_example (
    example_id         bigserial   PRIMARY KEY,
    case_id            bigint      NULL REFERENCES merge_candidate (case_id) ON DELETE SET NULL,
    label_source       text        NOT NULL CHECK (label_source IN ('reviewer', 'synthetic_truth')),
    reviewer_id        bigint      NULL REFERENCES review_user (user_id),
    schema_version     integer     NOT NULL,
    snapshot           jsonb       NOT NULL,
    decision           text        NOT NULL CHECK (decision IN ('merge', 'keep_apart')),
    survivor           text        NULL CHECK (survivor IN ('A', 'B')),
    reason_codes       text[]      NOT NULL DEFAULT '{}',
    confidence         text        NULL CHECK (confidence IN ('certain', 'likely', 'unsure')),
    notes              text        NULL,
    seconds_to_decide  integer     NULL,
    split              text        NOT NULL CHECK (split IN ('train', 'dev', 'test')),
    created_at         timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT uq_review_example_case_source UNIQUE (case_id, label_source)
);
CREATE INDEX ix_review_example_split ON review_example (label_source, split);

ALTER TABLE merge_candidate
    ADD COLUMN reason_codes text[] NULL,
    ADD COLUMN confidence   text   NULL CHECK (confidence IN ('certain', 'likely', 'unsure'));
