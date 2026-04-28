from fastapi import (FastAPI, HTTPException, Request,
                      status, Response, Query, Depends)
import httpx
from starlette.middleware.base import BaseHTTPMiddleware
from contextlib import asynccontextmanager
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.exceptions import RequestValidationError
import asyncio
from database import engine, Base, AsyncSessionLocal
from schemas import ProfileSchema, CreateProfileRequest, ProfileListResponse
from models import Profile
from auth import get_current_user, require_admin
from models import User
from utils import (get_age_group, profile_to_dict,
                    get_country_name, seed_database,
                    parse_query, is_valid_uuid, build_url)
from typing import Optional
import time
import logging
from routers.auth import router as auth_router
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded
import math

# Database Setup
from sqlalchemy import select, func
from sqlalchemy.exc import IntegrityError


@asynccontextmanager
async def lifespan(app: FastAPI):
    #Create DB tables
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    app.state.genderize = httpx.AsyncClient(base_url="https://api.genderize.io", timeout=10.0)
    app.state.agify = httpx.AsyncClient(base_url="https://api.agify.io", timeout=10.0)
    app.state.nationalize = httpx.AsyncClient(base_url="https://api.nationalize.io", timeout=10.0)
    
    print("✅ Tables created")
    asyncio.create_task(seed_database())
    yield

    await asyncio.gather(

        app.state.genderize.aclose(),
        app.state.agify.aclose(),
        app.state.nationalize.aclose(),

    )


app = FastAPI(lifespan=lifespan)
app.include_router(auth_router)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Exception Handlers
@app.exception_handler(HTTPException)
async def custom_http_exception_handler(request: Request, exc: HTTPException):

    if isinstance(exc.detail, dict):
        return JSONResponse(
            status_code=exc.status_code,
            content=exc.detail
        )

    return JSONResponse(
        status_code=exc.status_code,
        content={
            "status": "error",
            "message": str(exc.detail)
        }
    )

@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
        return JSONResponse(
        status_code=422,
        content={
            "status": "error",
            "message": "Invalid query parameters"
        }
    )

# API versioning
class APIVersionMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        if request.url.path.startswith("/api/"):
            version = request.headers.get("X-API-Version")
            if not version:
                return JSONResponse(
                    status_code=400,
                    content={
                        "status": "error",
                        "message": "API version header required"
                    }
                )
        return await call_next(request)

app.add_middleware(APIVersionMiddleware)

# Logging middleware
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class LoggingMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        start = time.time()
        response = await call_next(request)
        duration = round((time.time() - start) * 1000, 2)
        logger.info(
            f"{request.method} {request.url.path} "
            f"→ {response.status_code} ({duration}ms)"
        )
        return response

app.add_middleware(LoggingMiddleware)


limiter = Limiter(key_func=get_remote_address)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)


# Post function
@app.post("/api/profiles", response_model=ProfileSchema, status_code=201)
@limiter.limit("60/minute")
async def create_profile(request: Request,
                         body: CreateProfileRequest,
                         current_user: User = Depends(require_admin)
):
    name =  body.name.strip().lower()
    if not name:
        raise HTTPException(status_code=400, detail={
            "status": "error",
            "message": "Missing or empty name"
        })

    async with AsyncSessionLocal() as session:
        try:
            gender_res, age_res, nation_res = await asyncio.gather(
                app.state.genderize.get("/", params={"name": name}),
                app.state.agify.get("/", params={"name": name}),
                app.state.nationalize.get("/", params={"name": name}),
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

        # Pick top country by probability
        top_country = max(countries, key=lambda c: c["probability"]) if countries else None
        country_id=top_country["country_id"] if top_country else None
        full_country_name = get_country_name(country_id)
        
        profile = Profile(
            name=name,
            gender=gender_data.get("gender"),
            gender_probability=gender_data.get("probability"),
            age=age_data.get("age"),
            age_group=get_age_group(age_data.get("age")),
            country_id=country_id,
            country_name=full_country_name,
            country_probability=top_country["probability"] if top_country else None,
        )

        session.add(profile)

        try:
            await session.commit()
            await session.refresh(profile)

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


@app.get("/api/profiles" , response_model=ProfileListResponse)
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
    # Sorting
    sort_by: Optional[str] = None,       
    order: str = Query(default="asc", pattern="^(asc|desc)$"),  
    # Pagination
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
            "message": f"Invalid sort_by value. Must be one of: {', '.join(SORTABLE_FIELDS)}"
        })
    
    async with AsyncSessionLocal() as session:
        query = select(Profile)

        # Apply filters if provided
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
        
        # Total count (before pagination) 
        count_result = await session.execute(select(func.count()).select_from(query.subquery()))
        total = count_result.scalar()

        # Sorting 
        if sort_by:
            column = SORTABLE_FIELDS[sort_by]
            query = query.order_by(column.desc() if order == "desc" else column.asc())
        
        # Pagination
        offset = (page - 1) * limit
        query = query.offset(offset).limit(limit)
        result = await session.execute(query)
        profiles = result.scalars().all()

        total_pages = math.ciel(total / limit)

        return {
            "status": "success",
            "page": page,
            "limit": limit,
            "total": total,
            "total_pages": total_pages,
            "links": {
                "self": build_url(page),
                "next": build_url(page + 1) if page < total_pages else None,
                "prev": build_url(page - 1) if page > 1 else None,
            },
            "data": [profile_to_dict(p) for p in profiles]
        }


