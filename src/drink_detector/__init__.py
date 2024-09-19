from .config import Config
from .drink_detection import drink_detection
import json
from quart import Quart, g, render_template, request, url_for, send_from_directory, abort, make_response
import os
from datetime import datetime
import sqlite3
from dataclasses import dataclass, astuple, asdict
from asyncio import Lock, Queue, CancelledError, Task, Event
import asyncio
from typing import AsyncGenerator

PAGINATION_SIZE = 10

app = Quart(__name__)
app.config.from_object(Config)
Config.setup()

def connect_db():
    db_con = sqlite3.connect(app.config["DB"])
    db_con.row_factory = sqlite3.Row
    db_cur = db_con.cursor()
    db_cur.arraysize = PAGINATION_SIZE
    return (db_con, db_cur)

def get_db():
    if "db" not in g:
        (db_con, db_cur) = connect_db()
        g.db_con = db_con
        g.db_cur = db_cur

    return (g.db_con, g.db_cur)

def close_db():
    db = g.pop("db", None)
    if db is not None:
        db.close()

def init_db():
    (db_con, db_cur) = get_db()
    db_cur.execute(
        """CREATE TABLE IF NOT EXISTS captures (
            id INTEGER PRIMARY KEY,
            model TEXT NOT NULL,
            result TEXT NOT NULL,
            filename TEXT NOT NULL,
            created_at INTEGER NOT NULL
        )"""
    )
    db_con.commit()

class FeedBroker:
    def __init__(self) -> None:
        self.connections: set[Queue] = set()

    async def publish(self, message: str) -> None:
        for conn in self.connections:
            await conn.put(message)
        print("finish publish to %s clients" % len(self.connections))

    async def subscribe(self) -> AsyncGenerator[str, None]:
        conn = Queue()
        self.connections.add(conn)
        try:
            while True:
                yield await conn.get()
        finally:
            self.connections.remove(conn)

broker = FeedBroker()
update_check_cancel_event: Event = Event()
update_check_task: Task
UPDATE_RATE = 10

async def update_check():
    most_recent: int | None = None
    (db_con, db_cur) = connect_db()

    try:
        while True:
            print("update check")
            row = db_cur.execute("SELECT id, model, result, filename, created_at FROM captures ORDER BY created_at DESC LIMIT 1").fetchone()
            if most_recent is not None and row["id"] > most_recent:
                await broker.publish(json.dumps(asdict(process_row(row))))
            most_recent = row["id"]
            await asyncio.sleep(UPDATE_RATE)
    finally:
        print("Update checker stopping")
        db_con.close()

@app.while_serving
async def manage_update_check():
    app.add_background_task(update_check)
    yield
    update_check_cancel_event.set()

def capture():
    print("Starting in capture mode")
    (db_con, db_cur) = connect_db()
    drink_detection(db_con, db_cur, Config)

@dataclass
class CaptureRow:
    objects: list
    run: int
    model: str
    timestamp: str

def process_row(row) -> CaptureRow:
    result = json.loads(row["result"])
    objects = [{ "label": label, "score": float(score), "box": box, } for label, score, box in zip(result["labels"], result["scores"], result["boxes"])]
    objects.sort(key=lambda item: item["score"], reverse=True)
    return CaptureRow(
        objects=objects,
        run=row["created_at"],
        model=row["model"],
        timestamp=datetime.fromtimestamp(row["created_at"]).isoformat(sep=" ", timespec="seconds")
    )

@dataclass
class ServerSentEvent:
    data: str
    event: str | None = None
    id: int | None = None
    retry: int | None = None

    def encode(self) -> bytes:
        message = f"data: {self.data}"
        if self.event is not None:
            message = f"{message}\nevent: {self.event}"
        if self.id is not None:
            message = f"{message}\nid: {self.id}"
        if self.retry is not None:
            message = f"{message}\nretry: {self.retry}"
        message = f"{message}\n\n"
        return message.encode('utf-8')

@app.route("/feed")
async def feed():
    (db_con, db_cur) = get_db()
    row = db_cur.execute("SELECT model, result, filename, created_at FROM captures ORDER BY created_at DESC LIMIT 1").fetchone()
    if row is None:
        return render_template("empty_feed.html")
    objects = process_row(row)
    return await render_template("feed.html", **asdict(objects))

@app.route("/image/<run>")
async def image(run):
    (db_con, db_cur) = get_db()
    row = db_cur.execute("SELECT filename FROM captures WHERE created_at = ?", (run,)).fetchone()
    if "annotated" in request.args:
        dir = app.config["ANNO_DIR"]
    else:
        dir = app.config["ORIG_DIR"]
    return await send_from_directory(dir, row["filename"])

@app.route("/feed/sse")
async def feed_sse():
    if "text/event-stream" not in request.accept_mimetypes:
        abort(400)

    async def send_feed_updates():
        print("Subscribing to broker")
        async for msg in broker.subscribe():
            print("Message from broker")
            event = ServerSentEvent(msg)
            yield event.encode()

    res = await make_response(
        send_feed_updates(),
        {
            "Content-Type": "text/event-stream",
            "Cache-Control": "no-cache",
            "Transfer-Encoding": "chunked"
        }
    )
    res.timeout = None
    return res

@app.route("/history")
async def history():
    (db_con, db_cur) = get_db()
    rows = db_cur.execute("SELECT model, result, filename, created_at FROM captures ORDER BY created_at DESC LIMIT ?", (PAGINATION_SIZE,)).fetchmany()
    if len(rows) == 0:
        return render_template("empty_feed.html")
    captures = map(process_row, rows)
    return await render_template("history.html", captures=map(lambda cap: astuple(cap), captures))

def run() -> None:
    app.run(debug=True)
