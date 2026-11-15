# Devpost write-up

Paste into Devpost. Select every track in the table at the bottom.

## Care Navigator

**Text one number with any medical question. It knows your coverage and location, so it tells you what to do, where to go, and what it will cost.**

## Inspiration

When something goes wrong, almost nobody has their plan details on hand. Insurer chatbots only serve their own members, and general chatbots don't know your plan or what's open near you. The people with the least help are the uninsured and Medi-Cal members, and about 2 million Californians on Medi-Cal are at risk of losing coverage under H.R. 1 (California DHCS). Four in ten Californians already carry medical debt (CHCF).

## What it does

You onboard by text in about two minutes: send a photo of your insurance card (or say you're uninsured), your household size and an income range, and your language. Then text any health question, in English or Spanish:

1. **Safety first, in code.** Red flags (chest pain, stroke signs, trouble breathing, severe bleeding, suicidal thoughts, overdose, and more) get a fixed "call 911" reply, plus 988 for self-harm, before any AI runs.
2. **First aid now.** General steps and when to go to the ER, plus "Where are you right now?" It never diagnoses.
3. **Two or three places open right now**, ranked for your coverage: distance, hours, X-ray on site, what it will cost you (copay, $0 with Medi-Cal, sliding fee, or self-pay price), and the source link with when it was checked.
4. **A check-in the next day.** If it's worse, it moves you up a level of care.

Same question, three people: a Kaiser member gets Kaiser urgent care and the advice line; a Medi-Cal member gets an in-network clinic at $0; an uninsured person gets a sliding-fee or cash option and the price.

## How we built it

- **Messaging:** Sendblue iMessage webhook into a FastAPI app (Python).
- **Safety:** a hard-coded red-flag screen (11 categories, English and Spanish, typos) runs before every model call.
- **Gemini API:** reads the insurance card photo, writes first-aid steps, picks the level of care (code moves it up a level when unsure), extracts the place from "I'm near UCSD", ranks candidates, and extracts facts from crawled clinic pages. **Gemini only returns database IDs when ranking; code writes every name, price, and hour**, so it can't invent a clinic.
- **Neon Postgres + PostGIS:** one SQL query finds candidates by coverage, open-now (an `is_open_now()` SQL function over stored hours), capability (X-ray, walk-in), and distance. Users, profiles, conversation state, and the follow-up queue live in Neon too.
- **Medi-Cal network data:** Community Health Group's public FHIR provider directory (Da Vinci Plan-Net): 77 San Diego urgent care, lab, and community health center sites, with hours, geocoded and checked. The #1 option for a Medi-Cal member is re-checked live against that directory at answer time.
- **Plan costs:** Gemini reads Covered California's 2026 standard plan designs PDF, so a Kaiser Silver 70 member sees "$50 copay" for urgent care (checked by hand against page 6 of the PDF).
- **Bright Data:** crawls clinic and urgent care sites for prices, hours, and walk-in rules. Many of the useful pages block plain requests (an urgent care chain's pricing pages return 500, Kaiser's facility pages loop on redirects, another chain returns 403); Web Unlocker gets them. A clinic's walk-in list is matched to directory sites by name.

## Challenges

- Directory data is messy: 24/7 phone lines attached to clinics made them look "open" at 2 AM, and a geocoder put a Hillcrest clinic in Escondido. We filter all-day spans and reject any address that lands more than 8 km from its ZIP.
- Two of our agents built the same feature in parallel; we added claim-before-start rules and CI.
- Gemini's free tier caps requests per model per day; the client skips exhausted models and spills over to others.

## Accomplishments that we're proud of

- The same text, "think i sprained my ankle", gets three different correct answers for Kaiser, Medi-Cal, and uninsured users, each with a cost and a source link.
- No clinic, price, or hour in a reply comes from the model. Every fact is rendered from the database.
- Emergencies never wait on AI: the red-flag screen answers in under a second.
- It runs end to end on a real iMessage line, plus a local browser chat page that uses the same pipeline as a backup.

## What we learned

The hard part isn't the chatbot. It's knowing, right now, which nearby place takes this person's coverage, is open, can do an X-ray, and what it costs. That is a data problem, and it needs data from many sources.

## What's next

More plans and counties, live verification of every clinic website (not only the Medi-Cal directory), hospital charity-care rules (California requires them up to 400% FPL), voice onboarding, and booking.

## Built with

Python, FastAPI, Sendblue, Gemini API, Neon, PostGIS, Bright Data, FHIR (Da Vinci Plan-Net), Nominatim / OpenStreetMap.

## Prize tracks (one line for each judge)

| Track | Where to look |
|---|---|
| **Bright Data: Data for Good** | `crawler/crawl.py` and `crawler/extract.py`: Web Unlocker fetches clinic pages that block plain requests (pricing, hours, walk-in rules), Gemini extracts the facts, and they update the same rows the Medi-Cal and uninsured answers come from. |
| **Neon: Best Database-Driven / Agent Backend** | `app/care/find_care.py` + `db/schema.sql`: coverage, open-now (`is_open_now()` in SQL), capability, and distance in one PostGIS query; agent state, profiles, and the follow-up queue are in Neon; crawls load into a Neon branch before main. |
| **MLH: Best Use of Gemini API** | `app/llm/gemini.py` and callers: card photo vision, first aid, level of care, place extraction, ranking that returns only database IDs, and structured extraction from crawled pages. |
| **Best Overall / Most Innovative** | Insurance-agnostic navigation over iMessage: the same question gets three different correct answers for Kaiser, Medi-Cal, and uninsured users. |
| **MLH: Best Domain from GoDaddy Registry** | One-page site in `site/` (domain to be registered with the MLH code). |

## Honest status (checked Sun Nov 15, 11:10 AM)

- Kaiser copay: loaded ($50 urgent care, $400 ER, $50 primary care) from Covered California's 2026 Silver 70 design (#14 closed).
- Bright Data crawl (#12): ran live Sunday 11:04 AM through a Web Unlocker zone, loaded into a Neon branch, passed validation, and was swapped into main: 86 places, uninsured coverage up from 5 to 42, corrected Kaiser and AFC hours (PR #45).
- Live verification: CHG directory sites and clinic websites are re-checked at answer time (#15). Kaiser's page often takes longer than the 4 s limit, so Kaiser answers show "checked [crawl time]" instead of "verified".
- Cash prices: none of the urgent care sites we crawl lists a self-pay price, so uninsured answers say "not confirmed, ask when you call" instead of a number.
- The free Sendblue line only reaches verified phones, so judges watch the demo phone (or the local chat page, `uv run python -m app.local`).
