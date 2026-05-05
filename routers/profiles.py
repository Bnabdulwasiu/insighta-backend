import csv
import io
import json
import math
from datetime import datetime as dt
from typing import Optional
import asyncio

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from fastapi.responses import JSONResponse, StreamingResponse
from sqlalchemy import select, func
from sqlalchemy.exc import IntegrityError

from auth import get_current_user, require_admin
from core.limiter import limiter
from core.cache import get_query_cache, set_query_cache, invalidate_query_cache
from database import AsyncSessionLocal
from models import Profile, User
from schemas import CreateProfileRequest, ProfileListResponse, ProfileSchema
from utils import (
    build_url, get_age_group, get_country_name,
    is_valid_uuid, parse_query, profile_to_dict
)

router = APIRouter(prefix="/api", tags=["profiles"])

@router.get("/users/me")
@limiter.limit("60/minute")
async def get_users_me(
    request: Request,
    current_user: User = Depends(get_current_user)
):
    return {
        "status": "success",
        "data": {
            "id": str(current_user.id),
            "username": current_user.username,
            "email": current_user.email,
            "role": current_user.role,
            "avatar_url": current_user.avatar_url,
            "last_login_at": current_user.last_login_at.isoformat() if current_user.last_login_at else None,
        }
    }


@router.post("/profiles", response_model=ProfileSchema, status_code=201)
@limiter.limit("60/minute")
async def create_profile(
    request: Request,
    body: CreateProfileRequest,
    current_user: User = Depends(require_admin),
):
    name = body.name.strip().lower()
    if not name:
        raise HTTPException(status_code=400, detail={
            "status": "error",
            "message": "Missing or empty name"
        })

    async with AsyncSessionLocal() as session:
        try:
            async with httpx.AsyncClient() as client:
                gender_res, age_res, nation_res = await asyncio.gather(
                    client.get("https://api.genderize.io/", params={"name": name}),
                    client.get("https://api.agify.io/", params={"name": name}),
                    client.get("https://api.nationalize.io/", params={"name": name}),
                )
        except (httpx.HTTPStatusError, httpx.RequestError):
            raise HTTPException(status_code=502, detail={
                "status": "error",
                "message": "Upstream or server failure"
            })

        gender_data = gender_res.json()
        age_data = age_res.json()
        nation_data = nation_res.json()

        if gender_data.get("gender") is None or gender_data.get("count") == 0:
            raise HTTPException(status_code=502, detail={
                "status": "error",
                "message": "Genderize returned an invalid response"
            })
        if age_data.get("age") is None:
            raise HTTPException(status_code=502, detail={
                "status": "error",
                "message": "Agify returned an invalid response"
            })
        countries = nation_data.get("country", [])
        if not countries:
            raise HTTPException(status_code=502, detail={
                "status": "error",
                "message": "Nationalize returned an invalid response"
            })

        top_country = max(countries, key=lambda c: c["probability"])
        country_id = top_country["country_id"]
        full_country_name = get_country_name(country_id)

        profile = Profile(
            name=name,
            gender=gender_data.get("gender"),
            gender_probability=gender_data.get("probability"),
            age=age_data.get("age"),
            age_group=get_age_group(age_data.get("age")),
            country_id=country_id,
            country_name=full_country_name,
            country_probability=top_country["probability"],
        )
        session.add(profile)

        try:
            await session.commit()
            await session.refresh(profile)
            # A new profile was written — invalidate all cached query results
            # so the next reader gets fresh data.
            invalidate_query_cache()
            return JSONResponse(status_code=201, content={
                "status": "success",
                "data": profile_to_dict(profile)
            })
        except IntegrityError:
            await session.rollback()
            result = await session.execute(
                select(Profile).where(Profile.name == name)
            )
            existing = result.scalar_one()
            return JSONResponse(status_code=200, content={
                "status": "success",
                "message": "Profile already exists",
                "data": profile_to_dict(existing)
            })


