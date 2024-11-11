import asyncio
import json
import multiprocessing
from asyncio import Event
from concurrent.futures import ProcessPoolExecutor
from datetime import datetime
from typing import Optional
from uuid import UUID, uuid4

from quart import (
    Quart,
    Response,
    abort,
    g,
    render_template,
    request,
    send_from_directory,
)

from .broker import FeedBroker, ServerSentEvent, send_feed_updates, update_check
from .db import CaptureCreatedBy, CaptureType, Db
from .files import save_orig
from .tasks import drink_detection, similarity

app = Quart(__name__)
app.background_futures = set()


def get_db() -> Db:
    if "db" not in g:
        db = Db(app.config["DB"])
        g.db = db

    return g.db


def close_db():
    db = g.pop("db", None)
    if db is not None:
        db.close()


app.broker: FeedBroker = FeedBroker()
app.feed_shutdown_event: Event = Event()
app.update_now_event: Event = Event()
app.process_pool_executor: ProcessPoolExecutor = ProcessPoolExecutor()
app.process_pool_manager: multiprocessing.Manager = multiprocessing.Manager()
app.capture_loop_process: Optional[asyncio.Future] = None
app.capture_loop_stop: multiprocessing.Event = app.process_pool_manager.Event()


@app.before_serving
async def manage_update_check():
    app.add_background_task(
        update_check,
        app.config["DB"],
        app.broker,
        app.feed_shutdown_event,
        app.update_now_event,
    )


async def render(template_file, **kwargs):
    return await render_template(
        template_file,
        **kwargs,
        _nav=[
            ("feed", "Feed", []),
            ("history", "History", []),
            (
                "request_form",
                "Request",
                ["capture_request_accept", "similarity_request_accept"],
            ),
            ("stock", "Stock", [])
        ],
        _capture_task_active=app.capture_loop_process is not None and app.capture_loop_process.is_alive(),
    )


@app.route("/feed")
async def feed():
    db = get_db()
    capture = db.fetch_latest_capture()
    if capture is None:
        return await render("empty_feed.html")
    return await render("feed.html", capture=capture)


@app.route("/image/<run>", defaults={"ind": 0})
@app.route("/image/<run>/<int:ind>")
async def image(run, ind):
    db = get_db()
    if "annotated" in request.args:
        typ = CaptureType.ANNO
        dir = app.config["ANNO_DIR"]
    else:
        typ = CaptureType.ORIG
        dir = app.config["ORIG_DIR"]
    filename = db.fetch_image_for_capture(run, typ, ind)
    if filename is None:
        abort(404)
    return await send_from_directory(dir, filename)


@app.route("/feed/sse", defaults={"uuid":None})
@app.route("/feed/sse/<uuid:uuid>")
async def feed_sse(uuid: Optional[UUID]):
    return await send_feed_updates(app.broker, app.feed_shutdown_event, uuid)


@app.route("/history")
async def history():
    db = get_db()
    captures = db.fetch_captures()
    if len(captures) == 0:
        return await render("empty_feed.html")
    return await render("history.html", captures=captures)


@app.route("/request")
async def request_form():
    return await render(
        "request_form.html",
        obj_det_model=app.config["OBJ_DET_MODEL"],
        img_feat_model=app.config["IMG_FEAT_MODEL"]
    )


@app.route("/detection_request", methods=["POST"])
async def detection_request_accept():
    image = (await request.files)["image"]
    db = get_db()
    dt = datetime.now()
    try:
        file_id = await save_orig(db, app.config, image.stream, image.mimetype, dt)
        capture_id = db.create_capture_with_files(
            uuid4(),
            app.config["OBJ_DET_MODEL"],
            CaptureCreatedBy.REQUEST,
            dt,
            [file_id]
        )
    except OSError:
        abort(400)

    print("Starting image processing task")
    process_future = asyncio.get_event_loop().run_in_executor(
        app.process_pool_executor,
        drink_detection.setup_and_process_image,
        capture_id,
        file_id,
        app.config,
        dt
    )

    def on_done(future):
        print("Finished image processing task")
        app.update_now_event.set()
        app.background_futures.discard(future)

    process_future.add_done_callback(on_done)
    app.background_futures.add(process_future)
    return await render("detection_result.html"), 202


