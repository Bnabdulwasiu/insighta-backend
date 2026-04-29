# Insighta Backend

FastAPI backend for the Insighta Labs+ Profile Intelligence Platform.

## Live URL
- **API:** https://profile-app-5343e495.fastapicloud.dev
- **Docs:** https://profile-app-5343e495.fastapicloud.dev/docs

---

## System Architecture

```
┌─────────────┐     ┌─────────────┐
│   CLI Tool  │     │ Web Portal  │
└──────┬──────┘     └──────┬──────┘
       └───────────┬────────┘
                   │
          ┌────────▼────────┐
          │  FastAPI Backend │
          └────────┬────────┘
                   │
      ┌────────────┼────────────┐
      │            │            │
┌─────▼────┐ ┌─────▼──────┐ ┌──▼──────────┐
│PostgreSQL│ │GitHub OAuth│ │External APIs│
└──────────┘ └────────────┘ │ Genderize   │
                             │ Agify       │
                             │ Nationalize │
                             └─────────────┘
```

---

## Authentication Flow

### CLI Flow
```
1. insighta login
2. CLI generates state, starts local server on localhost:9876
3. Browser opens → /auth/github?state=...&cli_callback=localhost:9876
4. Backend encodes cli_callback into state → redirects to GitHub
5. User approves on GitHub
6. GitHub → /auth/github/callback
7. Backend issues JWT tokens → redirects to localhost:9876?access_token=...
8. CLI saves tokens to ~/.insighta/credentials.json
9. Prints "Logged in as @username"
```

### Web Flow
```
1. User clicks "Continue with GitHub"
2. Backend sets oauth_state HTTP-only cookie → redirects to GitHub
3. User approves on GitHub
4. GitHub → /auth/web/github/callback
5. Backend validates state cookie (CSRF protection)
6. Backend issues tokens as HTTP-only cookies
7. Redirects to /dashboard.html
```

---

## Token Handling

| Token | Expiry | CLI Storage | Web Storage |
|---|---|---|---|
| Access token | 3 minutes | `~/.insighta/credentials.json` | HTTP-only cookie |
| Refresh token | 5 minutes | `~/.insighta/credentials.json` | HTTP-only cookie |

- Refresh tokens are single-use — invalidated immediately on use
- New token pair issued on every refresh
- Web tokens are inaccessible to JavaScript

---

## Role Enforcement

| Role | Permissions |
|---|---|
| `analyst` | GET profiles, search, export |
| `admin` | All analyst permissions + create + delete |

Default role on signup: `analyst`

Promote a user to admin:
```sql
UPDATE users SET role = 'admin' WHERE username = 'your_username';
```

All `/api/*` endpoints require a valid access token.
Inactive users (`is_active = false`) receive `403 Forbidden` on all requests.

---

## Natural Language Parsing

Rule-based parser — no AI or LLMs.

### How It Works

The `parse_query()` function in `utils.py` normalizes the input to lowercase,
strips punctuation, and applies keyword matching and regex patterns in sequence
to extract filters.

### Supported Keywords

| Category | Keywords | Maps to |
|---|---|---|
| Gender | `male`, `males`, `man`, `men`, `boy`, `boys` | `gender=male` |
| Gender | `female`, `females`, `woman`, `women`, `girl`, `girls` | `gender=female` |
| Age group | `child`, `children` | `age_group=child` |
| Age group | `teenager`, `teen`, `teens`, `teenagers` | `age_group=teenager` |
| Age group | `adult`, `adults` | `age_group=adult` |
| Age group | `senior`, `seniors`, `elderly` | `age_group=senior` |
| Special | `young`, `youth` | `min_age=16, max_age=24` |
| Explicit age | `above 30`, `over 30`, `older than 30` | `min_age=30` |
| Explicit age | `below 25`, `under 25`, `younger than 25` | `max_age=25` |
| Explicit age | `between 20 and 35` | `min_age=20, max_age=35` |
| Country | `from nigeria`, `in kenya`, `of ghana` | resolved via pycountry |

### Logic Order
1. Normalize input — lowercase, strip punctuation
2. Detect gender — keyword scan, both present = no gender filter
3. Detect age group — keyword scan, age_group wins over `young`
4. Detect explicit age — regex patterns, first match wins
5. Detect country — trigger word strategy first, token scan fallback

### Examples
```
"young males from nigeria"       → gender=male, min_age=16, max_age=24, country_id=NG
"adult females from kenya"       → gender=female, age_group=adult, country_id=KE
"seniors above 65"               → age_group=senior, min_age=65
"male and female teenagers"      → age_group=teenager (no gender)
"people from south africa"       → country_id=ZA
"women below 30 in japan"        → gender=female, max_age=30, country_id=JP
```

