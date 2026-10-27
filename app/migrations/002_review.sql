-- Review workflow: reviewers, sessions, and case handling on merge_candidate.

CREATE TABLE review_user (
    user_id        bigserial   PRIMARY KEY,
    email          text        NOT NULL UNIQUE CHECK (email = lower(email)),
    display_name   text        NOT NULL,
    password_hash  text        NOT NULL,
    role           text        NOT NULL DEFAULT 'reviewer' CHECK (role IN ('reviewer', 'admin')),
    active         boolean     NOT NULL DEFAULT true,
    created_at     timestamptz NOT NULL DEFAULT now()
);

-- Only a SHA-256 of each session token is stored.
CREATE TABLE review_session (
    token_hash   bytea       PRIMARY KEY,
    user_id      bigint      NOT NULL REFERENCES review_user (user_id) ON DELETE CASCADE,
    created_at   timestamptz NOT NULL DEFAULT now(),
    expires_at   timestamptz NOT NULL
);
CREATE INDEX ix_review_session_user ON review_session (user_id);

-- Each merge candidate becomes a reviewable case.
ALTER TABLE merge_candidate DROP CONSTRAINT merge_candidate_pkey;
ALTER TABLE merge_candidate ADD COLUMN case_id bigserial PRIMARY KEY;
ALTER TABLE merge_candidate ADD CONSTRAINT uq_merge_candidate_pair UNIQUE (customer_a, customer_b);

ALTER TABLE merge_candidate DROP CONSTRAINT merge_candidate_status_check;
ALTER TABLE merge_candidate ADD CONSTRAINT merge_candidate_status_check
    CHECK (status IN ('open', 'merged', 'rejected', 'obsolete'));

ALTER TABLE merge_candidate
    ADD COLUMN claimed_by            bigint      NULL REFERENCES review_user (user_id),
    ADD COLUMN claimed_at            timestamptz NULL,
    ADD COLUMN decided_by            bigint      NULL REFERENCES review_user (user_id),
    ADD COLUMN decided_at            timestamptz NULL,
    ADD COLUMN decision              text        NULL CHECK (decision IN ('merge', 'keep_apart')),
    ADD COLUMN notes                 text        NULL,
    ADD COLUMN survivor_customer_id  uuid        NULL REFERENCES customer (customer_id);

CREATE INDEX ix_merge_candidate_queue ON merge_candidate (status, created_at) WHERE status = 'open';
CREATE INDEX ix_merge_candidate_decided ON merge_candidate (decided_by, decided_at);
