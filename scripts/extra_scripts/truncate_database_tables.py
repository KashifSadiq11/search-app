# truncate_database_tables.py
from sqlalchemy import text, inspect
from database import engine
from config import settings

def truncate_all_tables() -> None:
    """
    Truncate all application tables safely.
    Only allowed when CURRENT_ENVIRONMENT is 'development' or 'local'.
    """

    current_env = settings.CURRENT_ENVIRONMENT.lower()

    # Allow truncation only in safe environments
    if current_env not in {"development", "local"}:
        raise RuntimeError(
            f"Truncation blocked: CURRENT_ENVIRONMENT={current_env!r}. "
            "Allowed environments: 'development', 'local'."
        )

    with engine.begin() as connection:
        inspector = inspect(connection)

        # Get tables from public schema
        table_names = inspector.get_table_names(schema="public")

        # Exclude migration/system tables
        EXCLUDED_TABLES = {"alembic_version"}
        table_names = [t for t in table_names if t not in EXCLUDED_TABLES]

        if not table_names:
            print("No tables found to truncate.")
            return

        qualified_tables = [f'"public"."{t}"' for t in table_names]

        truncate_sql = (
            "TRUNCATE TABLE "
            + ", ".join(qualified_tables)
            + " RESTART IDENTITY CASCADE;"
        )

        connection.execute(text(truncate_sql))

        print(
            f"Successfully truncated {len(table_names)} tables "
            f"in CURRENT_ENVIRONMENT={current_env!r}."
        )


if __name__ == "__main__":
    truncate_all_tables()
