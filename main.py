from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from typing import Optional

# All connection/table/seed logic lives in db.py now - this file only ever
# imports from it, it never talks to Postgres directly
from db import get_connection, row_to_task, init_db


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
    description="A simple Postgres-backed CRUD API for managing tasks",
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
    # Straight read from Postgres, no more in-memory list
    connection = get_connection()
    rows = connection.execute("SELECT * FROM tasks").fetchall()
    connection.close()

    return [row_to_task(row) for row in rows]


@app.get("/tasks/{task_id}", summary="Get a single task by id")
def get_task(task_id: int):
    # %s placeholder keeps the id out of the query string, avoids SQL injection
    # (psycopg's paramstyle, replaces SQLite's ? from the previous version)
    connection = get_connection()
    row = connection.execute("SELECT * FROM tasks WHERE id = %s", (task_id,)).fetchone()
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

    connection = get_connection()

    # Title is bound as a parameter instead of built into the SQL string, so it can never break out of the query.
    # RETURNING id hands back the id Postgres just assigned via the serial column - psycopg has no
    # lastrowid like sqlite3 did, so this is how the new id is read in one round trip.
    new_task_id = connection.execute(
        "INSERT INTO tasks (title, done) VALUES (%s, %s) RETURNING id", (task.title, False)
    ).fetchone()["id"]
    connection.commit()
    connection.close()

    return {"id": new_task_id, "title": task.title, "done": False}


@app.put("/tasks/{task_id}", summary="Update a task's title and/or done status")
def update_task(task_id: int, task_update: TaskUpdate):
    connection = get_connection()

    # Row is fetched first since a 404 should win over a 400, and the UPDATE below needs
    # the current title/done for whichever field the client did not send.
    row = connection.execute("SELECT * FROM tasks WHERE id = %s", (task_id,)).fetchone()

    if row is None:
        connection.close()
        raise HTTPException(status_code=404, detail=f"Task {task_id} not found")

    # Both fields missing means the client sent nothing worth applying
    if task_update.title is None and task_update.done is None:
        connection.close()
        raise HTTPException(status_code=400, detail="Request body must include title or done")

    # A title that is present but blank is treated the same as a missing
    # title since an empty name is not a usable task title
    if task_update.title is not None and not task_update.title.strip():
        connection.close()
        raise HTTPException(status_code=400, detail="Title cannot be empty")

    # A field left out of the request keeps its current value instead of being overwritten.
    new_title = task_update.title if task_update.title is not None else row["title"]
    new_done = task_update.done if task_update.done is not None else row["done"]

    # Both columns are written every time, with values bound as parameters so the
    # id and title can never be spliced into the SQL string.
    connection.execute(
        "UPDATE tasks SET title = %s, done = %s WHERE id = %s", (new_title, new_done, task_id)
    )
    connection.commit()
    connection.close()

    return {"id": task_id, "title": new_title, "done": new_done}


@app.delete("/tasks/{task_id}", status_code=204, summary="Delete a task by id")
def delete_task(task_id: int):
    connection = get_connection()

    # id is bound as a parameter rather than formatted into the string, same reason as every other query here.
    cursor = connection.execute("DELETE FROM tasks WHERE id = %s", (task_id,))
    connection.commit()
    connection.close()

    # rowcount is 0 when no row matched that id, which tells us it never existed
    # without needing a separate SELECT before the DELETE.
    if cursor.rowcount == 0:
        raise HTTPException(status_code=404, detail=f"Task {task_id} not found")

    # Response with no body is returned explicitly since 204 must not include
    # a payload. Returning None would still serialize to a json null body
    return Response(status_code=204)
