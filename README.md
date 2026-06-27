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
| Age group | `senior`, `seniors`, `elderly`, `old` | `age_group=senior` |
| Age range | `under 30`, `older than 25`, `between 20 and 40` | `min_age` / `max_age` |
| Country | Any country name or demonym (`Nigerian`, `Nigeria`, `NG`) | `country_id=NG` |

### Age Group Boundaries

Computed by `get_age_group()` in `utils.py`:

| Age | Group |
|---|---|
| 0 – 12 | `child` |
| 13 – 19 | `teenager` |
| 20 – 59 | `adult` |
| 60 + | `senior` |

### Country Resolution

Country names and demonyms are resolved to ISO 3166-1 alpha-2 codes using
`pycountry`. The resolver is wrapped with `@functools.lru_cache` so repeated
lookups for the same country name cost nothing after the first call.

```python
get_country_name("NG")   # → "Nigeria"
get_country_name("US")   # → "United States"
```

### Example Queries

| Plain English | Parsed Filters |
|---|---|
| `"young males in Nigeria"` | `gender=male, age_group=teenager, country_id=NG` |
| `"adult women from the US"` | `gender=female, age_group=adult, country_id=US` |
| `"seniors older than 65"` | `age_group=senior, min_age=65` |

---

## Local Setup

### Requirements
- Python 3.10+
- PostgreSQL

### Install

```bash
pip install -r requirements.txt
```

### Environment Variables

Copy `.env.example` to `.env` and fill in your values:

```bash
cp .env.example .env
```

### Run

```bash
uvicorn main:app --reload
```

API docs available at: http://localhost:8000/docs
