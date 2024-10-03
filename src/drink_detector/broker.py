import asyncio
from typing import Optional
from uuid import UUID

from .db import Db

UPDATE_RATE = 10


class FeedBroker:
    def __init__(self) -> None:
        self.connections: dict[asyncio.Queue, Optional[UUID]] = dict()

    async def publish(self, message: str, target: Optional[UUID] = None) -> None:
        for conn in self.connections:
            if target is None or self.connections[conn] == target:
                await conn.put(message)

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
                    await broker.publish(row.created_at)
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