@router.get("/profiles/export")
@limiter.limit("60/minute")
async def export_profiles(
    request: Request,
    format: str = Query(default="csv"),
    gender: Optional[str] = None,
    country_id: Optional[str] = None,
    age_group: Optional[str] = None,
    min_age: Optional[int] = None,
    max_age: Optional[int] = None,
    sort_by: Optional[str] = None,
    order: str = Query(default="asc", pattern="^(asc|desc)$"),
    current_user: User = Depends(get_current_user),
):
    if format != "csv":
        raise HTTPException(status_code=400, detail={
            "status": "error",
            "message": "Only format=csv is supported"
        })

    SORTABLE_FIELDS = {
        "age": Profile.age,
        "created_at": Profile.created_at,
        "gender_probability": Profile.gender_probability,
    }

    async with AsyncSessionLocal() as session:
        query = select(Profile)
        if gender:
            query = query.where(Profile.gender == gender.lower())
        if country_id:
            query = query.where(Profile.country_id == country_id.upper())
        if age_group:
            query = query.where(Profile.age_group == age_group.lower())
        if min_age is not None:
            query = query.where(Profile.age >= min_age)
        if max_age is not None:
            query = query.where(Profile.age <= max_age)
        if sort_by and sort_by in SORTABLE_FIELDS:
            col = SORTABLE_FIELDS[sort_by]
            query = query.order_by(col.desc() if order == "desc" else col.asc())

        result = await session.execute(query)
        profiles = result.scalars().all()

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow([
        "id", "name", "gender", "gender_probability",
        "age", "age_group", "country_id", "country_name",
        "country_probability", "created_at"
    ])
    for p in profiles:
        writer.writerow([
            str(p.id), p.name, p.gender, p.gender_probability,
            p.age, p.age_group, p.country_id, p.country_name,
            p.country_probability,
            p.created_at.isoformat() if p.created_at else ""
        ])

    output.seek(0)
    timestamp = dt.now().strftime("%Y%m%d_%H%M%S")
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename=profiles_{timestamp}.csv"}
    )


@router.get("/profiles/parse")
async def parse_profile_query(
    request: Request,
    q: str = Query(..., min_length=1),
    current_user: User = Depends(get_current_user),
):
    filters = parse_query(q)
    if not filters:
        raise HTTPException(status_code=400, detail={
            "status": "error",
            "message": "Unable to interpret query"
        })
    return {"q": q, "parsed_filters": filters}


@router.get("/profiles/search")
@limiter.limit("60/minute")
async def search_profiles(
    request: Request,
    q: str = Query(..., min_length=1),
    page: int = Query(default=1, ge=1),
    limit: int = Query(default=10, ge=1, le=50),
    current_user: User = Depends(get_current_user),
):
    filters = parse_query(q)
    if not filters:
        raise HTTPException(status_code=400, detail={
            "status": "error",
            "message": "Unable to interpret query"
        })

    # ── Cache check ─────────────────────────────────────────────────
    # Build a deterministic key from the parsed filters + pagination params.
    # json.dumps with sort_keys ensures dict ordering never affects the key.
    cache_key = f"search:{json.dumps(filters, sort_keys=True)}:p{page}:l{limit}"
    cached = get_query_cache(cache_key)
    if cached:
        return cached

    async with AsyncSessionLocal() as session:
        query = select(Profile)
        if "gender" in filters:
            query = query.where(Profile.gender == filters["gender"])
        if "age_group" in filters:
            query = query.where(Profile.age_group == filters["age_group"])
        if "min_age" in filters:
            query = query.where(Profile.age >= filters["min_age"])
        if "max_age" in filters:
            query = query.where(Profile.age <= filters["max_age"])
        if "country_id" in filters:
            query = query.where(Profile.country_id == filters["country_id"])

        count_result = await session.execute(
            select(func.count()).select_from(query.subquery())
        )
        total = count_result.scalar()
        total_pages = math.ceil(total / limit)

        offset = (page - 1) * limit
        query = query.offset(offset).limit(limit)
        result = await session.execute(query)
        profiles = result.scalars().all()

    response_data = {
        "status": "success",
        "page": page,
        "limit": limit,
        "total": total,
        "total_pages": total_pages,
        "links": {
            "self": build_url(request, page),
            "next": build_url(request, page + 1) if page < total_pages else None,
            "prev": build_url(request, page - 1) if page > 1 else None,
        },
        "data": [profile_to_dict(p) for p in profiles]
    }
    set_query_cache(cache_key, response_data)
    return response_data


