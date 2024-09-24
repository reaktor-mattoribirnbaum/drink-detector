from .broker import FeedBroker
from dataclasses import dataclass, astuple, asdict
from quart import Request, abort, request, make_response
from typing import AsyncGenerator
from functools import partial
import asyncio


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


async def _send_feed_updates(broker: FeedBroker, feed_shutdown_event: asyncio.Event):
    print("Subscribing to feed")
    subscription = broker.subscribe()
    subscription_task: asyncio.Task
    cancel_event_task: asyncio.Task
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


def send_feed_updates(broker: FeedBroker, feed_shutdown_event: asyncio.Event):
    return return_sse(partial(_send_feed_updates, broker, feed_shutdown_event))
