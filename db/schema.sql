-- Care Navigator schema (Neon Postgres + PostGIS). Contract: see docs/interfaces.md and docs/HANDOFF.md section 6.
-- Notes:
--   * No member_id column anywhere, on purpose.
--   * plans.id and locations.id are stable text keys (e.g. 'chg_medi_cal', 'fhcsd_city_heights')
--     so a re-crawl updates the same row.
--   * Crawls never write to main directly: they load a Neon branch, validate there, then
--     crawler/swap.py copies locations into main in one transaction. Never promote the branch.

CREATE EXTENSION IF NOT EXISTS postgis;
CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE TABLE IF NOT EXISTS plans (
    id                text PRIMARY KEY,           -- e.g. 'chg_medi_cal', 'kaiser_cc_silver'
    insurer           text NOT NULL,
    plan_name         text NOT NULL,
    coverage_type     text NOT NULL,
    urgent_care_copay numeric,
    er_copay          numeric,
    pcp_copay         numeric,
    telehealth        text CHECK (telehealth IN ('free', 'copay', 'none', 'unknown')),
    telehealth_copay  numeric,
    nurse_line        text,
    deductible        numeric,
    source_url        text,
    extracted_at      timestamptz
);

CREATE TABLE IF NOT EXISTS users (
    id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    phone             text UNIQUE NOT NULL,
    active_profile_id uuid,
    language          text NOT NULL DEFAULT 'en',
    opted_out         boolean NOT NULL DEFAULT false,   -- STOP
    created_at        timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS profiles (
    id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id          uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    label            text NOT NULL,                     -- 'kaiser' | 'medi_cal' | 'uninsured' | ...
    coverage_type    text NOT NULL,                     -- commercial | kaiser | medi_cal | covered_ca | medicare | uninsured
    insurer          text,
    plan_id          text REFERENCES plans(id),
    household_size   int,
    income_range_fpl text CHECK (income_range_fpl IN ('<138', '138-200', '200-400', '>400')),
    language         text NOT NULL DEFAULT 'en',
    UNIQUE (user_id, label)
);

ALTER TABLE users DROP CONSTRAINT IF EXISTS users_active_profile_fk;
ALTER TABLE users ADD CONSTRAINT users_active_profile_fk
    FOREIGN KEY (active_profile_id) REFERENCES profiles(id) ON DELETE SET NULL;

CREATE TABLE IF NOT EXISTS locations (
    id             text PRIMARY KEY,                     -- stable key from the source
    name           text NOT NULL,
    kind           text NOT NULL CHECK (kind IN ('urgent_care', 'fqhc', 'kaiser', 'er', 'lab', 'telehealth')),
    geom           geography(Point, 4326) NOT NULL,
    address        text NOT NULL,
    phone          text,
    hours          jsonb NOT NULL DEFAULT '{}'::jsonb,   -- {"mon": [["08:00","20:00"]], ...}; missing day = closed; split overnight spans
    has_xray       boolean,
    walk_in        boolean,
    accepts        jsonb NOT NULL DEFAULT '[]'::jsonb,   -- ["chg_medi_cal", "uninsured", "kaiser", ...]
    self_pay_price numeric,
    sliding_scale  jsonb,                                -- {"<138": [0, 30], ...}
    charity_care   jsonb,                                -- hospital discount thresholds from HCAI
    source_url     text NOT NULL,
    crawled_at     timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS locations_geom_idx ON locations USING gist (geom);
CREATE INDEX IF NOT EXISTS locations_accepts_idx ON locations USING gin (accepts);

-- Last good copy, for rollback after a bad swap.
CREATE TABLE IF NOT EXISTS locations_prev (LIKE locations INCLUDING ALL);

CREATE TABLE IF NOT EXISTS verifications (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    location_id text NOT NULL,          -- no foreign key, so the crawl swap never fails on it
    checked_at  timestamptz NOT NULL DEFAULT now(),
    open_now    boolean,
    raw         jsonb
);
CREATE INDEX IF NOT EXISTS verifications_loc_idx ON verifications (location_id, checked_at DESC);

CREATE TABLE IF NOT EXISTS conversations (
    user_id    uuid PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    state      text NOT NULL DEFAULT 'new',
    -- states: new | onboarding_card | onboarding_confirm | onboarding_household | onboarding_income
    --         | onboarding_language | idle | awaiting_location | awaiting_followup_reply
    context    jsonb NOT NULL DEFAULT '{}'::jsonb,
    -- context: symptom_text, pending card extraction, last_level_of_care, recommended_location_ids.
    -- Clear it when a conversation ends, keeping only what the follow-up needs.
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS followups (
    id      uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    topic   text NOT NULL,
    due_at  timestamptz NOT NULL,
    sent_at timestamptz
);
CREATE INDEX IF NOT EXISTS followups_due_idx ON followups (due_at) WHERE sent_at IS NULL;

CREATE TABLE IF NOT EXISTS crawl_runs (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    branch_name text NOT NULL,
    started_at  timestamptz NOT NULL DEFAULT now(),
    finished_at timestamptz,
    row_count   int,
    checks      jsonb,
    passed      boolean,
    swapped_at  timestamptz
);

-- Geocoding lookup: about 40 San Diego neighborhoods and ZIP codes, seeded by hand.
CREATE TABLE IF NOT EXISTS sd_places (
    name    text PRIMARY KEY,
    aliases text[] NOT NULL DEFAULT '{}',
    lat     double precision NOT NULL,
    lon     double precision NOT NULL
);

-- Open now. hours: {"mon": [["08:00","20:00"]], ...}; missing day = closed. Times are San Diego local.
-- A span is [open, close): open at 08:00, closed at exactly 20:00.
-- Open until midnight: close "24:00". Open 24 hours: [["00:00","24:00"]].
-- Overnight (22:00 to 02:00): split into "mon": [["22:00","24:00"]] and "tue": [["00:00","02:00"]].
CREATE OR REPLACE FUNCTION is_open_now(hours jsonb, at_ts timestamptz DEFAULT now())
RETURNS boolean LANGUAGE sql STABLE AS $$
  SELECT EXISTS (
    SELECT 1
      FROM jsonb_array_elements(
             COALESCE(hours -> lower(to_char(at_ts AT TIME ZONE 'America/Los_Angeles', 'Dy')), '[]'::jsonb)
           ) AS span
     WHERE (at_ts AT TIME ZONE 'America/Los_Angeles')::time >= (span->>0)::time
       AND (at_ts AT TIME ZONE 'America/Los_Angeles')::time <  (span->>1)::time
  )
$$;
