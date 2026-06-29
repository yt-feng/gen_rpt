# Report Management Backend

This is Phase 1 of the new independent backend architecture for report management. 

## Tech Stack
- **Framework**: FastAPI
- **Database ORM**: SQLAlchemy 2.0 (Async) + Alembic
- **Validation**: Pydantic v2
- **Storage**: Cloudflare R2 (via boto3)
- **Database**: PostgreSQL (Supabase)

## Structure
- `app/api/`: API Routes
- `app/core/`: Configuration and Security
- `app/database/`: Database session setup
- `app/services/`: Service layer interfaces
- `app/repositories/`: Database repository interfaces
- `app/storage/`: Storage provider interfaces

## Getting Started

1. Copy `.env.example` to `.env` and fill in values.
2. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```
3. Run locally:
   ```bash
   uvicorn app.main:app --reload
   ```

Or run with docker-compose:
```bash
docker-compose up --build
```