### Limitations
- No negation support — `"not from nigeria"` is ignored
- No ISO code matching — use full country names, not `"NG"`
- No spelling correction — `"nigerria"` will not resolve
- Only first age pattern matched per query
- `"young"` + explicit age can produce conflicting range
- Multi-word countries need trigger word — `"from south africa"` works, `"south africa males"` may not
- No support for multiple countries in one query

---

## API Reference

Base URL: `https://profile-app-5343e495.fastapicloud.dev`

All `/api/*` requests require:
```
Authorization: Bearer <access_token>
X-API-Version: 1
```

### Profile Endpoints

| Method | Endpoint | Role | Description |
|---|---|---|---|
| POST | /api/profiles | admin | Create profile |
| GET | /api/profiles | analyst | List with filters + pagination |
| GET | /api/profiles/search | analyst | Natural language search |
| GET | /api/profiles/export | analyst | Export CSV |
| GET | /api/profiles/parse | analyst | Debug query parser |
| GET | /api/profiles/{id} | analyst | Get single profile |
| DELETE | /api/profiles/{id} | admin | Delete profile |

### Filtering (GET /api/profiles)

| Param | Type | Example |
|---|---|---|
| gender | string | `male` or `female` |
| age_group | string | `child`, `teenager`, `adult`, `senior` |
| country_id | string | `NG`, `KE`, `US` |
| min_age | int | `25` |
| max_age | int | `40` |
| min_gender_probability | float | `0.8` |
| min_country_probability | float | `0.7` |
| sort_by | string | `age`, `created_at`, `gender_probability` |
| order | string | `asc` or `desc` |
| page | int | `1` |
| limit | int | `10` (max 50) |

### Pagination Response Format
```json
{
  "status": "success",
  "page": 1,
  "limit": 10,
  "total": 2026,
  "total_pages": 203,
  "links": {
    "self": "/api/profiles?page=1&limit=10",
    "next": "/api/profiles?page=2&limit=10",
    "prev": null
  },
  "data": []
}
```

### Auth Endpoints

| Method | Endpoint | Description |
|---|---|---|
| GET | /auth/github | CLI OAuth start |
| GET | /auth/github/callback | CLI OAuth callback |
| POST | /auth/refresh | Refresh tokens (CLI) |
| POST | /auth/logout | Logout (CLI) |
| GET | /auth/me | Current user (CLI) |
| GET | /auth/web/github | Web OAuth start |
| GET | /auth/web/github/callback | Web OAuth callback |
| POST | /auth/web/refresh | Refresh tokens (Web) |
| POST | /auth/web/logout | Logout (Web) |
| GET | /auth/web/me | Current user (Web) |

---

## Error Responses

All errors follow this structure:
```json
{ "status": "error", "message": "<error message>" }
```

| Status | Meaning |
|---|---|
| 400 | Missing or invalid parameter |
| 401 | Invalid or expired token |
| 403 | Insufficient permissions or inactive account |
| 404 | Profile not found |
| 422 | Invalid parameter type |
| 429 | Rate limit exceeded |
| 502 | Upstream API failure |
| 500 | Internal server error |

---

## Rate Limiting

| Scope | Limit |
|---|---|
| `/auth/*` endpoints | 10 requests/minute |
| All other endpoints | 60 requests/minute |

Returns `429 Too Many Requests` when exceeded.

---

## Local Development

```bash
git clone https://github.com/Bnabdulwasiu/insighta-backend.git
cd insighta-backend
python -m venv venv
source venv/bin/activate   # Windows: venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env       # fill in your values
uvicorn main:app --reload
```

---

## Environment Variables

```env
DB_USER=
DB_PASSWORD=
DB_HOST=
DB_PORT=
DB_NAME=
GITHUB_CLIENT_ID=
GITHUB_CLIENT_SECRET=
GITHUB_WEB_CLIENT_ID=
GITHUB_WEB_CLIENT_SECRET=
JWT_SECRET=
FRONTEND_URL=
```

---

## Project Structure

```
insighta-backend/
├── main.py              # app setup, middleware, exception handlers
├── auth.py              # JWT utilities, auth dependencies
├── database.py          # async engine, session factory
├── models.py            # SQLAlchemy models (Profile, User, RefreshToken)
├── schemas.py           # Pydantic request/response schemas
├── utils.py             # helpers, NLP parser, profile_to_dict
├── core/
│   └── limiter.py       # slowapi rate limiter instance
├── middleware/
│   ├── versioning.py    # X-API-Version header enforcement
│   └── logging.py       # per-request method/path/status/time logging
├── routers/
│   ├── auth.py          # /auth/* endpoints (CLI + Web)
│   └── profiles.py      # /api/profiles/* endpoints
└── tests/
    └── test_health.py
```

---

## CI/CD

GitHub Actions runs on every PR to `main`:
- Ruff linting
- Pytest tests
- PostgreSQL service container

Branch protection on `main` requires:
- PR review before merge
- CI checks to pass