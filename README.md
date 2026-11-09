# NegosyoOnline Backend

Clean Django foundation for the NegosyoOnline API.

## Local setup

1. Use the safe default SQLite database for basic local checks. See `.env.example` for the variables required when connecting to another environment. The initial foundation reads operating-system variables only.
2. Run `uv run python manage.py migrate` only when a developer explicitly needs a local database.
3. Run `uv run python manage.py runserver`.

Railway staging owns PostgreSQL and Redis. Its credentials must be configured later as Railway environment variables, not committed here.
