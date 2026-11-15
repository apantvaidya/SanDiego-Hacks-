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

## Team setup notes
- Get the team `.env` privately from a teammate (never commit it or paste it in issues, PRs, or Slack). Keep `DATABASE_URL` in single quotes so `source .env` works.
- **One shared Neon database** (branch `production`). Don't drop tables or run destructive SQL without telling the team; experiment on a Neon branch.
- **Bright Data has no zones yet.** Create a Web Unlocker zone in the Bright Data control panel and set `BRIGHTDATA_UNLOCKER_ZONE` before #12 or #15 can fetch pages.
- Still empty in the team `.env`: `NEON_API_KEY` (needed by #13 for crawl branches), `BRIGHTDATA_UNLOCKER_ZONE`. Each person sets their own `DEMO_PHONE` and `SENDBLUE_WEBHOOK_SECRET`.
- Optional Neon tooling for Claude Code: `npx -y neon@latest mcp --oauth --project --agent claude-code -y` and `npx -y neon@latest skills -s neon -s neon-postgres --agent claude-code -y`. Don't run `neon init` or `neon link` (they can overwrite `DATABASE_URL`).

## Messaging (Sendblue)
Tested Nov 15, 2026 on the free shared-line plan: outbound texts, inbound webhook, inbound polling, and photos all work.
- Free plan: every recipient must be added as a contact in the Sendblue dashboard and text the line once first. Only the verified demo phone can use it.
- Text and photo sent together arrive as one message (`content` + `media_url`).

Run with a public webhook (needs `brew install cloudflared`):
```bash
uv run uvicorn app.main:app --port 8000
cloudflared tunnel --url http://localhost:8000      # prints https://<random>.trycloudflare.com
uv run python -m scripts.sendblue_setup webhook https://<random>.trycloudflare.com
```
The quick-tunnel URL changes each run, so re-register the webhook after restarting cloudflared.

No tunnel? Poll instead: `MESSAGING_INBOUND=poll uv run uvicorn app.main:app --port 8000`.

Other helpers: `uv run python -m scripts.sendblue_setup show | send "<text>" | inbox`.

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
Each GitHub issue lists the files it owns, so agents can work in parallel without conflicts. Nobody is assigned yet: **claim an issue before starting it** (see "Claiming an issue" in [CLAUDE.md](CLAUDE.md)).

| Start now (P0) | Next (P1) | Later (P2) |
|---|---|---|
| #1 Messaging round trip (test Sendblue first) | #5 Geocoding | #16 Follow-up |
| #2 Neon setup, schema, seeds | #8 Cost estimator | |
| #3 Safety red-flag screen | #10 Text onboarding | |
| #4 Gemini client + triage | #13 Crawl branch, validate, swap | |
| #6 Candidate query | #14 Plan benefits | |
| #7 Ranking + rendering | #15 Live verify | |
| #9 Conversation router | #18 Demo, video, Devpost | |
| #11 CHG FHIR network | | |
| #12 Bright Data crawl | | |
| #17 Pipeline integration (after the rest) | | |