# Debug endpoint — proves parser works independently
@app.get("/api/profiles/parse")
async def parse_profile_query(q: str = Query(..., min_length=1)):
    filters = parse_query(q)
    if not filters:
        raise HTTPException(status_code=400, detail={
            "status": "error",
            "message": "Unable to interpret query"
        })
    return {"q": q, "parsed_filters": filters}


# Natural language search endpoint
@app.get("/api/profiles/search")
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
        if "min_gender_probability" in filters:
            query = query.where(Profile.gender_probability >= filters["min_gender_probability"])
        if "min_country_probability" in filters:
            query = query.where(Profile.country_probability >= filters["min_country_probability"])

        count_result = await session.execute(select(func.count()).select_from(query.subquery()))
        total = count_result.scalar()

        offset = (page - 1) * limit
        query = query.offset(offset).limit(limit)

        result = await session.execute(query)
        profiles = result.scalars().all()
        total_pages = math.ceil(total / limit)

        return {
            "status": "success",
            # "q": q,
            # "parsed_filters": filters,   # helpful for transparency/debugging
            "page": page,
            "limit": limit,
            "total": total,
            "total_pages": total_pages,
            "links": {
                "self": build_url(page),
                "next": build_url(page + 1) if page < total_pages else None,
                "prev": build_url(page - 1) if page > 1 else None,
            },
            "data": [profile_to_dict(p) for p in profiles]
        }

@app.get("/api/profiles/{profile_id}")
@limiter.limit("60/minute")
async def get_profile(
    reques: Request,
    profile_id: str,
    current_user: User = Depends(get_current_user)
):
    if not is_valid_uuid(profile_id):
        raise HTTPException(status_code=422, detail={
            "status": "error",
            "message": "Invalid parameter type"
        })
    async with AsyncSessionLocal() as session:
        result = await session.execute(select(Profile).where(Profile.id == profile_id))
        profile = result.scalar_one_or_none()

        if not profile:
            raise HTTPException(status_code=404, detail={
                "status": "error",
                "message": "Profile not found"
            })

        return {"status": "success", "data": profile_to_dict(profile)}

    
@app.delete("/api/profiles/{profile_id}", status_code=status.HTTP_204_NO_CONTENT)
@limiter.limit("60/minute")
async def delete_profile(
    request:Request,
    profile_id: str,
    current_user: User = Depends(require_admin)
):
    if not is_valid_uuid(profile_id):
        raise HTTPException(status_code=422, detail={
            "status": "error",
            "message": "Invalid parameter type"
        })
    
    async with AsyncSessionLocal() as session:
        result = await session.execute(select(Profile).where(Profile.id == profile_id))
        profile = result.scalar_one_or_none()

        if not profile:
            raise HTTPException(status_code=404, detail={
                "status": "error",
                "message": "Profile not found"
            })
        
        await session.delete(profile)
        await session.commit()

        # 4. Return Response(status_code=204) or simply None
        return Response(status_code=status.HTTP_204_NO_CONTENT)