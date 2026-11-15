# Demo script

Judging: **Sunday Nov 15, 12:30 to 2:30 PM**, Qualcomm Room, science-fair style. 3 minutes to present, 2 minutes of questions. **Every teammate at the table.** Judged on idea, demo, technical complexity, completion.

The demo runs **live on one phone** at real time. That matters on Sunday afternoon:

- FHCSD's walk-in clinics (the $25 sliding fee) are **closed Sundays**, so the uninsured answer will be cash urgent care, not FHCSD. That is the correct answer and a good moment: "it knows what's open right now." Mention the $25 option as what the same person gets on a weekday.
- What judges will see at 1 PM Sunday near UCSD (rendered from the live database Saturday night):

| Profile | #1 option | Cost shown |
|---|---|---|
| Kaiser | Kaiser San Diego Medical Center Urgent Care, 7.0 mi, open until 9 PM, X-ray | "your plan's copay (amount not confirmed)" until #14 lands |
| Medi-Cal (CHG) | Marque Urgent Care UTC, 2.4 mi, open until 6 PM | $0 with Medi-Cal |
| Uninsured | AFC Urgent Care Clairemont / Mira Mesa, about 5 mi, open until 8 PM, X-ray | $199 self-pay at Mira Mesa (visit only, X-ray extra); Clairemont "not confirmed" |

## Before judging (checklist)

- [ ] Server running (`uv run uvicorn app.main:app --port 8000`), tunnel up, webhook registered to the current tunnel URL (`uv run python -m scripts.sendblue_setup webhook <url>`)
- [ ] Gemini has credits or billing (free tier is 20 requests per day per model; see PR #34)
- [ ] `.env`: `GEMINI_MODEL=gemini-3.5-flash-lite`
- [ ] Demo phone texted the line once today; `#reset` then `#profile kaiser` works
- [ ] Phone on Do Not Disturb, screen mirroring or a big font
- [ ] Backup: screen recording of a full run, in case Wi-Fi or Gemini fails at the table

## Live demo (60 seconds)

1. "Most people don't know their copay, their network, or what a visit costs when they need care. We hold that for them. You just text."
2. `#profile kaiser`, then text: **think i sprained my ankle**
   - Text 1 arrives in about 3 s: first aid (rest, ice, compression, elevation), when to go to the ER, "Where are you right now?"
   - Reply **UCSD**. Text 2: Kaiser urgent care, open until 9, X-ray on site, Kaiser advice line.
3. `#profile medi_cal`, same two texts. "Same question. Now it's Marque Urgent Care, 2.4 miles, $0, because it's in Community Health Group's Medi-Cal network. That network comes from CHG's own public provider directory API."
4. `#profile uninsured`, same two texts. "No insurance: AFC with X-ray and its listed self-pay price, open now. On a weekday this person also gets Family Health Centers' $25 walk-in visit, which it skips today because it's closed Sundays."
5. Safety: text **my chest hurts and my arm is numb**. Instant fixed 911 reply, no AI involved. "Red flags are caught in code before the model sees anything."
6. `#followup`: "How's the ankle? 1 better, 2 same, 3 worse." "It checks back the next day."

"Same question, three different correct answers, and it never makes up a clinic."

## 3-minute video (record Sunday morning, before the 11 AM initial submission)

1. 0:00 to 0:20: the problem (one line each): insurer bots only serve their members, ChatGPT doesn't know your plan or what's open nearby, and 4 in 10 Californians have medical debt.
2. 0:20 to 1:40: the live demo above (three profiles, safety, follow-up).
3. 1:40 to 2:30: how it works, one sentence each: Sendblue iMessage, safety screen in code, Gemini for first aid, level of care and ranking (it only picks from database IDs; code writes every fact), Neon + PostGIS candidate query with open-now in SQL, CHG FHIR directory, Bright Data crawl.
4. 2:30 to 3:00: Data for Good: uninsured and Medi-Cal users get first-class answers, and Medi-Cal coverage losses are coming.

## Questions judges will ask

**"Why not ChatGPT or my insurer's app?"**
Insurer bots (UnitedHealthcare, Included Health, and others) only serve their own members. ChatGPT gives general advice, but it doesn't know your plan, your network, or which clinic is open near you right now. We work for any plan or none, and answer with a specific nearby place and what it costs you.

**"What if the AI misses an emergency?"**
Red flags (chest pain, stroke signs, trouble breathing, severe bleeding, suicidal thoughts, overdose, and more, in English and Spanish) are matched in code before any model call and get a fixed 911 / 988 reply. Gemini's prompt is a second net, not the first.

**"What if it sends someone to the wrong place?"**
Gemini never writes a clinic name, price, or hours. It ranks IDs from our database; code renders every fact with its source link and when it was checked. If nothing verified is open, it says so and gives 211 instead of guessing.

**"Is the data accurate?"**
Medi-Cal network: from Community Health Group's own FHIR provider directory (required of Medi-Cal plans by CMS). Prices and hours: scraped from clinic sites with Bright Data, with the source on every option. Directories are often wrong, which is why every answer shows its source and age.

**"Privacy?"**
We don't store the member ID or the card photo. We keep coverage type, plan, ZIP-level location when asked, household size, and an income range, not an exact income. STOP works everywhere.

**"Can strangers try it?"**
Not yet: the free Sendblue line only reaches verified contacts. That's a plan limit, not a design one.

## Pitch numbers (use this wording)

- About **2 million** Medi-Cal members are at risk of losing coverage under H.R. 1, **per the California Department of Health Care Services** (not CPEHN, whose estimate is higher).
- **4 in 10** Californians have medical debt (CHCF 2026 California Health Policy Survey).
- California hospitals must offer charity care or discounts at or below **400% FPL** (AB 1020).
- Directory accuracy: in a 2023 Senate Finance Committee secret-shopper study of **mental health providers listed by Medicare Advantage plans**, callers got an appointment only 18% of the time. Don't say "people" generally.
- FHCSD: "a reduced fee of **$25 per visit**" for patients who qualify for the sliding fee and don't qualify for insurance (FHCSD billing page, checked Nov 15). Walk-in urgent care 8 AM to 8 PM, **except Sundays**.
- Don't quote cash urgent care prices as facts on stage. Say "the price the clinic lists, with the link."
