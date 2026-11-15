# CLAUDE.md: Care Navigator (Bright Data "Data for Good" hackathon)

## What we are building
A text-message healthcare assistant that works for anyone, with any insurance or none.

One-liner: "Text one number with any medical question. It knows your coverage and location, so it tells you what to do, where to go, and what it will cost."

Flow:
1. **Web onboarding** (about 2 minutes) builds a coverage profile.
2. **Text assistant** (SMS or iMessage) the user can message at any time.
3. For each message: safety screen, immediate steps, level of care, then 2 or 3 nearby options ranked by coverage, distance, open now, and estimated cost.
4. Follow-up text the next day.
5. Appointment booking comes later. It is out of scope for the hackathon.

## Product decisions (locked)
- **Insurance-agnostic.** Serve everyone: commercial plans, Kaiser, Medi-Cal, Covered California, and uninsured.
- **The core value is convenience and context**, not directory accuracy. Users rarely have their plan details on hand when they need care. We hold that context for them.
- **The Data for Good angle:** uninsured and Medi-Cal users get first-class answers (sliding-scale clinics, charity care, cash prices). Insurer-run bots do not serve them.
- **Directory accuracy is not the pitch**, but run a quiet live check on a place before recommending it, so we don't send someone to a clinic that rejects their plan.

## Competitive context
- **Insurer and employer bots** (UnitedHealthcare Avery, Included Health Dot, Transcarent WayFinding, Pager Health, Buoy) only work for one payer's members.
- **ChatGPT and Claude** give general advice but have no plan, deductible or local network context.
- **Our edge:** any plan or none, plus a specific local answer that includes cost.
- **The question judges will ask:** "Why not ChatGPT or my insurer's app?" Answer with the edge above.

## Onboarding: coverage profile
Collect the minimum:
- Photo of the insurance card. Extract the insurer, plan name and member ID. If there is no card, offer "uninsured / Medi-Cal / other".
- ZIP code
- Household size and an **income range** (not an exact number). This unlocks sliding-scale fees and charity care.
- Preferred language
- Transportation: car, transit, or none

Then look up the plan's benefits and network.

### Draft schema (to refine)
```json
{
  "user_id": "uuid",
  "phone": "+1...",
  "coverage": {
    "type": "commercial | kaiser | medi_cal | covered_ca | medicare | uninsured",
    "insurer": "string | null",
    "plan_name": "string | null",
    "member_id": "string | null",
    "benefits": {
      "urgent_care_copay": "number | null",
      "er_copay": "number | null",
      "pcp_copay": "number | null",
      "telehealth": "free | copay | none | unknown",
      "nurse_line": "string | null",
      "deductible": "number | null"
    }
  },
  "zip": "92xxx",
  "household_size": 1,
  "income_range_fpl": "<138 | 138-200 | 200-400 | >400",
  "language": "en | es | ...",
  "transport": "car | transit | none"
}
```

## Assistant behavior
- **Safety comes first, in code, not the LLM.** Hard-coded red flags (chest pain, trouble breathing, stroke signs, severe bleeding, suicidal thoughts, and so on) return "Call 911 / go to the ER now" before anything else. For suicidal thoughts, also give 988.
- **Never diagnose.** Give general first-aid steps only (for example, rest, ice and elevation for a possible sprain).
- **Choose a level of care:** self-care, telehealth, urgent care, primary care, or ER. When in doubt, go one level up.
- **Recommend 2 or 3 options**, each with distance, hours, estimated cost for this user, and a source link with a "verified X min ago" time.
- **Keep texts short** and match the user's language.

## Data sources
| Data | Source | Method |
|---|---|---|
| Providers and clinics | NPPES NPI Registry | Free API |
| Community health centers (FQHCs) | HRSA health center data | Download |
| Plan networks | Insurer directory pages; Transparency in Coverage in-network files | Bright Data scrape / bulk files |
| Plan benefits | Plan benefit summaries, Covered California plan documents | Scrape plus LLM extraction |
| Hours, walk-in rules, "accepting new patients" | Clinic and urgent care websites | Bright Data scrape (live check) |
| Cash prices, live wait times | Urgent care chain websites | Bright Data scrape |
| Sliding fee schedules | Clinic websites (often PDFs) | Scrape plus extraction |
| Charity care rules | HCAI Hospital Fair Pricing Policy Lookup (hcai.ca.gov) | Scrape |
| Distance and transit time | Maps API | API |

**Pitch line for Bright Data:** "Insurance-agnostic navigation needs data from hundreds of insurers and thousands of clinics. No single API covers it. Bright Data is how we get it."

## Proposed stack
- **Texting:** Twilio SMS (or Sendblue for iMessage)
- **Agent:** LangGraph, with these tools:
  - `triage_screen` (rules-based red flags)
  - `find_care` (PostGIS query filtered by coverage, distance and open now)
  - `verify_listing` (live Bright Data scrape of the clinic page)
  - `estimate_cost` (plan benefits plus cash prices and sliding scale)
- **Database:** Postgres with PostGIS
- **Onboarding:** Next.js
- **Crawling:** nightly crawl plus on-demand verification

## Hackathon scope
- **Geography:** San Diego only.
- **Coverage types:** Medi-Cal, uninsured, Kaiser, plus one commercial plan if time allows.
- **Scenarios:** sprained ankle, routine blood test, uninsured user who needs cheap care.
- **Skip:** booking, full accounts. Add Spanish only if it is cheap.

### Demo (60 seconds)
Three phones text the same message: "think i sprained my ankle."
- **Kaiser member:** Kaiser advice line plus the nearest Kaiser urgent care and the copay.
- **Medi-Cal member:** the nearest in-network community health center, open now, $0.
- **Uninsured member:** a sliding-scale clinic with the estimated cost at their income, plus why to avoid the ER unless it gets worse.

Same question, three different correct answers.

## Risks
- **Medical safety:** keep triage conservative and never diagnose.
- **Privacy:** HIPAA may not apply to a consumer app, but health app privacy rules still do. Collect the minimum and keep few logs.
- **Wrong recommendations:** show the source link and the verification time on every option.

## Background research (for the pitch)
- Up to 2M Californians may lose Medi-Cal under H.R. 1 (CPEHN).
- 4 in 10 Californians have medical debt (CHCF 2026 survey).
- California hospitals must offer discounted care up to 400% FPL (AB 1020), and policies vary by hospital.
- Insurer directories are often wrong. In a Senate Finance secret-shopper study, people could book only 18% of the time.

## Style for this repo
- Plain, direct prose in docs and UI copy. No em dashes.
- Make honest, defensible claims. No inflated metrics.
- Default language: Python (agent and data). TypeScript for the onboarding front end.
