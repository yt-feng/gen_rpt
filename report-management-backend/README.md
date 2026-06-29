# Report Management Backend

This is the independent backend architecture for report management, built using FastAPI, SQLAlchemy 2.0 (Async), and PostgreSQL.

## Supabase Database Connection

The backend uses a single connection string to connect to the PostgreSQL database (Supabase).
All services, including SQLAlchemy, Alembic, and health checks, rely exclusively on `DATABASE_URL`.

**DATABASE_URL Format:**
```
postgresql+asyncpg://[user]:[password]@[host]:[port]/[database]
```
*Example for Supabase:*
```
DATABASE_URL=postgresql+asyncpg://postgres:YourSecurePassword!@db.xxxxxxxxxx.supabase.co:5432/postgres
```

## Required Environment Variables

To run the application, copy `.env.example` to `.env` and configure the following required variables:

- `APP_ENV`: Environment (e.g., development, production)
- `DATABASE_URL`: The single PostgreSQL connection string.
- `JWT_SECRET`: Secret key for JWT auth.
- `R2_...`: Cloudflare R2 configurations (required for storage operations).

> **Important:** The application will fail to start if `DATABASE_URL` is missing from the environment.

## Local Development

1. Copy `.env.example` to `.env` and fill in the values, ensuring `DATABASE_URL` points to your local or staging PostgreSQL database.
2. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```
3. Run the development server:
   ```bash
   uvicorn app.main:app --reload
   ```
4. Verify the database connection by navigating to: `http://localhost:8000/health`

Alternatively, you can run the application with docker-compose:
```bash
docker-compose up --build
```

## Alembic Migration Workflow

Alembic automatically picks up the `DATABASE_URL` from the environment. There is no need to manually update `alembic.ini`.

- **Create a new migration (after modifying models):**
  ```bash
  alembic revision --autogenerate -m "Description of change"
  ```
- **Apply migrations to your database:**
  ```bash
  alembic upgrade head
  ```
- **Revert the last migration:**
  ```bash
  alembic downgrade -1
  ```

## Production Deployment

In a production environment (e.g., Docker container, cloud platform):
1. Ensure the container has the `DATABASE_URL` securely injected as an environment variable (do not bake `.env` into the image).
2. Set `APP_ENV=production` and `APP_DEBUG=false`.
3. The application will use a production-ready asynchronous connection pool (`pool_size=10`, `max_overflow=20`, `pool_pre_ping=True`) for optimal Supabase connection management.
