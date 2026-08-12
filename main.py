import sqlite3
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from typing import Optional

# File lives next to main.py and is created automatically on first run, so
# no manual setup step is needed before the app can start
DB_FILE = "tasks.db"


def get_connection():
    # row_factory lets a row be read like a dict (row["title"]) instead of
    # by positional index, which keeps future query code close to the shape
    # the API already returns to clients
    connection = sqlite3.connect(DB_FILE)
    connection.row_factory = sqlite3.Row
    return connection


def row_to_task(row):
    # done is stored as 0/1 since SQLite has no native boolean type. Cast
    # back to bool here so the response shape matches Assignment 1 exactly
    return {"id": row["id"], "title": row["title"], "done": bool(row["done"])}


def init_db():
    connection = get_connection()
    cursor = connection.cursor()

    # IF NOT EXISTS makes this safe to call on every startup, a restart
    # should never fail just because the table was already created before
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS tasks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            done BOOLEAN NOT NULL DEFAULT 0
        )
    """)

    cursor.execute("SELECT COUNT(*) FROM tasks")
    existing_task_count = cursor.fetchone()[0]

    # Seeding only happens when the table is empty so the same three example
    # tasks are not re-inserted every time the app restarts
    if existing_task_count == 0:
        cursor.executemany(
            "INSERT INTO tasks (title, done) VALUES (?, ?)",
            [
                ("I will Learn FastAPI basics", False),
                ("I will Build the tasks endpoint", False),
                ("I will Test with curl", True),
            ]
        )

    connection.commit()
    connection.close()


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Runs once before the app accepts any requests, so the table and seed
    # data are guaranteed to exist before the first endpoint call arrives
    init_db()
    yield


# Title and description show up at the top of Swagger UI so anyone opening
# /docs knows what this API is for before reading a single endpoint
app = FastAPI(
    title="Task API",
    description="A simple in memory CRUD API for managing tasks",
    lifespan=lifespan
)


# title is optional here rather than required so a missing title reaches our
# own check below and gets a 400 with a clear message instead of FastAPI's
# generic 422 validation error
class TaskCreate(BaseModel):
    title: Optional[str] = None


# Both fields are optional since a client may update only the title or only
# done. Requiring both would force callers to resend data they did not change
class TaskUpdate(BaseModel):
    title: Optional[str] = None
    done: Optional[bool] = None


# Endpoints below still read/write this list rather than tasks.db. The table
# above exists and is seeded, but nothing is wired into the CRUD routes yet -
# that swap happens in the next stage
tasks = [
    {"id": 1, "title": "I will Learn FastAPI basics", "done": False},
    {"id": 2, "title": "I will Build the tasks endpoint", "done": False},
    {"id": 3, "title": "I will Test with curl", "done": True}
]

# Running counter tracks the next id to hand out. Starts after the seed
# tasks above so a new task never collides with an existing id
next_task_id = 4


@app.get("/", summary="API info")
def read_root():
    # Returning a plain dict here since FastAPI serializes it to JSON automatically
    # endpoints list is hardcoded for now since /tasks is the only route that exists
    return {
        "name": "Task API",
        "version": "1.0",
        "endpoints": ["/tasks"]
    }


@app.get("/health", summary="Check server is alive")
def health_check():
    # No database or dependency check here since this endpoint only needs to prove
    # the server process itself is up and able to respond
    return {"status": "ok"}


@app.get("/tasks", summary="List all tasks")
def get_tasks():
    # Straight read from tasks.db, no more in-memory list
    connection = get_connection()
    rows = connection.execute("SELECT * FROM tasks").fetchall()
    connection.close()

    return [row_to_task(row) for row in rows]


@app.get("/tasks/{task_id}", summary="Get a single task by id")
def get_task(task_id: int):
    # ? placeholder keeps the id out of the query string, avoids SQL injection
    connection = get_connection()
    row = connection.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
    connection.close()

    # Plain JSONResponse here, not HTTPException, so the key is "error" not "detail"
    if row is None:
        return JSONResponse(status_code=404, content={"error": "Task not found"})

    return row_to_task(row)


@app.post("/tasks", status_code=201, summary="Create a new task")
def create_task(task: TaskCreate):
    # Title is checked here since the server is the last line of defense
    # and must never assume the client sent valid data
    if not task.title or not task.title.strip():
        raise HTTPException(status_code=400, detail="Title is required and cannot be empty")

    global next_task_id

    # Counter is used instead of checking existing ids so a task id is never
    # reused even after the task with that id has been deleted
    new_task = {"id": next_task_id, "title": task.title, "done": False}
    tasks.append(new_task)
    next_task_id += 1

    return new_task


@app.put("/tasks/{task_id}", summary="Update a task's title and/or done status")
def update_task(task_id: int, task_update: TaskUpdate):
    # Task is looked up first since a 404 should win over a 400. There is
    # nothing to validate an update against if the task does not exist
    found_task = None
    for task in tasks:
        if task["id"] == task_id:
            found_task = task
            break

    if found_task is None:
        raise HTTPException(status_code=404, detail=f"Task {task_id} not found")

    # Both fields missing means the client sent nothing worth applying
    if task_update.title is None and task_update.done is None:
        raise HTTPException(status_code=400, detail="Request body must include title or done")

    # A title that is present but blank is treated the same as a missing
    # title since an empty name is not a usable task title
    if task_update.title is not None:
        if not task_update.title.strip():
            raise HTTPException(status_code=400, detail="Title cannot be empty")
        found_task["title"] = task_update.title

    if task_update.done is not None:
        found_task["done"] = task_update.done

    return found_task


@app.delete("/tasks/{task_id}", status_code=204, summary="Delete a task by id")
def delete_task(task_id: int):
    # Index is needed here rather than just the task since list.pop requires
    # a position and there is no separate id to index map kept for this list
    found_index = None
    for index, task in enumerate(tasks):
        if task["id"] == task_id:
            found_index = index
            break

    if found_index is None:
        raise HTTPException(status_code=404, detail=f"Task {task_id} not found")

    tasks.pop(found_index)

    # Response with no body is returned explicitly since 204 must not include
    # a payload. Returning None would still serialize to a json null body
    return Response(status_code=204)
