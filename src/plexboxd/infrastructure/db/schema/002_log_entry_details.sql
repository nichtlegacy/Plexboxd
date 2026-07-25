-- Letterboxd migrated diary writes to POST /api/v0/production-log-entries, which
-- identifies a film by its base-62 LID rather than the numeric film id. Cache the LID
-- so repeat ratings do not need an extra lookup.
ALTER TABLE film_match_cache ADD COLUMN letterboxd_lid TEXT;

-- The Discord modal already collects tags and a review; persist them on the job so the
-- worker can forward both to the API instead of discarding them.
ALTER TABLE rating_jobs ADD COLUMN requested_tags TEXT NOT NULL DEFAULT '[]';
ALTER TABLE rating_jobs ADD COLUMN requested_review TEXT NOT NULL DEFAULT '';
