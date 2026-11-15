# Care Navigator

Text one number with any medical question. It knows your coverage and location, so it tells you what to do, where to go, and what it will cost.

Built for the Bright Data "Data for Good" hackathon. San Diego only for now. Works for Kaiser, Medi-Cal (Community Health Group), and uninsured users.

## Docs
- [CLAUDE.md](CLAUDE.md): short context and rules for humans and agents
- [docs/HANDOFF.md](docs/HANDOFF.md): every product decision, flows, demo script, research with sources
- [docs/interfaces.md](docs/interfaces.md): module contracts so work can run in parallel
- [docs/original-plan.md](docs/original-plan.md): first rough plan (superseded where it conflicts)

## Setup
```bash
uv sync
cp .env.example .env   # fill in keys
uv run uvicorn app.main:app --reload --port 8000
```

Apply the schema to your Neon database:
```bash
psql "$DATABASE_URL" -f db/schema.sql
psql "$DATABASE_URL" -f db/seed/seed_locations.sql
```

Run tests:
```bash
uv run pytest
```

## Layout
```
app/
  main.py              FastAPI app and Sendblue webhook
  models.py            shared data types (contract)
  pipeline.py          end-to-end question flow
  messaging/           Sendblue send, receive, media download
  safety/              hard-coded red-flag screen
  conversation/        state machine, onboarding, #commands
  llm/                 Gemini client
  triage/              first-aid text and level of care
  geo/                 geocoding free-text locations
  care/                candidate query, ranking, cost, live verify, rendering
  i18n/                English and Spanish templates
  followup/            next-day follow-up
crawler/               Bright Data crawl into a Neon branch, validate, swap into main (never promote)
crawler/benefits/      plan benefit extraction (Kaiser, CHG)
db/                    schema and seeds
tests/
```

## Work items
Each GitHub issue lists the files it owns, so agents can work in parallel without conflicts. Owner labels: `angad` and `aran`.

| Owner | Focus | Issues |
|---|---|---|
| **Angad** | Messaging, conversation, safety, Bright Data, demo | #1 Sendblue round trip (P0, first), #3 Safety, #9 Router, #10 Onboarding, #5 Geocoding, #12 Bright Data crawl, #15 Live verify, #16 Follow-up, #18 Demo + Devpost |
| **Aran** | Neon, Gemini, care logic, data in the DB | #2 Neon setup (P0, first), #4 Gemini + triage (P0, first), #6 Candidate query, #7 Ranking + rendering, #8 Cost, #11 CHG FHIR, #13 Crawl branch + swap, #14 Plan benefits, #17 Pipeline integration |

Handoffs between owners: #12 crawl rows (Angad) feed #13 swap (Aran). #9 router (Angad) calls #17 pipeline (Aran). #5 geocoding (Angad) reads `sd_places` seeded in #2 (Aran).