@router.get("/profiles", response_model=ProfileListResponse)
@limiter.limit("60/minute")
async def get_all_profiles(
    request: Request,
    gender: Optional[str] = None,
    country_id: Optional[str] = None,
    age_group: Optional[str] = None,
    min_age: Optional[int] = None,
    max_age: Optional[int] = None,
    min_gender_probability: Optional[float] = None,
    min_country_probability: Optional[float] = None,
    sort_by: Optional[str] = None,
    order: str = Query(default="asc", pattern="^(asc|desc)$"),
    page: int = Query(default=1, ge=1),
    limit: int = Query(default=10, ge=1, le=50),
    current_user: User = Depends(get_current_user),
):
    SORTABLE_FIELDS = {
        "age": Profile.age,
        "created_at": Profile.created_at,
        "gender_probability": Profile.gender_probability,
    }
    if sort_by and sort_by not in SORTABLE_FIELDS:
        raise HTTPException(status_code=400, detail={
            "status": "error",
            "message": f"sort_by must be one of: {', '.join(SORTABLE_FIELDS)}"
        })

    # ── Cache check ─────────────────────────────────────────────────
    cache_key = (
        f"list:g={gender}:c={country_id}:ag={age_group}"
        f":mna={min_age}:mxa={max_age}:mgp={min_gender_probability}"
        f":mcp={min_country_probability}:sb={sort_by}:o={order}"
        f":p={page}:l={limit}"
    )
    cached = get_query_cache(cache_key)
    if cached:
        return cached

    async with AsyncSessionLocal() as session:
        query = select(Profile)
        if gender:
            query = query.where(Profile.gender == gender.lower())
        if country_id:
            query = query.where(Profile.country_id == country_id.upper())
        if age_group:
            query = query.where(Profile.age_group == age_group.lower())
        if min_age is not None:
            query = query.where(Profile.age >= min_age)
        if max_age is not None:
            query = query.where(Profile.age <= max_age)
        if min_gender_probability is not None:
            query = query.where(Profile.gender_probability >= min_gender_probability)
        if min_country_probability is not None:
            query = query.where(Profile.country_probability >= min_country_probability)

        count_result = await session.execute(
            select(func.count()).select_from(query.subquery())
        )
        total = count_result.scalar()
        total_pages = math.ceil(total / limit)

        if sort_by:
            col = SORTABLE_FIELDS[sort_by]
            query = query.order_by(col.desc() if order == "desc" else col.asc())

        offset = (page - 1) * limit
        query = query.offset(offset).limit(limit)
        result = await session.execute(query)
        profiles = result.scalars().all()

    response_data = {
        "status": "success",
        "page": page,
        "limit": limit,
        "total": total,
        "total_pages": total_pages,
        "links": {
            "self": build_url(request, page),
            "next": build_url(request, page + 1) if page < total_pages else None,
            "prev": build_url(request, page - 1) if page > 1 else None,
        },
        "data": [profile_to_dict(p) for p in profiles]
    }
    set_query_cache(cache_key, response_data)
    return response_data


@router.get("/profiles/{profile_id}")
@limiter.limit("60/minute")
async def get_profile(
    request: Request,
    profile_id: str,
    current_user: User = Depends(get_current_user),
):
    if not is_valid_uuid(profile_id):
        raise HTTPException(status_code=422, detail={
            "status": "error",
            "message": "Invalid parameter type"
        })
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(Profile).where(Profile.id == profile_id)
        )
        profile = result.scalar_one_or_none()
        if not profile:
            raise HTTPException(status_code=404, detail={
                "status": "error",
                "message": "Profile not found"
            })
    return {"status": "success", "data": profile_to_dict(profile)}


@router.delete("/profiles/{profile_id}", status_code=status.HTTP_204_NO_CONTENT)
@limiter.limit("60/minute")
async def delete_profile(
    request: Request,
    profile_id: str,
    current_user: User = Depends(require_admin),
):
    if not is_valid_uuid(profile_id):
        raise HTTPException(status_code=422, detail={
            "status": "error",
            "message": "Invalid parameter type"
        })
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(Profile).where(Profile.id == profile_id)
        )
        profile = result.scalar_one_or_none()
        if not profile:
            raise HTTPException(status_code=404, detail={
                "status": "error",
                "message": "Profile not found"
            })
        await session.delete(profile)
        await session.commit()
        # Profile deleted — clear cached results so readers don't see stale counts
        invalidate_query_cache()
    return Response(status_code=status.HTTP_204_NO_CONTENT)