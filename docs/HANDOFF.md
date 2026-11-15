# Care Navigator: Team Handoff

Bright Data "Data for Good" hackathon (SanD Hacks, Nov 14 to 15). This file replaces the rough first plan (`docs/original-plan.md`) where the two disagree. It records every decision made in planning so a teammate (or their agent) can start building right away.

## 0. What changed in this version

Read this first if you saw the earlier draft.

1. **Sendblue's free plan may not send inbound webhooks or allow outbound texts.** Build step 1 now tests this before anything else, with fallbacks (section 3, section 9).
2. **Crawl branch promotion replaced** with "validate in a branch, then swap into main." Promoting a branch would have erased user data written to main after the branch was made (section 6).
3. **CHG has a public provider directory API.** It replaces scraping for the Medi-Cal network (sections 6 and 8).
4. **Bright Data budget:** the hackathon gives every team $100 in API credits.
5. **Geocoding decided:** San Diego place lookup table, with Nominatim as the fallback.
6. **Hours format and an `is_open_now()` SQL function** defined, so "open now" works in the candidate query.
7. **Plan and location IDs are stable text keys**, so they survive each re-crawl.
8. **Safety additions:** emergency notice in the welcome text, STOP and HELP, and two more red flags to review.
9. **Pitch stats corrected** (18% study scope, 2M attribution). The Twilio finding is corrected too.
10. **New sections:** prize tracks we're entering (section 12) and hackathon deadlines and rules (section 13).

## 1. The product in one paragraph

A text-message (iMessage) healthcare assistant for anyone in San Diego, with any insurance or none. The user onboards by text in about 2 minutes: they send a photo of their insurance card and answer a few questions. After that, they can text any medical question. The assistant screens for emergencies, gives first-aid steps, asks where they are, then sends 2 or 3 ranked nearby options with cost for *their* coverage, hours, distance, and a source link with a verification time. It follows up the next day.

**Pitch line:** "Text one number with any medical question. It knows your coverage and location, so it tells you what to do, where to go, and what it will cost."

**Answer to "why not ChatGPT or my insurer's app?":** Insurer bots only serve their own members. ChatGPT has no plan, deductible, or local network context. We serve any plan or none, with a specific local answer that includes cost. Uninsured and Medi-Cal users get first-class answers (sliding scale, charity care, cash prices).

## 2. Constraints

- **Time and team:** 24 hours or less, 1 to 2 builders. Every decision below is scoped to that.
- **Demo:** runs on **one phone**.
- **Geography:** San Diego only.
- **Out of scope:** booking, full accounts, web onboarding (ElevenLabs voice onboarding is a later idea, not now).

## 3. Locked decisions

