# Module interfaces

These contracts let several agents build in parallel. Shared types live in `app/models.py`. If you change a signature here, update this doc and `app/models.py` in the same PR and mention it in the PR description.

## Request flow

```
Sendblue webhook (app/main.py)
  -> messaging.parse_inbound()            -> InboundMessage
  -> conversation.router.handle(msg)      -> list[str] replies
       |- STOP / HELP (always first; STOP sets users.opted_out)
       |- commands (#profile, #followup, #reset)
       |- onboarding steps (if profile incomplete)
       |- safety.screen(text)              -> SafetyResult   (ALWAYS before any LLM call)
       |- pipeline.first_reply(...)        -> Text 1 (first aid + "where are you?")
       |- pipeline.options_reply(...)      -> Text 2 (2-3 option cards)
  -> messaging.send_text(phone, reply) for each reply
```

## Contracts

### messaging (`app/messaging/__init__.py` re-exports the active provider)
Provider chosen by `MESSAGING_PROVIDER` (`sendblue` default, `textbee` fallback). Each provider module (`sendblue.py`, `textbee.py`) exposes the same three functions. If Sendblue's free plan has no inbound webhooks, `app/messaging/poller.py` polls for new messages and feeds them to the same handler.
- `parse_inbound(payload: dict) -> InboundMessage | None`  (None for outbound echoes, status callbacks, group messages)
- `send_text(phone: str, text: str) -> None`
- `download_media(url: str) -> bytes`  (inbound media CDN links expire, download immediately)

Inbound path: webhook (`app/main.py`) or poller (`app/messaging/poller.py`) -> `app/messaging/handler.handle_inbound(msg)`: dedupes on `message_id`, downloads media, calls `conversation.router.handle`, sends replies. Until the router exists it echoes.

### safety (`app/safety/keywords.py`)
- `screen(text: str, language: str = "en") -> SafetyResult`
  Pure function, no network. `SafetyResult.reply` is a fixed string (911, plus 988 for self-harm).
- `NOT_FOR_EMERGENCIES[lang]`: fixed notice for the welcome text and HELP reply (911 and 988).

### llm (`app/llm/gemini.py`)
- `generate_json(prompt: str, schema: dict, image: bytes | None = None, system: str | None = None) -> dict`
- `generate_text(prompt: str, system: str | None = None) -> str`

### triage (`app/triage/assess.py`)
- `first_aid_reply(text: str, profile: Profile) -> TriageResult`
  Returns `red_flag=True` if Gemini's second-net check fires. Otherwise first-aid text (no diagnosis) ending with "Where are you right now?".
- `level_of_care(text: str, profile: Profile) -> tuple[CareLevel, float]`
- `bump_if_unsure(level: CareLevel, confidence: float, threshold: float = 0.7) -> CareLevel`  (pure)

### geo (`app/geo/geocode.py`)
- `extract_place(text: str) -> str | None`  Gemini pulls the place out ("I'm in La Jolla" -> "La Jolla")
- `geocode(text: str) -> GeoPoint | None`  `sd_places` lookup (name or alias, also ZIP), then Nominatim (max 1 req/s, real User-Agent). None means the caller asks for a ZIP.

### care
- `app/care/find_care.py`: `find_candidates(profile: Profile, level: CareLevel, point: GeoPoint, radius_miles: float = 5, limit: int = 10, *, needs_xray: bool = False, at: datetime | None = None) -> list[Location]`
  One PostGIS query using the SQL function `is_open_now(hours)` (see `db/schema.sql` and HANDOFF section 6). Filters: `accepts ? plan_id OR accepts ? coverage_type`, open now, kind allowed for this level (Kaiser members: Kaiser sites only, plus ERs at the ER level), walk-in for urgent care, `needs_xray` drops places known to lack X-ray (unknown stays), within radius. Nearest first, `distance_miles` set. `at` pins the clock for tests and demos.
- `app/care/find_care.py`: `find_candidates_widening(...same args...) -> tuple[list[Location], float]`  runs at `radius_miles`, then once at 15 miles if empty; returns the radius used. Empty means send `render_fallback`.
- `app/care/find_care.py`: `is_open_now(hours: dict, at: datetime | None = None) -> bool` and `closes_at(hours, at=None) -> time | None`  pure Python mirrors of the SQL function, for rendering "open until 8 PM".
- `app/care/rank.py`: `rank(candidates: list[Location], profile: Profile, symptom_text: str, level: CareLevel) -> list[RankedOption]`
  Gemini returns IDs and reasons. Drop any ID not in `candidates`. Max 3.
