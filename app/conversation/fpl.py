"""Income brackets by household size from current HHS poverty guidelines (look them up, do not guess)."""
from app.models import IncomeBracket


def income_brackets(household_size: int) -> list[IncomeBracket]:
    raise NotImplementedError