| Area | Decision | Notes |
|---|---|---|
| Messaging | **Sendblue (iMessage)**, with a tested fallback | Free plan uses a shared line; the contact must text our number first. Inbound photos arrive as a CDN link (expires in 30 days, so download right away). **Risk:** a third-party summary of Sendblue's pricing page (Nov 7, 2026) lists the free sandbox as having no outbound messaging and no webhooks or callbacks. Build step 1 checks this. If inbound webhooks don't fire, poll the Sendblue API for new messages if it offers a list endpoint; otherwise switch to **textbee** (an Android phone as an SMS gateway, webhooks supported, free tier of 300 messages a month). |
| Backend | **FastAPI webhook** (Python) | Receives inbound messages, runs the pipeline, replies through the messaging API. No LangGraph. |
| LLM | **Gemini Flash for everything** | Card photo vision, safety double-check, level of care, ranking, reply wording. |
| Database | **Neon Postgres + PostGIS** | Neon is a hackathon track: "strongest database-driven app or agent backend built on Neon." PostGIS is supported on all Neon plans, including free. |
| Onboarding | **Over text**, not web | Card photo, household + income bracket, language. Transport question dropped. |
| Card photo | **Gemini vision + confirm** | Extract insurer and plan, reply "Looks like Kaiser HMO. Right? (Y/N)". Fallback: user types plan or picks "uninsured / Medi-Cal / other". |
| Privacy | **Do not store member ID or the card image** | Extract insurer and plan, then discard. Pitch line: "we keep the minimum." Clear `conversations.context` when a conversation ends, keeping only what the follow-up needs. |
| Location | **Ask every time** | Asked in the same first reply that already gives first-aid steps, so the wait doesn't feel slow. |
| Geocoding | **Lookup table first, Nominatim second** | Gemini pulls the place out of the reply ("I'm in La Jolla" becomes "La Jolla"). Look it up in `sd_places` (about 40 San Diego neighborhoods and ZIP codes with coordinates, seeded by hand). Anything not found goes to Nominatim, which allows at most 1 request per second and needs a real User-Agent. If both fail, ask for a ZIP code. |
| Safety | **Hard-coded keyword layer first, then strict LLM prompt** | Keywords trigger a fixed 911 / 988 reply before Gemini sees the message. Gemini's prompt is the second net. Safety lives in code. |
| Level of care | **Gemini picks, code bumps up** | Gemini returns one of `self_care / telehealth / urgent_care / primary_care / er` plus confidence. Low confidence: code moves it up one level. |
| Ranking | **Gemini ranks candidates, returns IDs only** | Code queries candidates from Neon; Gemini returns ranked location IDs + one-line reason each. **Code renders every fact** (name, address, price, hours, link) from the DB. The LLM never writes a price or clinic name. Validate returned IDs are in the candidate set. |
| Data | **Pre-scrape with Bright Data**, plus one live re-check | Crawl writes to a Neon branch, gets validated there, then is swapped into main (section 6). At message time, one live Bright Data call re-checks the #1 option. |
| Freshness label | #1 option: "verified X min ago". Others: "checked [date/time of crawl]" | Honest about what was live-checked. |
| Plan benefits | **Pre-extract 2 to 3 plans** | Bright Data + Gemini over benefit summary PDFs. Label costs "estimated from your plan's summary". |
| Medi-Cal plan for demo | **Community Health Group (CHG)** | Medi-Cal in San Diego is managed care, so "in network" depends on the plan. **Get CHG's network from its public FHIR provider directory API** (section 6), not by scraping. |
| Uninsured framing | **Show the tradeoff** | Cash urgent care with X-ray vs sliding-scale clinic vs when to go to the ER. |
| Language | **English + Spanish** | Use profile language; switch if the user writes in the other. Code-rendered option cards have en/es templates. |
| No match | **Widen radius once, then honest fallback** | Fallback: "I don't have verified options near you yet" + plan nurse/advice line if known + 211 San Diego + telehealth if covered. Never invent an option. |
| Follow-up | **Build it, with a manual trigger** | Scheduled next-day text, plus a `#followup` command to fire it on stage. If the messaging plan blocks outbound texts, only `#followup` works, because it's a reply to the user's message. That's enough for the demo. |
| Demo profiles | **Switch profiles live** | A command like `#profile kaiser`, `#profile medi_cal`, `#profile uninsured` swaps the active profile so one phone shows three answers. |
| IDs | **Stable text keys for plans and locations** | For example `chg_medi_cal`, `kaiser_cc_silver`, `fhcsd_city_heights`. Built from the source, so a re-crawl updates the same row instead of creating a new one. |

## 4. Message flows

### 4.1 Onboarding (by text)
1. User texts the number (required first by Sendblue free plan).
2. Bot: welcome + "This is not for emergencies. If you think it's an emergency, call 911." + "Send a photo of your insurance card, or reply NONE / MEDI-CAL / OTHER."
3. Photo: Gemini vision extracts insurer + plan. Bot confirms "Looks like [plan]. Right? (Y/N)". Discard image and member ID.
4. "How many people in your household?"
5. Show 4 dollar income brackets calculated for that household size, mapped to FPL tiers (`<138`, `138-200`, `200-400`, `>400`), plus "skip". One-line reason: "This unlocks discounts at clinics." **Compute brackets from the current HHS poverty guidelines; do not hard-code numbers from memory.**
6. "English or Español?"
7. Done. Profile saved to Neon.