@app.route("/similarity_request", methods=["POST"])
async def similarity_request_accept():
    files = await request.files
    db = get_db()
    dt = datetime.now()
    image_1 = files["image_1"]
    image_2 = files["image_2"]
    try:
        img_1_id = await save_orig(db, app.config, image_1.stream, image_1.mimetype, dt, 1)
        img_2_id = await save_orig(db, app.config, image_2.stream, image_2.mimetype, dt, 2)
        uuid = uuid4()
        capture_id = db.create_capture_with_files(
            uuid,
            app.config["IMG_FEAT_MODEL"],
            CaptureCreatedBy.SIMILARITY,
            dt,
            [img_1_id, img_2_id]
        )
    except OSError:
        abort(400)

    process_future = asyncio.get_event_loop().run_in_executor(
        app.process_pool_executor,
        similarity.find_similarity,
        img_1_id,
        img_2_id,
        capture_id,
        app.config,
    )

    def on_done(future):
        print("Finished image similarity task")
        sse = ServerSentEvent(f"{future.result() * 100}%", "similarity")
        asyncio.create_task(app.broker.publish(sse, uuid))
        app.background_futures.discard(future)

    process_future.add_done_callback(on_done)
    app.background_futures.add(process_future)
    return await render("similarity_result.html", capture_id=capture_id, uuid=uuid), 202


@app.route("/capture_loop/on", methods=["PUT"])
async def capture_loop_on():
    if app.capture_loop_process is None or app.capture_loop_process.done():
        print("Starting capture loop")
        app.capture_loop_stop.clear()
        app.capture_loop_process = asyncio.get_event_loop().run_in_executor(
            app.process_pool_executor,
            drink_detection.drink_detection,
            app.config,
            app.capture_loop_stop
        )
        return Response(status=200)
    else:
        return Response(status=409)


@app.route("/capture_loop/off", methods=["PUT"])
async def capture_loop_off():
    if app.capture_loop_process is None or app.capture_loop_process.done():
        return Response(status=409)
    else:
        print("Stopping capture loop")
        app.capture_loop_stop.set()
        app.capture_loop_process.cancel()
        return Response(status=200)

@app.route("/stock")
async def stock():
    db = get_db()
    capture = db.fetch_latest_capture([CaptureCreatedBy.LOOP, CaptureCreatedBy.REQUEST])
    if capture is None:
        return await render("empty_feed.html")
    obj_counts = capture.object_counts()
    total = sum(obj_counts.values())
    obj_counts = [
        (app.config["STOCK_TYPES_BY_QUERY"][key], val)
        for key, val
        in obj_counts.items()
    ]
    rows = [
        { "title": st["name"], "amount": val, "categories": st["categories"] }
        for (st, val)
        in obj_counts
        if st is not None
    ]
    return await render(
        "stock.html",
        total=total,
        rows=rows,
        categories=list(set([cat for row in rows for cat in row["categories"]]))
    )

@app.route("/stock/search")
async def stock_search():
    query = request.args.get("q") or ""
    db = get_db()
    latest = db.fetch_latest_capture([CaptureCreatedBy.LOOP, CaptureCreatedBy.REQUEST])
    obj_counts = latest.object_counts().items()
    obj_counts = [(app.config["STOCK_TYPES_BY_QUERY"][key], val) for key, val in obj_counts]
    if latest is None:
        res = []
    else:
        unfiltered = [
            { "title": st["name"], "amount": val, "categories": st["categories"] }
            for (st, val)
            in obj_counts
            if st is not None
        ]
        res = [row for row in unfiltered if query in row["title"]]
    return json.dumps({
        "data": res,
        "categories": list(set([cat for row in unfiltered for cat in row["categories"]]))
    })
