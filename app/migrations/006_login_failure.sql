-- Failed review-app sign-ins, so throttling holds across every API worker.
-- Only a SHA-256 of the lower-cased email is kept; rows older than the
-- throttle window are deleted as new failures arrive.

CREATE TABLE login_failure (
    key_hash   bytea       NOT NULL,
    failed_at  timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX ix_login_failure_key ON login_failure (key_hash, failed_at);
CREATE INDEX ix_login_failure_age ON login_failure (failed_at);
