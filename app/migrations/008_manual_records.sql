-- Source records a manager adds by hand in custIDR. They belong to the reserved
-- source system MANUAL, are numbered from a sequence (MANUAL/M-000001), and keep
-- who added them, when and why. submit_token makes a double-clicked save add the
-- record once.

CREATE SEQUENCE manual_record_seq;

CREATE TABLE manual_record (
    record_id     bigint      PRIMARY KEY REFERENCES source_record (record_id) ON DELETE CASCADE,
    added_by      bigint      NOT NULL REFERENCES review_user (user_id),
    added_at      timestamptz NOT NULL DEFAULT now(),
    note          text        NULL,
    submit_token  uuid        NOT NULL UNIQUE
);
CREATE INDEX ix_manual_record_user ON manual_record (added_by, added_at);

ALTER TABLE lookup_access DROP CONSTRAINT lookup_access_action_check;
ALTER TABLE lookup_access ADD CONSTRAINT lookup_access_action_check
    CHECK (action IN ('search', 'view_customer', 'add_record_check', 'add_record'));
