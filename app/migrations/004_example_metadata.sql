-- Free-form labels on a training example, e.g. the demo scenario it came from.
ALTER TABLE review_example ADD COLUMN metadata jsonb NOT NULL DEFAULT '{}'::jsonb;
