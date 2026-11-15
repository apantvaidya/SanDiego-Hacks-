"""Neon repository helpers for users, profiles, conversations, and follow-up cancellation.

Owned by the router issue (#9). Other modules (onboarding #10, pipeline #17, follow-up #16)
should use these instead of writing their own SQL for these tables.
"""
from __future__ import annotations

from typing import Any
from uuid import UUID

from psycopg.types.json import Jsonb

from app.db import get_conn
from app.models import CoverageType, FplRange, Profile, User

# Demo profiles for `#profile <label>`, so one phone can show three answers.
DEMO_PROFILES: dict[str, dict[str, Any]] = {
    "kaiser": {
        "coverage_type": CoverageType.KAISER.value,
        "insurer": "Kaiser Permanente",
        "plan_id": "kaiser_cc_silver",
        "household_size": 1,
        "income_range_fpl": FplRange.FROM_200_TO_400.value,
    },
    "medi_cal": {
        "coverage_type": CoverageType.MEDI_CAL.value,
        "insurer": "Community Health Group",
        "plan_id": "chg_medi_cal",
        "household_size": 1,
        "income_range_fpl": FplRange.UNDER_138.value,
    },
    "uninsured": {
        "coverage_type": CoverageType.UNINSURED.value,
        "insurer": None,
        "plan_id": None,
        "household_size": 1,
        "income_range_fpl": FplRange.UNDER_138.value,
    },
}

_PROFILE_FIELDS = ("coverage_type", "insurer", "plan_id", "household_size", "income_range_fpl", "language")


def _user(row: dict) -> User:
    return User(
        id=row["id"],
        phone=row["phone"],
        active_profile_id=row["active_profile_id"],
        language=row["language"],
        opted_out=row["opted_out"],
    )


def _profile(row: dict) -> Profile:
    return Profile(
        id=row["id"],
        user_id=row["user_id"],
        label=row["label"],
        coverage_type=CoverageType(row["coverage_type"]),
        insurer=row["insurer"],
        plan_id=row["plan_id"],
        household_size=row["household_size"],
        income_range_fpl=FplRange(row["income_range_fpl"]) if row["income_range_fpl"] else None,
        language=row["language"],
    )


# --- users -------------------------------------------------------------------

def get_or_create_user(phone: str) -> User:
    with get_conn() as conn:
        row = conn.execute(
            """INSERT INTO users (phone) VALUES (%s)
               ON CONFLICT (phone) DO UPDATE SET phone = EXCLUDED.phone
               RETURNING *""",
            (phone,),
        ).fetchone()
        conn.execute(
            "INSERT INTO conversations (user_id) VALUES (%s) ON CONFLICT (user_id) DO NOTHING",
            (row["id"],),
        )
    return _user(row)


def set_opted_out(user_id: UUID, opted_out: bool) -> None:
    with get_conn() as conn:
        conn.execute("UPDATE users SET opted_out = %s WHERE id = %s", (opted_out, user_id))
        if opted_out:
            conn.execute("DELETE FROM followups WHERE user_id = %s AND sent_at IS NULL", (user_id,))


def set_language(user_id: UUID, language: str) -> None:
    """Set the user's language and the language on all their profiles."""
    with get_conn() as conn:
        conn.execute("UPDATE users SET language = %s WHERE id = %s", (language, user_id))
        conn.execute("UPDATE profiles SET language = %s WHERE user_id = %s", (language, user_id))


def delete_user(user_id: UUID) -> None:
    """Remove the user and everything tied to them (cascades)."""
    with get_conn() as conn:
        conn.execute("DELETE FROM users WHERE id = %s", (user_id,))


# --- profiles ----------------------------------------------------------------

def get_active_profile(user: User) -> Profile | None:
    if not user.active_profile_id:
        return None
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM profiles WHERE id = %s", (user.active_profile_id,)).fetchone()
    return _profile(row) if row else None


def list_profiles(user_id: UUID) -> list[Profile]:
    with get_conn() as conn:
        rows = conn.execute("SELECT * FROM profiles WHERE user_id = %s ORDER BY label", (user_id,)).fetchall()
    return [_profile(r) for r in rows]


def upsert_profile(user_id: UUID, label: str, **fields: Any) -> Profile:
    """Create or update the user's profile with this label. Unknown fields are rejected."""
    bad = set(fields) - set(_PROFILE_FIELDS)
    if bad:
        raise ValueError(f"unknown profile fields: {sorted(bad)}")
    cols = ["user_id", "label", *fields]
    vals = [user_id, label, *fields.values()]
    updates = ", ".join(f"{c} = EXCLUDED.{c}" for c in fields) or "label = EXCLUDED.label"
    with get_conn() as conn:
        row = conn.execute(
            f"""INSERT INTO profiles ({", ".join(cols)}) VALUES ({", ".join(["%s"] * len(cols))})
                ON CONFLICT (user_id, label) DO UPDATE SET {updates}
                RETURNING *""",
            vals,
        ).fetchone()
    return _profile(row)


def set_active_profile(user_id: UUID, profile_id: UUID | None) -> None:
    with get_conn() as conn:
        conn.execute("UPDATE users SET active_profile_id = %s WHERE id = %s", (profile_id, user_id))


def ensure_demo_profile(user: User, label: str) -> Profile:
    """Create the demo profile if missing (keeps the user's language) and make it active."""
    fields = {**DEMO_PROFILES[label], "language": user.language}
    profile = upsert_profile(user.id, label, **fields)
    set_active_profile(user.id, profile.id)
    return profile


def delete_profiles(user_id: UUID) -> None:
    with get_conn() as conn:
        conn.execute("UPDATE users SET active_profile_id = NULL WHERE id = %s", (user_id,))
        conn.execute("DELETE FROM profiles WHERE user_id = %s", (user_id,))


# --- conversations -----------------------------------------------------------

def get_conversation(user_id: UUID) -> tuple[str, dict]:
    with get_conn() as conn:
        row = conn.execute("SELECT state, context FROM conversations WHERE user_id = %s", (user_id,)).fetchone()
    if not row:
        return "new", {}
    return row["state"], row["context"] or {}


def set_conversation(user_id: UUID, state: str, context: dict | None = None) -> None:
    """Set state. Pass context to replace it; omit to keep the current context."""
    with get_conn() as conn:
        if context is None:
            conn.execute(
                """INSERT INTO conversations (user_id, state) VALUES (%s, %s)
                   ON CONFLICT (user_id) DO UPDATE SET state = EXCLUDED.state, updated_at = now()""",
                (user_id, state),
            )
        else:
            conn.execute(
                """INSERT INTO conversations (user_id, state, context) VALUES (%s, %s, %s)
                   ON CONFLICT (user_id) DO UPDATE
                   SET state = EXCLUDED.state, context = EXCLUDED.context, updated_at = now()""",
                (user_id, state, Jsonb(context)),
            )
