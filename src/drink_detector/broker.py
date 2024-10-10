import asyncio
from dataclasses import dataclass
from functools import partial
from typing import AsyncGenerator, Optional
from uuid import UUID

from quart import Request, abort, make_response, request

from .db import Db

UPDATE_RATE = 10


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
        return message


class FeedBroker:
    def __init__(self) -> None:
        self.connections: dict[asyncio.Queue[ServerSentEvent], Optional[UUID]] = dict()

    async def publish(self, event: ServerSentEvent, target: Optional[UUID] = None) -> None:
        for conn, uuid in self.connections.items():
            if target is None or uuid == target:
                await conn.put(event.encode())

    def subscribe(self, uuid: Optional[UUID] = None) -> asyncio.Queue:
        conn = asyncio.Queue()
        self.connections[conn] = uuid
        return conn

    def unsubscribe(self, conn: asyncio.Queue) -> None:
        try:
            del self.connections[conn]
        except KeyError as err:
            raise Exception("Tried unsubscribing with unknown queue!") from err


async def update_check(
    db_url: str,
    broker: FeedBroker,
    feed_shutdown_event: asyncio.Event,
    update_now_event: asyncio.Event,
    update_rate=UPDATE_RATE,
):
    most_recent: int
    db = Db(db_url)
    shutdown_wait_task = asyncio.create_task(feed_shutdown_event.wait())
    update_now_task = asyncio.create_task(update_now_event.wait())

    try:
        row = db.fetch_latest_capture()
        most_recent = row.created_at if row is not None else 0

        while True:
            print("Checking for feed updates...")
            row = db.fetch_latest_capture()
            if row is not None:
                if row.created_at > most_recent:
                    print("Update found, publishing")
                    await broker.publish(ServerSentEvent(row.created_at))
                most_recent = row.created_at
            await asyncio.wait(
                (shutdown_wait_task, update_now_task),
                timeout=update_rate,
                return_when=asyncio.FIRST_COMPLETED,
            )
            if update_now_event.is_set():
                update_now_event.clear()
                update_now_task = asyncio.create_task(update_now_event.wait())
            if feed_shutdown_event.is_set():
                break
    finally:
        print("Update checker stopping")
        db.close()


async def return_sse(gen: AsyncGenerator[str, None]) -> Request:
    if "text/event-stream" not in request.accept_mimetypes:
        abort(400)

    res = await make_response(
        gen(),
        {
            "Content-Type": "text/event-stream",
            "Cache-Control": "no-cache",
            "Transfer-Encoding": "chunked",
        },
    )
    return res


async def _send_feed_updates(
    broker: FeedBroker, feed_shutdown_event: asyncio.Event, uuid: Optional[UUID] = None
):
    print(f"Subscribing to feed{f' ({uuid})' if uuid is not None else ''}")
    subscription = broker.subscribe(uuid)
    subscription_task: asyncio.Task
    cancel_event_task: asyncio.Task
    try:
        while True:
            subscription_task = asyncio.create_task(subscription.get())
            cancel_event_task = asyncio.create_task(feed_shutdown_event.wait())
            done, pending = await asyncio.wait(
                (subscription_task, cancel_event_task),
                return_when=asyncio.FIRST_COMPLETED,
            )
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


def send_feed_updates(
    broker: FeedBroker, feed_shutdown_event: asyncio.Event, uuid: Optional[UUID] = None
):
    return return_sse(partial(_send_feed_updates, broker, feed_shutdown_event, uuid))
