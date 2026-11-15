"""Live Bright Data re-check of the #1 option. Writes a row to verifications."""
from app.models import Location, Verification


def verify_live(location: Location, timeout_s: float = 6.0) -> Verification:
    raise NotImplementedError
