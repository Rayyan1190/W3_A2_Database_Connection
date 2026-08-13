import os

import psycopg
from psycopg.rows import dict_row
from dotenv import load_dotenv

# Reads .env into the process environment. Safe to call even if .env is
# missing (e.g. in CI), since real deployments would set DATABASE_URL
# through the platform's own environment instead of a file.
load_dotenv()

# No hardcoded fallback on purpose - a missing DATABASE_URL should fail loudly
# at startup instead of silently connecting to the wrong database.
DATABASE_URL = os.environ["DATABASE_URL"]


def get_connection():
    # dict_row makes each row behave like a dict (row["title"]) instead of a
    # plain tuple, matching how sqlite3.Row worked in the SQLite version so
    # the route code barely had to change when the driver was swapped.
    return psycopg.connect(DATABASE_URL, row_factory=dict_row)


def row_to_task(row):
    # done is a native boolean column in Postgres, so no int-to-bool cast is
    # needed here anymore - kept as its own function anyway so route code
    # doesn't care how a row became a dict.
    return {"id": row["id"], "title": row["title"], "done": row["done"]}


def init_db():
    connection = get_connection()

    # IF NOT EXISTS makes this safe to call on every startup, a restart
    # should never fail just because the table was already created before.
    # serial replaces SQLite's AUTOINCREMENT for an auto-incrementing id.
    connection.execute("""
        CREATE TABLE IF NOT EXISTS tasks (
            id SERIAL PRIMARY KEY,
            title TEXT NOT NULL,
            done BOOLEAN NOT NULL DEFAULT FALSE
        )
    """)

    existing_task_count = connection.execute("SELECT COUNT(*) FROM tasks").fetchone()["count"]

    # Seeding only happens when the table is empty so the same three example
    # tasks are not re-inserted every time the app restarts.
    if existing_task_count == 0:
        connection.execute(
            "INSERT INTO tasks (title, done) VALUES (%s, %s), (%s, %s), (%s, %s)",
            (
                "I will Learn FastAPI basics", False,
                "I will Build the tasks endpoint", False,
                "I will Test with curl", True,
            )
        )

    connection.commit()
    connection.close()
