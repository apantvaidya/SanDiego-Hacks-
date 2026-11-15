"""Cost estimate for this user at this location. Pure logic plus plan lookup."""
from app.models import CareLevel, CostEstimate, Location, Profile


def estimate_cost(location: Location, profile: Profile, level: CareLevel) -> CostEstimate:
    raise NotImplementedError
