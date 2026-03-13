# Sarj Docs

Internal knowledge base for [@sarj.ai](https://sarj.ai), built on top of Google Docs. Notion-like UX with nested sidebar navigation, workspace grouping via Google Admin, and embedded Google Docs editing — all within the Google Workspace ecosystem.

## Tech Stack

- **Frontend:** Next.js (App Router), Shadcn UI, Tailwind CSS
- **Backend:** FastAPI (Python), SQLAlchemy async, Alembic
- **Database:** PostgreSQL
- **Auth:** Google OAuth 2.0 (restricted to @sarj.ai)
- **Content:** Google Drive / Google Docs
 
## Setup  
 
### Backend 
 
```bash  
cd backend 
uv sync
cp .env.example .env  # fill in your values
uv run alembic upgrade head
uv run uvicorn app.main:app --reload
```

### Frontend

```bash
cd frontend
npm install
cp .env.example .env.local  # fill in your values
npm run dev
```

## Architecture

- Next.js client calls FastAPI endpoints only — never Google APIs directly
- FastAPI handles all Google API calls, token management, DB operations, and access control
- Google Drive folder structure mirrors the app's page tree
- Google Admin SDK syncs workspace groups and memberships
