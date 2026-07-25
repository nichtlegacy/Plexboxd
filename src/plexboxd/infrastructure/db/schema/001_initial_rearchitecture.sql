PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS schema_migrations (
    version TEXT PRIMARY KEY,
    applied_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS watch_events (
    id TEXT PRIMARY KEY,
    plex_rating_key TEXT NOT NULL,
    plex_guid_hash TEXT,
    tmdb_id TEXT,
    title TEXT NOT NULL,
    original_title TEXT,
    year INTEGER,
    watched_at TEXT NOT NULL,
    view_count_at_watch INTEGER,
    library_name TEXT,
    raw_payload_json TEXT NOT NULL DEFAULT '{}',
    detected_at TEXT NOT NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_watch_events_identity
ON watch_events (plex_rating_key, watched_at);

CREATE INDEX IF NOT EXISTS idx_watch_events_tmdb_id
ON watch_events (tmdb_id);

CREATE TABLE IF NOT EXISTS notifications (
    id TEXT PRIMARY KEY,
    watch_event_id TEXT NOT NULL,
    discord_channel_id TEXT NOT NULL,
    discord_message_id TEXT NOT NULL,
    discord_view_state TEXT NOT NULL,
    sent_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY (watch_event_id) REFERENCES watch_events(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_notifications_watch_event_id
ON notifications (watch_event_id);

CREATE TABLE IF NOT EXISTS rating_jobs (
    id TEXT PRIMARY KEY,
    watch_event_id TEXT NOT NULL,
    notification_id TEXT,
    status TEXT NOT NULL,
    requested_rating REAL NOT NULL,
    requested_liked INTEGER NOT NULL,
    requested_rewatch INTEGER NOT NULL,
    requested_by_discord_user_id TEXT,
    job_locked_at TEXT,
    job_lock_owner TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY (watch_event_id) REFERENCES watch_events(id) ON DELETE CASCADE,
    FOREIGN KEY (notification_id) REFERENCES notifications(id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_rating_jobs_watch_event_id
ON rating_jobs (watch_event_id);

CREATE INDEX IF NOT EXISTS idx_rating_jobs_status
ON rating_jobs (status, created_at);

CREATE TABLE IF NOT EXISTS rating_attempts (
    id TEXT PRIMARY KEY,
    rating_job_id TEXT NOT NULL,
    attempt_no INTEGER NOT NULL,
    match_strategy TEXT,
    write_strategy TEXT,
    status TEXT NOT NULL,
    error_type TEXT,
    error_message TEXT,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    debug_payload_json TEXT NOT NULL DEFAULT '{}',
    FOREIGN KEY (rating_job_id) REFERENCES rating_jobs(id) ON DELETE CASCADE
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_rating_attempts_job_attempt_no
ON rating_attempts (rating_job_id, attempt_no);

CREATE TABLE IF NOT EXISTS rating_results (
    id TEXT PRIMARY KEY,
    watch_event_id TEXT NOT NULL,
    rating_job_id TEXT NOT NULL,
    rating_attempt_id TEXT NOT NULL,
    letterboxd_film_id TEXT NOT NULL,
    letterboxd_entry_id TEXT,
    rating_value REAL NOT NULL,
    liked INTEGER NOT NULL,
    rewatch INTEGER NOT NULL,
    watched_on TEXT NOT NULL,
    succeeded_at TEXT NOT NULL,
    FOREIGN KEY (watch_event_id) REFERENCES watch_events(id) ON DELETE CASCADE,
    FOREIGN KEY (rating_job_id) REFERENCES rating_jobs(id) ON DELETE CASCADE,
    FOREIGN KEY (rating_attempt_id) REFERENCES rating_attempts(id) ON DELETE CASCADE
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_rating_results_watch_event_id
ON rating_results (watch_event_id);

CREATE TABLE IF NOT EXISTS film_match_cache (
    id TEXT PRIMARY KEY,
    tmdb_id TEXT,
    title TEXT NOT NULL,
    original_title TEXT,
    year INTEGER,
    letterboxd_film_id TEXT NOT NULL,
    letterboxd_slug TEXT NOT NULL,
    match_source TEXT NOT NULL,
    confidence REAL NOT NULL,
    last_verified_at TEXT,
    metadata_json TEXT NOT NULL DEFAULT '{}'
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_film_match_cache_tmdb_id
ON film_match_cache (tmdb_id)
WHERE tmdb_id IS NOT NULL;

CREATE TABLE IF NOT EXISTS match_candidates (
    id TEXT PRIMARY KEY,
    rating_attempt_id TEXT NOT NULL,
    candidate_rank INTEGER NOT NULL,
    letterboxd_film_id TEXT NOT NULL,
    letterboxd_slug TEXT NOT NULL,
    candidate_title TEXT NOT NULL,
    candidate_year INTEGER,
    score REAL NOT NULL,
    decision_reason TEXT NOT NULL,
    candidate_payload_json TEXT NOT NULL DEFAULT '{}',
    FOREIGN KEY (rating_attempt_id) REFERENCES rating_attempts(id) ON DELETE CASCADE
);
