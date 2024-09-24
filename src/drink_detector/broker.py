from .db import Db
import asyncio

UPDATE_RATE = 10

class FeedBroker:
    def __init__(self) -> None:
        self.connections: set[asyncio.Queue] = set()

    async def publish(self, message: str) -> None:
        for conn in self.connections:
            await conn.put(message)

    def subscribe(self) -> asyncio.Queue:
        conn = asyncio.Queue()
        self.connections.add(conn)
        return conn

    def unsubscribe(self, conn: asyncio.Queue) -> None:
        try:
            self.connections.remove(conn)
        except KeyError:
            print("Tried unsubscribing with unknown queue!")


async def update_check(db_url: str, broker: FeedBroker, feed_shutdown_event: asyncio.Event, update_rate=UPDATE_RATE):
    most_recent: int
    db = Db(db_url)
    shutdown_wait_task = asyncio.create_task(feed_shutdown_event.wait())

    try:
        row = db.fetch_latest_capture()
        if row is not None:
            most_recent = row["created_at"]
        else:
            most_recent = 0

        while True:
            print("Checking for feed updates...")
            row = db.fetch_latest_capture()
            if row is not None:
                if row["created_at"] > most_recent:
                    print("Update found, publishing")
                    await broker.publish(row["created_at"])
                most_recent = row["created_at"]
            await asyncio.wait((shutdown_wait_task,), timeout=update_rate)
            if feed_shutdown_event.is_set():
                break
    finally:
        print("Update checker stopping")
        db.close()
