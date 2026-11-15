"""CHG Medi-Cal network from its public FHIR provider directory API (no login).
Metadata: https://api-chgsd-prd.safhir.io/v1/api/provider-directory/metadata
Likely Da Vinci Plan-Net resources: Location, Organization, HealthcareService, InsurancePlan.
Pull San Diego locations, then add 'chg_medi_cal' to locations.accepts on matching rows. Cache in Neon."""


def fetch_chg_locations() -> list[dict]:
    raise NotImplementedError
