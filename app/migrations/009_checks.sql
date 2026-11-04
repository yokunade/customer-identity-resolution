-- Engine checks: loading the page shows customers' details, so it is logged.
ALTER TABLE lookup_access DROP CONSTRAINT lookup_access_action_check;
ALTER TABLE lookup_access ADD CONSTRAINT lookup_access_action_check
    CHECK (action IN ('search', 'view_customer', 'add_record_check', 'add_record', 'checks'));