At any point: **STOP** ends all messages (including follow-ups) and **HELP** replies with what the service does and the 911 / 988 notice.

### 4.2 Question flow ("think i sprained my ankle")
1. **Keyword safety screen (code).** Match: send fixed 911 / ER message (plus 988 for suicidal thoughts). Stop.
2. **Gemini safety double-check** as part of the main call. Flag: same fixed message.
3. **Text 1 (instant):** general first-aid steps (rest, ice, compression, elevation), ER warning signs (can't bear weight at all, deformity, numbness), and "Where are you right now?" Never diagnose.
4. User replies with a place. Geocode it (`sd_places`, then Nominatim, then ask for a ZIP).
5. **Level of care:** Gemini returns level + confidence; code bumps up one level if confidence is low.
6. **Candidates (code):** one PostGIS query in Neon filtered by coverage accepted, `is_open_now(hours)`, can handle this level of care (for example has X-ray), within radius. If none, widen radius once, then fallback.
7. **Ranking:** Gemini gets candidates + profile, returns ranked IDs + reason.
8. **Live verify:** one Bright Data call on the #1 option (open, hours). Store result and timestamp in `verifications`.
9. **Cost estimate (code):** plan copay from extracted benefits, or cash price, or sliding-scale fee at their income bracket.
10. **Text 2:** 2 or 3 option cards rendered by code from DB fields: name, distance, hours, estimated cost for this user, source link, verification time.
11. **Save for the follow-up:** store the level of care and the recommended location IDs in `conversations.context`, and queue a `followups` row for tomorrow.

### 4.3 Follow-up
Next day (or `#followup` on stage): "How's the ankle? Reply 1 better, 2 same, 3 worse." "Worse" re-runs triage one level above the level saved in step 11.

## 5. Demo script (one phone, about 60 seconds)

Same message three times: **"think i sprained my ankle"**, switching profile between each with `#profile ...`.

- **Kaiser:** Kaiser advice line + nearest Kaiser urgent care + copay from plan summary.
- **Medi-Cal (CHG):** nearest CHG in-network health center open now, $0.
- **Uninsured:** the tradeoff. Weekdays: Family Health Centers of San Diego walk-in at $25 per visit for qualifying uninsured patients (may turn away walk-ins when busy, may lack X-ray) vs cash urgent care with X-ray. **On Sunday (judging day) FHCSD walk-ins are closed**, so the live answer is cash urgent care; see `docs/demo.md`.

Close with `#followup` to show it keeps caring after the first answer.

"Same question, three different correct answers."

The same flow is the core of the **3-minute submission video**. Record it Sunday morning, before the 11 AM initial submission (section 13).

## 6. Data and crawl

### Sources in tonight's crawl (all four selected)
1. **Urgent care chain pages:** hours, self-pay prices, wait times (AFC, Concentra, South Bay Urgent Care, UCSD Express Care, others).
2. **FQHC sites** (FHCSD and others): walk-in hours, sliding-scale rules, which Medi-Cal plans they accept.
3. **Kaiser San Diego facilities:** urgent care locations, hours, advice line.
4. **HCAI charity care policies:** each San Diego hospital's discount and charity care thresholds (PDF heavy; lowest priority if time runs short).

### Not a scrape: CHG network from its FHIR API
CHG publishes a public, no-login provider directory API, as Medi-Cal managed care plans are required to:
`https://api-chgsd-prd.safhir.io/v1/api/provider-directory/metadata`

Curl it tonight to confirm it responds and see which resources it supports. Provider directory APIs usually follow the Da Vinci Plan-Net format (Location, Organization, HealthcareService, InsurancePlan). Pull the San Diego locations, then add `chg_medi_cal` to `accepts` on matching rows in `locations`. Cache the result in Neon; don't call it live.

### Bright Data budget
- **Hackathon credits:** $100 in Bright Data API credits per team (from the opening ceremony slides). Redeem through Slack or the help desk.
- **Free MCP tier:** about 5,000 requests a month, Web Unlocker + search only. Scraping Browser is paid, so use the $100 credits for any page that needs it.
- Keep the target list tight (about 100 pages). Pages are fetched once in the nightly crawl, plus one live check per answered question.

### Neon usage (the Neon track pitch)
1. **PostGIS candidate query:** coverage, open now, capability, and distance in one SQL query (draft below).
2. **Crawl branch, validate, swap into main.**
   - Each crawl writes to a **new Neon branch**, never to main.
   - Validation runs on the branch: row counts, null prices, hours parse, every row has coordinates, no big drop in rows from the last crawl. Log the result in `crawl_runs`.
   - If it passes, a script copies the validated `locations` rows from the branch into main **in one transaction**: copy the current table to `locations_prev`, delete, insert the new rows, commit. People texting during the swap see the old data until the commit.
   - If it fails, main is untouched. Rollback after a bad swap: restore from `locations_prev`.
   - Keep each crawl branch as a snapshot, so you can show what changed between crawls.
   - **Do not promote the branch to main.** A branch is a copy of the whole database. Promoting a branch made at 1 AM would erase any profiles and conversations saved to main after 1 AM. Promoting also means moving the compute to the new branch so the connection string stays the same.
3. **Agent state in Neon:** profiles, conversation state, verification timestamps, follow-up queue.

### Draft tables (refine as you build)
```sql
CREATE EXTENSION IF NOT EXISTS postgis;

-- users: one row per phone; active profile can be switched for the demo
users(id uuid pk, phone text unique, active_profile_id uuid, language text,
      opted_out bool default false, created_at timestamptz)

profiles(id uuid pk, user_id uuid, label text,              -- 'kaiser' | 'medi_cal' | 'uninsured'
         coverage_type text,                                 -- commercial | kaiser | medi_cal | covered_ca | medicare | uninsured
         insurer text, plan_id text null,                    -- no member_id column, on purpose
         household_size int, income_range_fpl text)          -- '<138' | '138-200' | '200-400' | '>400' | null

plans(id text pk,                                            -- stable key, e.g. 'chg_medi_cal'
      insurer text, plan_name text,
      urgent_care_copay numeric, er_copay numeric, pcp_copay numeric,
      telehealth text, nurse_line text, deductible numeric,
      source_url text, extracted_at timestamptz)

locations(id text pk,                                        -- stable key, e.g. 'fhcsd_city_heights'
          name text, kind text,                              -- urgent_care | fqhc | kaiser | er | lab | telehealth
          geom geography(point), address text, phone text,
          hours jsonb,                                       -- {"mon": [["08:00","20:00"]], ...}; missing day = closed
          has_xray bool, walk_in bool,
          accepts jsonb,                                     -- ["chg_medi_cal", "uninsured", ...]; GIN index
          self_pay_price numeric null, sliding_scale jsonb null,
          source_url text, crawled_at timestamptz)
-- locations_prev: same columns, the last good copy for rollback

verifications(id uuid pk, location_id text, checked_at timestamptz, open_now bool, raw jsonb)
-- location_id has no foreign key, so the crawl swap never fails on it

conversations(id uuid pk, user_id uuid, state text,          -- onboarding step, awaiting_location, etc.
              context jsonb,                                 -- includes last_level_of_care, recommended location ids
              updated_at timestamptz)

followups(id uuid pk, user_id uuid, due_at timestamptz, sent_at timestamptz null, topic text)

crawl_runs(id uuid pk, branch_name text, started_at timestamptz, finished_at timestamptz,
           row_count int, checks jsonb, passed bool, swapped_at timestamptz null)

sd_places(name text pk, aliases text[], lat double precision, lon double precision)
```

### Open now (tested on Postgres)
```sql
-- hours: {"mon": [["08:00","20:00"]], "tue": [...], ...}; missing day = closed.
-- Split an overnight span (22:00 to 02:00) into two spans across two days.
CREATE OR REPLACE FUNCTION is_open_now(hours jsonb, at_ts timestamptz DEFAULT now())
RETURNS boolean LANGUAGE sql STABLE AS $$
  SELECT EXISTS (
    SELECT 1
      FROM jsonb_array_elements(
             hours -> lower(to_char(at_ts AT TIME ZONE 'America/Los_Angeles', 'Dy'))
           ) AS span
     WHERE (at_ts AT TIME ZONE 'America/Los_Angeles')::time
           BETWEEN (span->>0)::time AND (span->>1)::time
  )
$$;
```

### Candidate query (draft, not yet run against PostGIS)
```sql
SELECT id, name, kind, address, phone, self_pay_price, sliding_scale, source_url, crawled_at,
       ST_Distance(geom, ST_MakePoint(%(lon)s, %(lat)s)::geography) / 1609.34 AS miles
  FROM locations
 WHERE (accepts ? %(plan_id)s OR accepts ? %(coverage_type)s)
   AND is_open_now(hours)
   AND kind = ANY(%(kinds_for_level)s)
   AND (NOT %(needs_xray)s OR has_xray)
   AND ST_DWithin(geom, ST_MakePoint(%(lon)s, %(lat)s)::geography, %(radius_m)s)
 ORDER BY miles
 LIMIT 10;
```

## 7. Safety rules
- Keyword and regex list in code runs before any LLM call: chest pain or pressure, trouble breathing, stroke signs (face droop, arm weakness, slurred speech), severe bleeding, suicidal thoughts, overdose, unconsciousness, and similar. Include common typos and Spanish equivalents.
- **Consider adding:** bleeding during pregnancy, and fever in a young infant. Check the wording against a reputable source (for example the CDC or the American Academy of Pediatrics) before adding.
- Fixed responses, not generated: "Call 911 or go to the ER now." Add 988 for suicidal thoughts.
- The welcome text and HELP reply both say the service is not for emergencies.
- Gemini system prompt repeats the red flags as a second net.
- Never diagnose. General first-aid steps only.
- When in doubt, go one level of care up (enforced in code via confidence).
- STOP is honored everywhere, including queued follow-ups (`users.opted_out`).

## 8. Research findings (with sources)

- **Twilio trial:** can only text verified numbers (up to 5), and every message starts with "Sent from a Twilio Trial account". US texting at scale needs A2P 10DLC registration (paid accounts only) or toll-free verification. Not used. [Twilio free trial](https://twilio.com/docs/messaging/tutorials/use-your-free-twilio-trial-account), [Trial FAQ](https://support.twilio.com/hc/en-us/articles/360036052753)
- **Sendblue:** free plan on a shared line; contact texts first; inbound media as CDN link. [Sendblue llms.txt](https://www.sendblue.com/llms.txt), [Receiving messages](https://docs.sendblue.com/getting-started/receiving-messages/index.md), [Limits](https://docs.sendblue.com/limits/). A third-party read of the pricing page on Nov 7, 2026 lists the free sandbox as: shared number, up to 10 verified contacts, no outbound messaging, no webhooks or callbacks. [Sendblue pricing breakdown](https://getkanal.com/blog/sendblue-pricing). Not yet confirmed against Sendblue's own docs.
- **textbee (fallback):** open-source, turns an Android phone into an SMS gateway with a REST API and webhooks; free tier of 300 messages a month; recipients can use any phone. [textbee](https://textbee.dev/), [Quickstart](https://textbee.dev/quickstart)
- **Neon:** PostGIS installs on any Neon project; free plan 1 GB per project. [PostGIS on Neon](https://neon.com/docs/extensions/postgis.md), [Neon changelog Oct 2026](https://neon.com/docs/changelog/2026-10-02). Promoting a branch to default requires moving the compute to keep the same connection string. [Promote a branch](https://neon.tech/docs/guides/branch-promote)
- **Bright Data MCP:** free tier about 5,000 requests a month, Web Unlocker + search; browser automation is paid. [Bright Data MCP README](https://glama.ai/mcp/servers/@brightdata/brightdata-mcp/blob/8cde7d109024fa4826c538665b5d9f3449088078/README.md). Hackathon gives $100 in API credits per team (opening ceremony slides).
- **Medi-Cal plans in San Diego:** Community Health Group and Blue Shield Promise both serve San Diego Medi-Cal. Full 2026 plan roster not confirmed. [CHG Medi-Cal](https://chgsd.com/chg-plans/medi-cal), [Blue Shield Promise](https://www.blueshieldca.com/promise/medi-cal/index.asp), [DHCS plan list](https://www.dhcs.ca.gov/providers-partners/medicare-medi-cal-plan-list/)
- **CHG provider directory API:** public FHIR endpoint, no login. [CHG FHIR developer API](https://chgsd.com/about-us/fhir-developer-api). Medicaid managed care plans have been required to publish provider directory APIs since Jan 1, 2021. [CMS fact sheet](https://www.cms.gov/newsroom/fact-sheets/interoperability-and-patient-access-fact-sheet)
- **FHCSD (confirmed Nov 15 on FHCSD's own pages):** walk-in urgent care 8 AM to 8 PM, **closed Sundays**; sliding fee for uninsured based on income and household size; no one turned away for inability to pay. The billing page says "a reduced fee of $25 per visit for patients who do not qualify for health insurance" (patients must qualify for the sliding-fee scale by income). The earlier "$0 to $30" figure from a secondary source is wrong. [FHCSD urgent care](https://www.fhcsd.org/urgent), [FHCSD billing](https://www.fhcsd.org/billing-and-insurance)
- **San Diego cash urgent care prices** (third-party listings, may be stale): South Bay Urgent Care visit $95 to $215 + $75 extremity X-ray; AFC Mira Mesa visit $199 + X-ray $139 to $169. [Solv: South Bay](https://www.solvhealth.com/south-bay-urgent-care-san-diego--ABBkJA), [Solv: AFC Mira Mesa](https://solvhealth.com/afc-urgent-care-san-diego-ca-gQKM8a) **Not confirmed with the clinics** (South Bay's site says only "Affordable Self Pay Rates"; AFC's pricing pages block plain requests). Don't state these as facts on stage.

### Pitch background (verified Nov 14, 2026)
- **Medi-Cal losses:** about 2 million Medi-Cal members are at risk of losing coverage under H.R. 1, per the California Department of Health Care Services. Credit DHCS, not CPEHN; CPEHN's own estimate is higher (3.5 million). [DHCS](https://www.dhcs.ca.gov/?p=173078), [CPEHN](https://cpehn.org/?p=13649)
- **Medical debt:** 4 in 10 Californians have medical debt, including 55% of those with low incomes (CHCF 2026 California Health Policy Survey). [CHCF](https://www.chcf.org/resource/2026-california-health-policy-survey/)
- **Hospital discounts:** under AB 1020, uninsured patients and patients with high medical costs at or below 400% FPL are eligible for a hospital's charity care or discount payment policy. [HCAI](https://hcai.ca.gov/affordability/hospital-fair-billing-program/laws-and-regulations)
- **Directory accuracy:** in a 2023 Senate Finance Committee secret-shopper study of mental health providers listed by 12 Medicare Advantage plans in six states, staff could get an appointment only 18% of the time. Say "mental health providers in Medicare Advantage," not "people." [Senate Finance](https://www.finance.senate.gov/chairmans-news/wyden-calls-for-action-to-get-rid-of-ghost-networks-releases-secret-shopper-study)

## 9. Open items and risks
- **Sendblue inbound webhooks and outbound texts on the free plan.** Test in build step 1. Fallbacks in section 3.
- **CHG API format.** Confirm the endpoint responds and which resources it returns. If it's down or hard to parse, fall back to CHG's web directory through Bright Data for a handful of clinics near the demo locations.
- **Kaiser benefits:** pick one specific Kaiser plan to extract (Covered California Kaiser Silver is a reasonable default).
- **Income brackets:** compute from current HHS poverty guidelines at build time.
- **FHCSD and cash urgent care prices** in the demo script need confirming before they're said on stage.
- **Live verify latency:** the single Bright Data call on the #1 option must stay within a few seconds. If it's slow, send Text 2 first and the "verified" note right after.
- **Bright Data credit budget:** keep the crawl target list tight.
- **Medical safety:** keep triage conservative; never diagnose.

## 10. Suggested build order (24 hours)
1. **Messaging round trip.** FastAPI echo bot on Sendblue: text the number, confirm the webhook fires, confirm the reply arrives, confirm a photo arrives as a link. If any of these fail, switch to the fallback now, not later.
2. Neon project, PostGIS, tables, `is_open_now()`. Seed a few locations and `sd_places` by hand so the pipeline can run end to end.
3. Keyword safety layer + Gemini Text 1 (first-aid + "where are you?"). STOP and HELP.
4. Geocoding, candidate query + Gemini ranking (IDs only) + code-rendered Text 2.
5. Text onboarding + card photo vision + `#profile` switching.
6. CHG network from the FHIR API. Curl it early, even if you parse it later.
7. Bright Data crawl into a Neon branch, validate, swap into main. Replace hand seeds.
8. Plan benefit extraction for Kaiser and CHG; cost estimator.
9. Live verify on #1 option.
10. Follow-up + `#followup`. Spanish templates.
11. Rehearse the 60-second demo, record the 3-minute video, write the Devpost entry.

## 11. Style
- Plain, direct prose in docs and UI copy. No em dashes.
- Honest, defensible claims. No inflated metrics.
- Python for agent and data.

## 12. Prize tracks we're entering

Select every one of these on Devpost, and give each a sentence in the write-up so its judges can find their part fast.

| Track | Prize | How we qualify |
|---|---|---|
| Bright Data: Data for Good | $1,000 / $750 / $500 (top three teams) | Nightly crawl of clinic, urgent care and charity care pages, plus a live check on every top answer |
| Neon: Best Database-Driven / Agent Backend | Not announced | PostGIS candidate query, crawl branches with validation, agent state and follow-up queue in Neon |
| MLH: Best Use of Gemini API | MLH swag kits | Gemini API inside the product: card photo vision, safety double-check, level of care, ranking. The prize is for using the API in the product, not for using Gemini as a coding assistant |
| Best Overall Hack, Most Innovative Project | See Devpost | Every project is eligible |
| MLH: Best Domain from GoDaddy Registry | Digital gift card | Register a domain with code **MLHSH77** (backup MLHBU68) at mlh.link/GoDaddyRegistry. A one-page site with the number and how it works is enough |
| DEV Writing Challenge | $100 (one pick a month) | Post the write-up on DEV after the event (dev.to/mlh-hackathon) |

Skipped: ElevenLabs (no voice this weekend), Tiger Data (overlaps with Neon), Solana, Vultr, Mastra (TypeScript only).

## 13. Hackathon deadlines and rules

From the opening ceremony slides and Devpost.

- **Hacking space:** Qualcomm Room and WLH 2005. Open until 9 PM Saturday, then 7 AM to 2:30 PM Sunday.
- **Commit window:** no commits before Saturday 10:30 AM or after Sunday 12 PM, or the team is disqualified.
- **Initial submission:** Sunday 11 AM on Devpost (a placeholder is fine).
- **Final submission:** Sunday 12 PM. No changes after that.
- **Every submission needs:** a 3-minute demo video (public YouTube or Google Drive link), a short write-up, the GitHub repo link, and optionally a live demo link.
- **Judging:** Sunday 12:30 to 2:30 PM, science-fair style in the Qualcomm Room. 3 minutes to present, then 2 minutes of questions. **Every team member must be at the table**, or that judge gives a zero.
- **Judged on:** idea, demo, technical complexity, completion. Code quality isn't judged.
- **Slack:** set your display name to "full name + school."
