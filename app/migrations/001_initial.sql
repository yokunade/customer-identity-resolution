-- Customer identity resolution: initial schema.

CREATE TABLE customer (
    customer_id   uuid PRIMARY KEY,
    status        text        NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'merged')),
    merged_into   uuid        NULL REFERENCES customer (customer_id),
    created_at    timestamptz NOT NULL DEFAULT now(),
    updated_at    timestamptz NOT NULL DEFAULT now()
);

-- One row per (source system, source record). The customer link is sticky:
-- re-sending the same source record updates it but never silently re-links it.
CREATE TABLE source_record (
    record_id         bigserial   PRIMARY KEY,
    source_system     text        NOT NULL,
    source_record_id  text        NOT NULL,
    customer_id       uuid        NOT NULL REFERENCES customer (customer_id),
    raw               jsonb       NOT NULL,
    cleansed          jsonb       NOT NULL,
    match_profile     jsonb       NOT NULL,
    data_quality      jsonb       NOT NULL DEFAULT '[]'::jsonb,
    received_at       timestamptz NOT NULL DEFAULT now(),
    updated_at        timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT uq_source_record UNIQUE (source_system, source_record_id)
);
CREATE INDEX ix_source_record_customer ON source_record (customer_id);

-- Deterministic match keys. key_value is a truncated HMAC-SHA256 of the key
-- material, so no raw PII is held in this table.
CREATE TABLE match_key (
    record_id  bigint NOT NULL REFERENCES source_record (record_id) ON DELETE CASCADE,
    key_type   text   NOT NULL,
    key_value  bytea  NOT NULL,
    PRIMARY KEY (record_id, key_type, key_value)
);
CREATE INDEX ix_match_key_lookup ON match_key (key_type, key_value);

-- Blocking keys: broad keys used to assemble the candidate pool.
CREATE TABLE blocking_key (
    record_id  bigint NOT NULL REFERENCES source_record (record_id) ON DELETE CASCADE,
    key_type   text   NOT NULL,
    key_value  bytea  NOT NULL,
    PRIMARY KEY (record_id, key_type, key_value)
);
CREATE INDEX ix_blocking_key_lookup ON blocking_key (key_type, key_value);

-- Audit trail of every resolution decision.
CREATE TABLE resolution_event (
    event_id     bigserial   PRIMARY KEY,
    record_id    bigint      NULL REFERENCES source_record (record_id) ON DELETE SET NULL,
    customer_id  uuid        NULL,
    outcome      text        NOT NULL,
    rule         text        NULL,
    details      jsonb       NOT NULL DEFAULT '{}'::jsonb,
    created_at   timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX ix_resolution_event_record ON resolution_event (record_id);
CREATE INDEX ix_resolution_event_customer ON resolution_event (customer_id);

-- Pairs of customers that both matched one inbound record at the same rule.
CREATE TABLE merge_candidate (
    customer_a   uuid        NOT NULL REFERENCES customer (customer_id),
    customer_b   uuid        NOT NULL REFERENCES customer (customer_id),
    rule         text        NOT NULL,
    record_id    bigint      NULL REFERENCES source_record (record_id) ON DELETE SET NULL,
    status       text        NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'merged', 'rejected')),
    created_at   timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (customer_a, customer_b),
    CHECK (customer_a < customer_b)
);