- `app/care/cost.py`: `estimate_cost(location: Location, profile: Profile, level: CareLevel) -> CostEstimate`  (pure plus DB lookup of plan benefits)
- `app/care/verify.py`: `verify_live(location: Location) -> Verification`  (one Bright Data call, writes to `verifications`)
- `app/care/render.py`: `render_options(options: list[RenderedOption], language: str, *, now: datetime | None = None) -> str`  (`now` pins the clock for "open until" and "verified X min ago")
- `app/care/render.py`: `render_fallback(profile: Profile, language: str) -> str`

### i18n (`app/i18n/templates.py`)
- `t(key: str, language: str, **kwargs) -> str`

### conversation
- `app/conversation/router.py`: `handle(msg: InboundMessage) -> list[str]`
  Order: opted-out check -> STOP/START/HELP and #commands -> `safety.screen` (every state) -> onboarding if no active profile or an onboarding state -> follow-up reply -> `awaiting_location`: `pipeline.options_reply` -> else `pipeline.first_reply`. Stubs that raise `NotImplementedError` get placeholder replies.
- `app/conversation/store.py`: repository helpers for `users`, `profiles`, `conversations`. Use these instead of new SQL: `get_or_create_user(phone)`, `get_active_profile(user)`, `list_profiles(user_id)`, `upsert_profile(user_id, label, **fields)`, `set_active_profile`, `ensure_demo_profile(user, label)`, `set_opted_out`, `set_language`, `delete_profiles`, `delete_user`, `get_conversation(user_id) -> (state, context)`, `set_conversation(user_id, state, context=None)` (None keeps context).
  **State ownership:** pipeline, onboarding, and follow-up set their own next state with `set_conversation`. The pipeline reads `symptom_text` from context in `awaiting_location`.
- `app/conversation/onboarding.py`: `handle_onboarding(user: User, msg: InboundMessage) -> list[str]`
- `app/conversation/onboarding.py`: `extract_card(image: bytes) -> CardExtraction`
- `app/conversation/fpl.py`: `income_brackets(household_size: int) -> list[IncomeBracket]`  (from current HHS poverty guidelines)
- `app/conversation/commands.py`: `handle_command(user: User, text: str) -> list[str] | None`  (None if not a command). Handles STOP, HELP, `#profile <label>`, `#followup`, `#reset`, `#whoami`.

### followup (`app/followup/scheduler.py`)
- `schedule_followup(user_id: UUID, topic: str, hours: int = 20) -> None`
- `run_due() -> int`  (send due follow-ups, return count)
- `followup_now(user_id: UUID) -> list[str]`  (used by `#followup`; set state `awaiting_followup_reply`)
- `handle_followup_reply(user: User, profile: Profile, text: str) -> list[str]`  (router calls this in state `awaiting_followup_reply`)

### db (`app/db.py`)
- `get_conn()` context manager returning a psycopg connection to `DATABASE_URL`.
- Repository helpers live with the module that owns the table (users and profiles in conversation, locations in care, followups in followup).

### crawler (`crawler/`)
- `crawler/crawl.py`: `run_crawl(targets_path: str) -> list[dict]`  raw pages via Bright Data Web Unlocker
- `crawler/extract.py`: `extract_location(raw: dict) -> dict`  Gemini extraction into `locations` columns
- `crawler/branch.py`: `create_branch(name) -> tuple[str, str]` (branch id, connection string), `delete_branch(branch_id)` (Neon API). **Never promote a branch:** it would erase users and conversations written to main after the branch was made.
- `crawler/validate.py`: `validate(branch_conn, main_conn) -> list[str]`  empty list means pass. Checks row counts, null prices, hours parse, coordinates present, no big drop vs main. Logged to `crawl_runs`.
- `crawler/swap.py`: `swap_into_main(branch_conn, main_conn) -> int`  one transaction: copy `locations` to `locations_prev`, delete, insert validated rows, commit. `rollback(main_conn)` restores from `locations_prev`.
- `crawler/chg_fhir.py`: `fetch_chg_locations() -> list[dict]`  CHG public FHIR provider directory (Da Vinci Plan-Net), cached in Neon, never called live. Base: `https://api-chgsd-prd.safhir.io/v1/api/provider-directory/`
- `crawler/benefits/extract_plans.py`: `extract_plan(pdf_url: str) -> dict`  into `plans` columns
