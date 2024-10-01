import asyncio
import signal

from .config import Config
from .server import app
from .tasks import drink_detection


def capture():
    print("Starting in capture mode")
    drink_detection.drink_detection(app.config)


def _sig_handler(*_: any) -> None:
    print("Shutting down server")
    app.feed_shutdown_event.set()
    if app.capture_loop_task is not None:
        app.capture_loop_task.cancel()


def process_pool_stopper() -> None:
    app.process_pool_executor.shutdown()


def run() -> None:
    app.config.from_object(Config)
    Config.setup()
    app.run(debug=True)


def serve() -> None:
    app.config.from_object(Config)
    Config.setup()

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
