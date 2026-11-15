# CLAUDE.md: Care Navigator (Bright Data "Data for Good" hackathon)

Read this first, then `docs/HANDOFF.md` (every product decision and why) and `docs/interfaces.md` (the module contracts). `docs/original-plan.md` is the first rough plan, kept for history. **Where they disagree, `docs/HANDOFF.md` wins.**

## What we are building
An iMessage healthcare assistant for anyone in San Diego, with any insurance or none. Onboarding happens by text (card photo, household and income bracket, language). Then the user texts any medical question and gets: a safety screen, first-aid steps, a "where are you?" question, then 2 or 3 ranked nearby options with cost for their coverage, hours, distance, and a source link with a verification time. Next-day follow-up text.

Pitch: "Text one number with any medical question. It knows your coverage and location, so it tells you what to do, where to go, and what it will cost."

## Stack (locked)
- **Messaging:** Sendblue (iMessage), behind a provider interface. Its free plan may lack inbound webhooks and outbound texts: test first (issue for build step 1). Fallbacks: poll Sendblue, or switch to textbee (Android SMS gateway). Not Twilio.
- **Backend:** Python 3.11+, FastAPI webhook. No LangGraph.
- **LLM:** Gemini Flash for everything (vision, safety double-check, level of care, ranking, wording).
- **Database:** Neon Postgres + PostGIS. Neon is a prize track ("strongest database-driven app or agent backend").
- **Data:** Bright Data crawl into a Neon branch, validate there, then **swap rows into main in one transaction** (`crawler/swap.py`). **Never promote the branch**: it would erase users and conversations saved to main after it was made. One live Bright Data re-check of the #1 option per answer. $100 hackathon Bright Data credits per team.
- **CHG network:** from CHG's public FHIR provider directory API, cached in Neon. Not scraped.
- **Geocoding:** Gemini extracts the place, `sd_places` lookup table, then Nominatim, then ask for a ZIP.
- **IDs:** plans and locations use stable text keys (`chg_medi_cal`, `fhcsd_city_heights`) so re-crawls update rows in place.

## Non-negotiable rules
1. **Safety in code first.** `app/safety/keywords.py` runs before any LLM call and returns a fixed 911 / 988 reply. The Gemini prompt is a second net only.
2. **Never diagnose.** General first-aid steps only.
3. **When in doubt, go one level of care up.** Enforced in code: low Gemini confidence bumps the level.
4. **The LLM never writes facts.** Gemini returns ranked location IDs and a short reason. Code renders name, address, price, hours, link from Neon. Reject IDs not in the candidate set.
5. **Never invent an option.** No match: widen radius once, then honest fallback (plan nurse line, 211 San Diego, telehealth).
6. **Privacy:** never store the member ID or the card image. Extract insurer and plan, then discard. Clear `conversations.context` when a conversation ends, keeping only what the follow-up needs.
7. **Freshness labels are honest:** #1 option says "verified X min ago" (live check); others say "checked [crawl time]".
8. **STOP and HELP work everywhere.** STOP sets `users.opted_out` and cancels follow-ups. Welcome text and HELP both say "This is not for emergencies. Call 911."
9. **Confirm dollar figures** (FHCSD sliding fee, cash urgent care prices) before saying them on stage.

## Scope
- San Diego only. One demo phone.
- Coverage: Kaiser, Medi-Cal via **Community Health Group (CHG)**, uninsured.
- Languages: English and Spanish.
- Skip: booking, accounts, web onboarding, voice (ElevenLabs is later).

## Demo (60 seconds, one phone)
Text "think i sprained my ankle" three times, switching profile with `#profile kaiser`, `#profile medi_cal`, `#profile uninsured`. Then `#followup`. Same question, three different correct answers.

## Hackathon rules that can disqualify us
- **Commit window:** no commits before Saturday Nov 14, 10:30 AM or after **Sunday Nov 15, 12:00 PM** Pacific. Check the time before committing.
- Initial Devpost submission Sunday 11 AM; final Sunday 12 PM. Needs a 3-minute demo video, write-up, repo link.
- Judging Sunday 12:30 to 2:30 PM. Every team member must be at the table.
- Prize tracks: Bright Data Data for Good, Neon, MLH Gemini API, Best Overall / Most Innovative, GoDaddy domain, DEV writing. See HANDOFF section 12.

## Claiming an issue (do this before writing any code)
1. Check the issue is free: `gh issue view <n> --json labels,assignees,comments` shows no `in-progress` label and no "claimed" comment, `gh pr list --state open` has no PR for it, and `git ls-remote --heads origin` has no `issue-<n>-*` branch. Run this for that exact issue right before claiming, and read who wrote each "Claimed by" comment; a list of in-progress numbers is not enough (that is how #11 got built twice).
2. If it is taken, pick another issue. Do not work on it in parallel.
3. Claim it: `gh issue edit <n> --add-label in-progress` and comment `Claimed by <name>'s agent, branch issue-<n>-<slug>`. Push the branch right away so others can see it.
4. When the PR merges (or you give up), remove the `in-progress` label.

## Working in this repo (for agents)
- Work is split into GitHub issues. Each issue names the files it owns. **Only edit files your issue owns.** If you need a change to a shared contract (`app/models.py`, `db/schema.sql`, `docs/interfaces.md`), make it a small separate commit and say so in the PR.
- Branch per issue: `issue-<number>-<slug>`. Open a PR that says `Closes #<number>`.
- Stubs raise `NotImplementedError`. Keep the function signatures in `docs/interfaces.md` unless you update that doc in the same PR.
- Tests go in `tests/`, named after the module (`tests/test_safety.py`). Run with `uv run pytest`.

## Before you push or merge
- **Push only after `uv run pytest` exits 0.** Check the exit code itself; piping through `tail` or `grep` hides it.
- **Tests never use the production database.** Database tests run against `TEST_DATABASE_URL`, a separate Neon branch that `tests/conftest.py` resets to schema plus seeds at the start of each run. Without it they skip. Assert rules (in network, open now, nearest first), not exact row ids, so loading more data can't break them.
- **Don't merge code that needs an unmerged PR.** If your code imports a module or function that only exists in another open PR, write "Depends on #N" in the PR and wait for #N to merge. `tests/test_imports.py` fails when an import doesn't resolve, including imports inside functions that unit tests fake.
- **CI** (`.github/workflows/tests.yml`) runs `uv run pytest` on every PR without secrets: unit tests and import checks run, database and live tests skip. Don't merge a red PR.
- Unit tests fake Gemini, Nominatim, Bright Data and Sendblue. Live calls only behind `RUN_LIVE_GEMINI=1` / `RUN_LIVE_GEO=1`.
- Secrets live in `.env` (see `.env.example`). Never commit `.env`.

## Style
- Plain, direct prose in docs and UI copy. No em dashes.
- Honest, defensible claims. No inflated metrics.
- Python for agent and data.
