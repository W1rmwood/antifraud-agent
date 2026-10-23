-- Хранилище номеров и кейсов звонков (контракт №4).
-- Даты — ISO 8601 строкой, списки — JSON-массивом строкой.

CREATE TABLE IF NOT EXISTS numbers (
    phone       TEXT PRIMARY KEY,           -- +79991234567
    calls_count INTEGER NOT NULL DEFAULT 0,
    first_seen  TEXT,                       -- started_at первого звонка
    last_seen   TEXT,                       -- started_at последнего звонка
    tags        TEXT NOT NULL DEFAULT '[]'  -- JSON: ["fraud", "bank"]
);

CREATE TABLE IF NOT EXISTS calls (
    call_id         TEXT PRIMARY KEY,
    phone           TEXT REFERENCES numbers(phone),
    started_at      TEXT NOT NULL,
    ended_at        TEXT,
    scheme_id       TEXT,
    max_stage       INTEGER,
    legend          TEXT,
    key_phrases     TEXT NOT NULL DEFAULT '[]',
    summary         TEXT NOT NULL DEFAULT '',
    transcript_path TEXT NOT NULL DEFAULT ''
);

CREATE INDEX IF NOT EXISTS idx_calls_phone ON calls(phone);
CREATE INDEX IF NOT EXISTS idx_calls_started ON calls(started_at);
