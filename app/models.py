"""Shared data types. This is a contract between modules: see docs/interfaces.md."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from uuid import UUID


class CoverageType(str, Enum):
    COMMERCIAL = "commercial"
    KAISER = "kaiser"
    MEDI_CAL = "medi_cal"
    COVERED_CA = "covered_ca"
    MEDICARE = "medicare"
    UNINSURED = "uninsured"


class CareLevel(str, Enum):
    """Ordered from lowest to highest. Use LEVEL_ORDER to bump up."""
    SELF_CARE = "self_care"
    TELEHEALTH = "telehealth"
    PRIMARY_CARE = "primary_care"
    URGENT_CARE = "urgent_care"
    ER = "er"


LEVEL_ORDER = [
    CareLevel.SELF_CARE,
    CareLevel.TELEHEALTH,
    CareLevel.PRIMARY_CARE,
    CareLevel.URGENT_CARE,
    CareLevel.ER,
]


class FplRange(str, Enum):
    UNDER_138 = "<138"
    FROM_138_TO_200 = "138-200"
    FROM_200_TO_400 = "200-400"
    OVER_400 = ">400"


@dataclass
class InboundMessage:
    phone: str
    text: str
    media_urls: list[str] = field(default_factory=list)
    received_at: datetime | None = None
    message_id: str | None = None


@dataclass
class Profile:
    id: UUID
    user_id: UUID
    label: str  # 'kaiser' | 'medi_cal' | 'uninsured' | custom
    coverage_type: CoverageType
    insurer: str | None = None
    plan_id: str | None = None  # stable key, e.g. 'chg_medi_cal'
    household_size: int | None = None
    income_range_fpl: FplRange | None = None
    language: str = "en"  # 'en' | 'es'


@dataclass
class User:
    id: UUID
    phone: str
    active_profile_id: UUID | None
    language: str = "en"
    opted_out: bool = False


@dataclass
class SafetyResult:
    matched: bool
    category: str | None = None  # 'cardiac' | 'breathing' | 'stroke' | 'bleeding' | 'self_harm' | ...
    reply: str | None = None  # fixed text, never LLM generated


@dataclass
class TriageResult:
    red_flag: bool
    reply: str  # first-aid text ending with the location question, or the fixed emergency text


@dataclass
class GeoPoint:
    lat: float
    lon: float
    label: str


@dataclass
class Location:
    id: str  # stable key, e.g. 'fhcsd_city_heights'
    name: str
    kind: str  # urgent_care | fqhc | kaiser | er | lab | telehealth
    lat: float
    lon: float
    address: str
    phone: str | None
    hours: dict  # {"mon": [["08:00","20:00"]], ...}
    has_xray: bool | None
    walk_in: bool | None
    accepts: list[str]  # plan ids and/or coverage types, e.g. ['chg_medi_cal', 'uninsured']
    self_pay_price: float | None
    sliding_scale: dict | None  # {"<138": [0, 30], "138-200": [30, 60], ...}
    source_url: str
    crawled_at: datetime
    distance_miles: float | None = None


@dataclass
class RankedOption:
    location_id: str
    reason: str  # short, from Gemini, no facts like prices or hours


@dataclass
class CostEstimate:
    low: float | None
    high: float | None
    basis: str  # 'plan_copay' | 'self_pay' | 'sliding_scale' | 'medi_cal' | 'unknown'
    note: str | None = None  # e.g. "estimated from your plan's summary"


@dataclass
class Verification:
    location_id: str
    checked_at: datetime
    open_now: bool | None
    raw: dict = field(default_factory=dict)


@dataclass
class RenderedOption:
    location: Location
    reason: str
    cost: CostEstimate
    verification: Verification | None  # only for the #1 option


@dataclass
class CardExtraction:
    insurer: str | None
    plan_name: str | None
    coverage_type: CoverageType | None
    confidence: float


@dataclass
class IncomeBracket:
    label: str  # e.g. "Under $22,000"
    fpl_range: FplRange
