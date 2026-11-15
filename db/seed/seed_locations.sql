-- Hand seeds so the pipeline runs before the crawl (#12) and CHG import (#11) land. Owned by #2.
-- Re-runnable: every insert upserts on the stable id.
--
-- Sources were checked by hand on 2026-11-15 around 00:30 PT; crawled_at records that time.
-- Rules followed here:
--   * Values come from the location's own site or CHG's public FHIR directory. Where a value
--     could not be confirmed it is NULL, never guessed. #12 and #11 replace these rows.
--   * Coordinates: Nominatim (OpenStreetMap) lookup of the street address.
--   * Hours: San Diego local time, [open, close), see is_open_now() in db/schema.sql.
--   * accepts holds plan ids and coverage types. Medi-Cal network depends on the plan, so
--     Medi-Cal sites list the plan id ('chg_medi_cal'), not the bare 'medi_cal' type.
--     ERs list every coverage type: emergency care is covered in or out of network, and
--     federal law (EMTALA) requires an ER to screen and stabilize anyone.

-- ---------------------------------------------------------------- plans
-- PLACEHOLDERS until #14 extracts the real benefit summaries. source_url and extracted_at
-- stay NULL so nothing can present these as extracted values.
INSERT INTO plans (id, insurer, plan_name, coverage_type, urgent_care_copay, er_copay, pcp_copay,
                   telehealth, telehealth_copay, nurse_line, deductible, source_url, extracted_at)
VALUES
  -- Copays unknown until #14. Nurse line: Kaiser Southern California advice nurse number listed on
  -- kp.org facility pages (healthy.kaiserpermanente.org, Zion Medical Center, Advice Nurse).
  ('kaiser_cc_silver', 'Kaiser Permanente', 'Covered California Silver (Kaiser Permanente HMO)', 'kaiser',
   NULL, NULL, NULL, 'unknown', NULL, '1-833-574-2273', NULL, NULL, NULL),
  -- Medi-Cal: $0 for covered visits (placeholder, confirm in #14). Nurse line unknown until #14.
  ('chg_medi_cal', 'Community Health Group', 'Medi-Cal', 'medi_cal',
   0, 0, 0, 'unknown', NULL, NULL, NULL, NULL, NULL)
ON CONFLICT (id) DO UPDATE SET
  insurer = EXCLUDED.insurer, plan_name = EXCLUDED.plan_name, coverage_type = EXCLUDED.coverage_type,
  urgent_care_copay = EXCLUDED.urgent_care_copay, er_copay = EXCLUDED.er_copay,
  pcp_copay = EXCLUDED.pcp_copay, telehealth = EXCLUDED.telehealth,
  telehealth_copay = EXCLUDED.telehealth_copay, nurse_line = EXCLUDED.nurse_line,
  deductible = EXCLUDED.deductible, source_url = EXCLUDED.source_url, extracted_at = EXCLUDED.extracted_at;

-- ---------------------------------------------------------------- locations
INSERT INTO locations (id, name, kind, geom, address, phone, hours, has_xray, walk_in, accepts,
                       self_pay_price, sliding_scale, charity_care, source_url, crawled_at)
VALUES
  -- Kaiser urgent care, members only. Kaiser's page: walk-ins accepted, 7 days a week including
  -- holidays. 9am to 9pm is from Kaiser's San Diego urgent care flyer. Hospital campus with
  -- diagnostic imaging, so has_xray = true.
  ('kaiser_sdmc_urgent', 'Kaiser Permanente San Diego Medical Center Urgent Care', 'kaiser',
   ST_SetSRID(ST_MakePoint(-117.1248701, 32.83013), 4326)::geography,
   '9455 Clairemont Mesa Blvd, San Diego, CA 92123', '1-833-574-2273',
   '{"mon":[["09:00","21:00"]],"tue":[["09:00","21:00"]],"wed":[["09:00","21:00"]],"thu":[["09:00","21:00"]],"fri":[["09:00","21:00"]],"sat":[["09:00","21:00"]],"sun":[["09:00","21:00"]]}',
   true, true, '["kaiser"]', NULL, NULL, NULL,
   'https://healthy.kaiserpermanente.org/southern-california/facilities/kaiser-permanente-san-diego-medical-center-331581/departments/urgent-care-dlp-173705',
   '2026-11-15 00:30:00-08'),

  -- Kaiser's page: walk-in urgent care 7 days a week including holidays, 9am to 9pm.
  -- Has a Radiology / Diagnostic Imaging department on site.
  ('kaiser_otay_mesa_urgent', 'Kaiser Permanente Otay Mesa Medical Offices Urgent Care', 'kaiser',
   ST_SetSRID(ST_MakePoint(-117.0335212, 32.5850525), 4326)::geography,
   '4650 Palm Ave, San Diego, CA 92154', '1-833-574-2273',
   '{"mon":[["09:00","21:00"]],"tue":[["09:00","21:00"]],"wed":[["09:00","21:00"]],"thu":[["09:00","21:00"]],"fri":[["09:00","21:00"]],"sat":[["09:00","21:00"]],"sun":[["09:00","21:00"]]}',
   true, true, '["kaiser"]', NULL, NULL, NULL,
   'https://healthy.kaiserpermanente.org/southern-california/facilities/otay-mesa-medical-offices-100079',
   '2026-11-15 00:30:00-08'),

  -- FHCSD urgent care site, also in CHG's network (CHG FHIR Location CHG4305899).
  -- fhcsd.org/urgent: 8am to 8pm daily except Sundays; accepts cash, Medi-Cal, Medicare and most
  -- insurance; sliding-fee scale for uninsured. X-ray is "on-site or off-site depending on
  -- availability", so has_xray is unknown. Sliding-fee dollar amounts are unconfirmed (HANDOFF
  -- section 8), so sliding_scale stays NULL until confirmed (#18) or crawled (#12).
  ('fhcsd_logan_heights', 'Logan Heights Family Health Center (Family Health Centers of San Diego)', 'fqhc',
   ST_SetSRID(ST_MakePoint(-117.1465692, 32.7015979), 4326)::geography,
   '1809 National Ave, San Diego, CA 92113', '(619) 515-2300',
   '{"mon":[["08:00","20:00"]],"tue":[["08:00","20:00"]],"wed":[["08:00","20:00"]],"thu":[["08:00","20:00"]],"fri":[["08:00","20:00"]],"sat":[["08:00","20:00"]]}',
   NULL, true, '["chg_medi_cal", "uninsured", "medicare", "commercial"]', NULL, NULL, NULL,
   'https://www.fhcsd.org/urgent',
   '2026-11-15 00:30:00-08'),

  -- CHG in-network urgent care (CHG FHIR Location CHG4112376). Hours as listed in CHG's directory:
  -- 9am to 2pm, 7 days. X-ray, walk-in policy and self-pay are not in the directory.
  ('urgent_care_and_more_midway', 'Urgent Care and More', 'urgent_care',
   ST_SetSRID(ST_MakePoint(-117.2123834, 32.7510145), 4326)::geography,
   '3434 Midway Dr Ste 1002, San Diego, CA 92110', '(619) 225-6200',
   '{"mon":[["09:00","14:00"]],"tue":[["09:00","14:00"]],"wed":[["09:00","14:00"]],"thu":[["09:00","14:00"]],"fri":[["09:00","14:00"]],"sat":[["09:00","14:00"]],"sun":[["09:00","14:00"]]}',
   NULL, NULL, '["chg_medi_cal"]', NULL, NULL, NULL,
   'https://api-chgsd-prd.safhir.io/v1/api/provider-directory/Location/CHG4112376',
   '2026-11-15 00:30:00-08'),

  -- Cash urgent care. AFC's site: 8am to 8pm, 7 days, on-site digital X-ray, walk-ins welcome.
  -- Self-pay $199 visit is a third-party listing (Solv), not confirmed with AFC (HANDOFF section 8);
  -- X-ray is extra ($139 to $169 per the same listing). Confirm before saying it on stage.
  ('afc_mira_mesa', 'AFC Urgent Care Mira Mesa', 'urgent_care',
   ST_SetSRID(ST_MakePoint(-117.1458344, 32.9143312), 4326)::geography,
   '8260 Mira Mesa Blvd Suite A, San Diego, CA 92126', '(858) 900-3550',
   '{"mon":[["08:00","20:00"]],"tue":[["08:00","20:00"]],"wed":[["08:00","20:00"]],"thu":[["08:00","20:00"]],"fri":[["08:00","20:00"]],"sat":[["08:00","20:00"]],"sun":[["08:00","20:00"]]}',
   true, true, '["uninsured"]', 199, NULL, NULL,
   'https://www.afcurgentcare.com/mira-mesa/',
   '2026-11-15 00:30:00-08'),

  -- Cash urgent care. AFC's X-ray page: 8am to 8pm, 7 days, on-site digital X-ray, walk in anytime.
  -- (A different AFC page lists 8am to 5pm on weekends; #12 should settle it.) Visit price unknown.
  ('afc_clairemont', 'AFC Urgent Care Clairemont', 'urgent_care',
   ST_SetSRID(ST_MakePoint(-117.1787468, 32.8191927), 4326)::geography,
   '5671 Balboa Ave, San Diego, CA 92111', '(858) 800-2880',
   '{"mon":[["08:00","20:00"]],"tue":[["08:00","20:00"]],"wed":[["08:00","20:00"]],"thu":[["08:00","20:00"]],"fri":[["08:00","20:00"]],"sat":[["08:00","20:00"]],"sun":[["08:00","20:00"]]}',
   true, true, '["uninsured"]', NULL, NULL, NULL,
   'https://www.afcurgentcare.com/clairemont/patient-services/x-ray',
   '2026-11-15 00:30:00-08'),

  -- ER, open 24 hours. Gary and Mary West Emergency Department, Jacobs Medical Center.
  -- Address per UC San Diego Health and HCAI. ED phone per UCSD student health.
  ('ucsd_jacobs_er', 'UC San Diego Health Jacobs Medical Center Emergency Department', 'er',
   ST_SetSRID(ST_MakePoint(-117.2263271, 32.8778127), 4326)::geography,
   '9300 Campus Point Dr, La Jolla, CA 92037', '(858) 657-7600',
   '{"mon":[["00:00","24:00"]],"tue":[["00:00","24:00"]],"wed":[["00:00","24:00"]],"thu":[["00:00","24:00"]],"fri":[["00:00","24:00"]],"sat":[["00:00","24:00"]],"sun":[["00:00","24:00"]]}',
   true, true, '["uninsured", "kaiser", "medi_cal", "chg_medi_cal", "commercial", "covered_ca", "medicare"]',
   NULL, NULL, NULL,
   'https://healthlocations.ucsd.edu/san-diego/9300-campus-point-drive',
   '2026-11-15 00:30:00-08'),

  -- ER, open 24 hours. Kaiser's page: Emergency available (urgent care is NOT offered here).
  ('kaiser_zion_er', 'Kaiser Permanente Zion Medical Center Emergency', 'er',
   ST_SetSRID(ST_MakePoint(-117.094983, 32.7914122), 4326)::geography,
   '4647 Zion Ave, San Diego, CA 92120', NULL,
   '{"mon":[["00:00","24:00"]],"tue":[["00:00","24:00"]],"wed":[["00:00","24:00"]],"thu":[["00:00","24:00"]],"fri":[["00:00","24:00"]],"sat":[["00:00","24:00"]],"sun":[["00:00","24:00"]]}',
   true, true, '["uninsured", "kaiser", "medi_cal", "chg_medi_cal", "commercial", "covered_ca", "medicare"]',
   NULL, NULL, NULL,
   'https://healthy.kaiserpermanente.org/southern-california/facilities/kaiser-permanente-zion-medical-center-100084',
   '2026-11-15 00:30:00-08')
ON CONFLICT (id) DO UPDATE SET
  name = EXCLUDED.name, kind = EXCLUDED.kind, geom = EXCLUDED.geom, address = EXCLUDED.address,
  phone = EXCLUDED.phone, hours = EXCLUDED.hours, has_xray = EXCLUDED.has_xray,
  walk_in = EXCLUDED.walk_in, accepts = EXCLUDED.accepts, self_pay_price = EXCLUDED.self_pay_price,
  sliding_scale = EXCLUDED.sliding_scale, charity_care = EXCLUDED.charity_care,
  source_url = EXCLUDED.source_url, crawled_at = EXCLUDED.crawled_at;
