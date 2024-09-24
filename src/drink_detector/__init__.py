from .config import Config
from . import drink_detection
from .db import Db
from .broker import FeedBroker, update_check
from .sse import send_feed_updates
import json
from quart import Quart, Request, Response, g, render_template, request, url_for, send_from_directory, abort, make_response
import os
from datetime import datetime
import sqlite3
from PIL import Image
from asyncio import Lock, CancelledError, Task, Event
import asyncio
import signal
from tempfile import NamedTemporaryFile
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass, astuple, asdict
from typing import AsyncGenerator, Optional


app = Quart(__name__)
app.config.from_object(Config)
Config.setup()
app.image_process_futures = set()

def get_db() -> Db:
    if "db" not in g:
        db = Db(app.config["DB"])
        g.db = db

    return g.db

def close_db():
    db = g.pop("db", None)
    if db is not None:
        db.close()

def init_db():
    db = Db(Config.DB)
    db._init_db_()


app.broker: FeedBroker = FeedBroker()
app.feed_shutdown_event: Event = Event()
app.process_pool_executor: ProcessPoolExecutor = ProcessPoolExecutor()
app.capture_loop_task: Optional[Task] = None


@app.before_serving
async def manage_update_check():
    app.add_background_task(update_check, app.config["DB"], app.broker, app.feed_shutdown_event)

def capture():
    print("Starting in capture mode")
    drink_detection.drink_detection(Config)

async def render(template_file, **kwargs):
    return await render_template(
        template_file,
        **kwargs,
        _nav=[
            ("feed", "Feed", []), ("history", "History", []),
            ("capture_request", "Request", ["capture_request_accept"])
        ],
        _capture_task_active=app.capture_loop_task is not None
    )

@app.route("/feed")
async def feed():
    db = get_db()
    capture = db.fetch_latest_capture_processed()
    if capture is None:
        return await render_template("empty_feed.html")
    return await render("feed.html", **capture)

@app.route("/image/<run>")
async def image(run):
    db = get_db()
    row = db.fetch_image(run)
    if "annotated" in request.args:
        dir = app.config["ANNO_DIR"]
    else:
        dir = app.config["ORIG_DIR"]
    return await send_from_directory(dir, row["filename"])

@app.route("/feed/sse")
async def feed_sse():
    return await send_feed_updates(app.broker, app.feed_shutdown_event)

@app.route("/history")
async def history():
    db = get_db()
    captures = db.fetch_captures_processed()
    if len(captures) == 0:
        return await render("empty_feed.html")
    return await render("history.html", captures=captures)

@app.route("/capture_request")
async def capture_request():
    return await render("request_form.html", model=app.config["MODEL"])

@app.route("/capture_request", methods=["POST"])
async def capture_request_accept():
    out_temp = NamedTemporaryFile()
    image = (await request.files)["image"]
    await image.save(out_temp.name)
    print("Starting image processing task")
    process_future = asyncio.get_event_loop().run_in_executor(
        app.process_pool_executor,
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
    return await render("request_accepted.html"), 202

@app.route("/capture_loop/on", methods=["PUT"])
async def capture_loop_on():
    if app.capture_loop_task is None:
        print("Starting capture loop")
        app.capture_loop_task = asyncio.get_event_loop().run_in_executor(
            app.process_pool_executor,
            drink_detection.drink_detection,
            Config
        )
        return Response(status=200)
    else:
        return Response(status=409)

@app.route("/capture_loop/off", methods=["PUT"])
async def capture_loop_off():
    if app.capture_loop_task is None:
        return Response(status=409)
    else:
        print("Stopping capture loop")
        app.capture_loop_task.cancel()
        return Response(status=200)

def _sig_handler(*_: any) -> None:
    print("Shutting down server")
    app.feed_shutdown_event.set()
    if app.capture_loop_task is not None:
        app.capture_loop_task.cancel()

def process_pool_stopper() -> None:
    app.process_pool_executor.shutdown()

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
    loop.run_until_complete(serve(app, config, shutdown_trigger=app.feed_shutdown_event.wait))
