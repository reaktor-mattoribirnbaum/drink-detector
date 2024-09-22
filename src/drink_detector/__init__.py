from .config import Config
from . import drink_detection, db
import json
from quart import Quart, Request, g, render_template, request, url_for, send_from_directory, abort, make_response
import os
from datetime import datetime
import sqlite3
from PIL import Image
from dataclasses import dataclass, astuple, asdict
from asyncio import Lock, Queue, CancelledError, Task, Event
import asyncio
import signal
from tempfile import NamedTemporaryFile
from concurrent.futures import ProcessPoolExecutor
from typing import AsyncGenerator


app = Quart(__name__)
app.config.from_object(Config)
Config.setup()
app.image_process_futures = set()

def get_db():
    if "db" not in g:
        (db_con, db_cur) = db.connect_db(app.config["DB"])
        g.db_con = db_con
        g.db_cur = db_cur

    return (g.db_con, g.db_cur)

def close_db():
    db = g.pop("db", None)
    if db is not None:
        db.close()

def init_db():
    (db_con, db_cur) = db.connect_db()
    db.init_db(db_con, db_cur)

class FeedBroker:
    def __init__(self) -> None:
        self.connections: set[Queue] = set()

    async def publish(self, message: str) -> None:
        for conn in self.connections:
            await conn.put(message)

    def subscribe(self) -> Queue:
        conn = Queue()
        self.connections.add(conn)
        return conn

    def unsubscribe(self, conn: Queue) -> None:
        try:
            self.connections.remove(conn)
        except KeyError:
            print("Tried unsubscribing with unknown queue!")

broker = FeedBroker()
feed_shutdown_event = Event()
process_pool_executor = ProcessPoolExecutor()
UPDATE_RATE = 10

async def update_check():
    most_recent: int | None = None
    (db_con, db_cur) = db.connect_db(app.config["DB"])
    shutdown_wait_task = asyncio.create_task(feed_shutdown_event.wait())

    try:
        while True:
            print("Checking for feed updates...")
            row = db_cur.execute("SELECT id, model, result, filename, created_at FROM captures ORDER BY created_at DESC LIMIT 1").fetchone()
            if most_recent is not None and row["id"] > most_recent:
                print("Update found, publishing")
                await broker.publish(json.dumps(asdict(process_row(row))))
            most_recent = row["id"]
            await asyncio.wait((shutdown_wait_task,), timeout=UPDATE_RATE)
            if feed_shutdown_event.is_set():
                break
    finally:
        print("Update checker stopping")
        db_con.close()

@app.before_serving
async def manage_update_check():
    app.add_background_task(update_check)

def capture():
    print("Starting in capture mode")
    drink_detection.drink_detection(Config)

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

async def return_sse(gen: AsyncGenerator[str, None]) -> Request:
    if "text/event-stream" not in request.accept_mimetypes:
        abort(400)

    res = await make_response(
        gen(),
        {
            "Content-Type": "text/event-stream",
            "Cache-Control": "no-cache",
            "Transfer-Encoding": "chunked"
        }
    )
    return res

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
    async def send_feed_updates():
        print("Subscribing to feed")
        subscription = broker.subscribe()
        subscription_task: Task
        cancel_event_task: Task
        try:
            while True:
                subscription_task = asyncio.create_task(subscription.get())
                cancel_event_task = asyncio.create_task(feed_shutdown_event.wait())
                done, pending = await asyncio.wait((subscription_task, cancel_event_task), return_when=asyncio.FIRST_COMPLETED)
                for task in pending:
                    task.cancel()
                if feed_shutdown_event.is_set():
                    print("Stopping feed due to shutdown")
                    return
                for task in done:
                    if task is subscription_task:
                        print("Sending feed update")
                        event = ServerSentEvent(await task)
                        yield event.encode()
        except asyncio.CancelledError:
            print("Feed connection closed")
        finally:
            broker.unsubscribe(subscription)
            subscription_task.cancel()
            cancel_event_task.cancel()

    return await return_sse(send_feed_updates)

@app.route("/history")
async def history():
    (db_con, db_cur) = get_db()
    rows = db_cur.execute("SELECT model, result, filename, created_at FROM captures ORDER BY created_at DESC LIMIT ?", (db.PAGINATION_SIZE,)).fetchmany()
    if len(rows) == 0:
        return render_template("empty_feed.html")
    captures = map(process_row, rows)
    return await render_template("history.html", captures=map(lambda cap: astuple(cap), captures))

@app.route("/capture_request")
async def capture_request():
    return await render_template("request_form.html", model=app.config["MODEL"])

@app.route("/capture_request", methods=["POST"])
async def capture_request_accept():
    out_temp = NamedTemporaryFile()
    image = (await request.files)["image"]
    await image.save(out_temp.name)
    (db_con, db_cur) = get_db()
    print("Starting image processing task")
    process_future = asyncio.get_event_loop().run_in_executor(
        process_pool_executor,
        drink_detection.setup_and_process_image,
        out_temp.name,
        Config
    )
    def on_done(future):
        print("Finished image processing task")
        out_temp.close()
        app.image_process_futures.discard(future)
    process_future.add_done_callback(on_done)
    app.image_process_futures.add(process_future)
    return await render_template("request_accepted.html"), 202

def _sig_handler(*_: any) -> None:
    print("Shutting down server")
    feed_shutdown_event.set()

def process_pool_stopper() -> None:
    process_pool_executor.shutdown()

def run() -> None:
    app.run(debug=True)

def serve() -> None:
    import hypercorn.config
    from hypercorn.asyncio import serve

    config = hypercorn.config.Config()
    config.bind = ["localhost:8080"]
    loop = asyncio.get_event_loop()
    loop.add_signal_handler(signal.SIGINT, _sig_handler)
    # shutdown_trigger must be set or the SIGINT handler seems to get overwritten by Quart
    loop.run_until_complete(serve(app, config, shutdown_trigger=feed_shutdown_event.wait))
