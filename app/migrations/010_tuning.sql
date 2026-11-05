-- Demo tuning: the true person behind each demo record, and every re-run of
-- the demo data with the engine settings it used and how accurate it was.

CREATE TABLE demo_truth (
    source_system     text NOT NULL,
    source_record_id  text NOT NULL,
    person            text NOT NULL,
    PRIMARY KEY (source_system, source_record_id)
);

CREATE TABLE tuning_run (
    run_id       bigserial   PRIMARY KEY,
    started_at   timestamptz NOT NULL DEFAULT now(),
    finished_at  timestamptz NULL,
    started_by   bigint      NULL REFERENCES review_user (user_id) ON DELETE SET NULL,
    status       text        NOT NULL DEFAULT 'running' CHECK (status IN ('running', 'done', 'failed')),
    note         text        NULL,
    config       jsonb       NOT NULL,
    progress     jsonb       NOT NULL DEFAULT '{}',
    scores       jsonb       NULL,
    -- person -> {state, review, record}: what the next run is compared with.
    people       jsonb       NULL,
    error        text        NULL
);
-- One run at a time.
CREATE UNIQUE INDEX uq_tuning_run_running ON tuning_run ((true)) WHERE status = 'running';
