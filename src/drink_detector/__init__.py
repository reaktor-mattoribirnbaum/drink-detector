import asyncio
import signal
from multiprocessing import freeze_support

from .config import Config
from .db import Db
from .server import app
from .tasks import drink_detection


def capture():
    print("Starting in capture mode")
    drink_detection.drink_detection(app.config)


def _sig_handler(*_: any) -> None:
    print("Shutting down server")
    app.feed_shutdown_event.set()
    if app.capture_loop_process is not None:
        app.capture_loop_stop.set()
        app.capture_loop_process.cancel()


def init_db():
    app.config.from_object(Config)
    db = Db(app.config["DB"])
    db._init_db_()

def load_config() -> None:
    app.config.from_object(Config())
    Config.setup()


def run() -> None:
    freeze_support()
    load_config()
    app.run(debug=True)


def serve() -> None:
    freeze_support()
    load_config()

    import hypercorn.config
    from hypercorn.asyncio import serve

    config = hypercorn.config.Config()
    config.bind = ["localhost:8080"]
    loop = asyncio.get_event_loop()
    loop.add_signal_handler(signal.SIGINT, _sig_handler)
    # shutdown_trigger must be set or the SIGINT handler
    # seems to get overwritten by Quart
    loop.run_until_complete(
        serve(app, config, shutdown_trigger=app.feed_shutdown_event.wait)
    )
