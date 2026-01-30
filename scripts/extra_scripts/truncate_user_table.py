# scripts/truncate_user_table.py

import sys
from pathlib import Path
from sqlalchemy import text

# ---------------------------------------------------------------
# Add PROJECT ROOT to PYTHONPATH so package imports work
# ---------------------------------------------------------------
CURRENT_FILE = Path(__file__).resolve()
PROJECT_ROOT = CURRENT_FILE.parents[1]  # root of your project, which contains `services/`

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# Now use package-style imports
from services.engine.database import engine
from services.engine.config import settings


def truncate_user_table() -> None:
    """
    TRUNCATE ONLY the 'user' table.
    Allowed only when CURRENT_ENVIRONMENT is 'development' or 'local'.
    """

    current_env = settings.CURRENT_ENVIRONMENT.lower()

    # Prevent accidental production wipes
    if current_env not in {"development", "local"}:
        raise RuntimeError(
            f"Truncate blocked: CURRENT_ENVIRONMENT={current_env!r}. "
            "Allowed environments: 'development', 'local'."
        )

    with engine.begin() as connection:
        truncate_sql = (
            'TRUNCATE TABLE "public"."users" '
            'RESTART IDENTITY CASCADE;'
        )

        connection.execute(text(truncate_sql))

        print(
            f"User table truncated successfully "
            f"in CURRENT_ENVIRONMENT={current_env!r}."
        )


if __name__ == "__main__":
    truncate_user_table()
