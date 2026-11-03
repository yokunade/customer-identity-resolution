-- Customer lookup: a manager role, and a log of every lookup.

ALTER TABLE review_user DROP CONSTRAINT review_user_role_check;
ALTER TABLE review_user ADD CONSTRAINT review_user_role_check CHECK (role IN ('reviewer', 'manager', 'admin'));

-- Who looked up what, when and (optionally) why. search_value is the cleansed
-- value searched for, so the log holds personal data: protect and retain it
-- like the rest of the database.
CREATE TABLE lookup_access (
    access_id     bigserial   PRIMARY KEY,
    user_id       bigint      NOT NULL REFERENCES review_user (user_id),
    accessed_at   timestamptz NOT NULL DEFAULT now(),
    action        text        NOT NULL CHECK (action IN ('search', 'view_customer')),
    search_type   text        NULL,
    search_value  text        NULL,
    customer_ids  uuid[]      NOT NULL DEFAULT '{}',
    reason        text        NULL
);
CREATE INDEX ix_lookup_access_user ON lookup_access (user_id, accessed_at);
CREATE INDEX ix_lookup_access_customer ON lookup_access USING gin (customer_ids);
