from fastapi import HTTPException
from models import Profile
import pycountry
from database import AsyncSessionLocal
import json
from sqlalchemy.dialects.postgresql import insert
import re
import uuid

#Helper functions
def error_response(status_code: int, message: str):
     raise HTTPException(
          status_code=status_code,
          detail={
               "status": "error",
               "message": message
          }
     )


def get_age_group(age: int | None) -> str | None:
    if age is None:
        return None
    if age <= 12:
        return "child"
    elif age <= 19:
        return "teenager"
    elif age <= 59:
        return "adult"
    else:
        return "senior"


def profile_to_dict(profile: Profile) -> dict:
    return {
        "id": str(profile.id),
        "name": profile.name,
        "gender": profile.gender,
        "gender_probability": profile.gender_probability,
        "age": profile.age,
        "age_group": profile.age_group,
        "country_id": profile.country_id,
        "country_name": profile.country_name,
        "country_probability": profile.country_probability,
        "created_at": profile.created_at.isoformat() if profile.created_at else None,
    }


def get_country_name(country_id: str) -> str:
    """
    Resolves a 2-letter ISO country code to its full name.
    Example: 'NG' -> 'Nigeria'
    """
    try:
        # country_id should be uppercase for pycountry
        country = pycountry.countries.get(alpha_2=country_id.upper())
        return country.name if country else "Unknown"
    except Exception:
        return "Unknown"
    


async def seed_database():
    async with AsyncSessionLocal() as session:
        with open("seed_profiles.json", "r") as f:
            raw = json.load(f)
            data = raw["profiles"]

            stmt = insert(Profile).values(data)
            stmt = stmt.on_conflict_do_nothing(index_elements=["name"])
            await session.execute(stmt)


        await session.commit()


def parse_query(q: str) -> dict:
    filters = {}
    text = q.lower().strip()
    # Remove punctuation except spaces
    text = re.sub(r"[^\w\s]", "", text)
    tokens = text.split()

    # ── 1. GENDER ────────────────────────────────────────────────
    MALE_WORDS   = {"male", "males", "man", "men", "boy", "boys"}
    FEMALE_WORDS = {"female", "females", "woman", "women", "girl", "girls"}

    has_male   = bool(MALE_WORDS & set(tokens))
    has_female = bool(FEMALE_WORDS & set(tokens))

    if has_male and not has_female:
        filters["gender"] = "male"
    elif has_female and not has_male:
        filters["gender"] = "female"
    # both present → no gender filter

    # ── 2. AGE GROUP + "young" special case ──────────────────────
    AGE_GROUP_WORDS = {
        "child": "child", "children": "child",
        "teenager": "teenager", "teenagers": "teenager", "teen": "teenager", "teens": "teenager",
        "adult": "adult", "adults": "adult",
        "senior": "senior", "seniors": "senior", "elderly": "senior",
    }
    YOUNG_WORDS = {"young", "youth"}

    found_age_group = None
    is_young = bool(YOUNG_WORDS & set(tokens))

    for token in tokens:
        if token in AGE_GROUP_WORDS:
            found_age_group = AGE_GROUP_WORDS[token]
            break

    if found_age_group:
        # "young adults" → age_group wins over young
        filters["age_group"] = found_age_group
    elif is_young:
        # "young" alone → age range 16–24
        filters["min_age"] = 16
        filters["max_age"] = 24

    # ── 3. EXPLICIT AGE via regex ─────────────────────────────────
    patterns = [
        (r"between\s+(\d+)\s+and\s+(\d+)",  "between"),
        (r"(?:above|over|older than)\s+(\d+)",   "min"),
        (r"(?:below|under|younger than)\s+(\d+)", "max"),
    ]

    for pattern, kind in patterns:
        match = re.search(pattern, text)
        if match:
            if kind == "between":
                filters["min_age"] = int(match.group(1))
                filters["max_age"] = int(match.group(2))
            elif kind == "min":
                filters["min_age"] = int(match.group(1))
            elif kind == "max":
                filters["max_age"] = int(match.group(1))
            break  # only apply first age match

    # ── 4. COUNTRY ────────────────────────────────────────────────
    # Strategy A: strip trigger words, try full remaining phrase
    # Strategy B: fallback token-by-token scan
    TRIGGER_WORDS = {"from", "in", "of"}

    country_id = None

    # Strategy A — grab everything after trigger word as candidate phrase
    for trigger in TRIGGER_WORDS:
        pattern = rf"\b{trigger}\b\s+(.+)"
        match = re.search(pattern, text)
        if match:
            candidate = match.group(1).strip()
            # remove known non-country trailing words
            candidate = re.sub(r"\b(above|below|over|under|older|younger|than|\d+)\b", "", candidate).strip()
            try:
                country = pycountry.countries.lookup(candidate)
                country_id = country.alpha_2
                break
            except LookupError:
                pass

    # Strategy B — fallback: scan every token
    if not country_id:
        for token in tokens:
            if token in TRIGGER_WORDS:
                continue
            try:
                country = pycountry.countries.lookup(token)
                country_id = country.alpha_2
                break
            except LookupError:
                pass

    if country_id:
        filters["country_id"] = country_id

    return filters


def is_valid_uuid(value: str) -> bool:
    try:
        uuid.UUID(str(value))
        return True
    except ValueError:
        return False