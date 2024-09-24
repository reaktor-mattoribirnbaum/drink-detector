import sqlite3
import enum
import json
from dataclasses import dataclass, asdict, astuple
from datetime import datetime

PAGINATION_SIZE = 10


class CaptureCreatedBy(enum.Enum):
    LOOP = "capture_loop", "Capture Loop", "primary", "cog icon"
    REQUEST = "process_request", "Process Request", "secondary", "file upload icon"
    OTHER = "", "Unknown", "grey", "question circle icon"

    def __new__(cls, *args, **kwargs):
        obj = object.__new__(cls)
        obj._value_ = args[0]
        return obj

    def __init__(self, _: str, title: str, label_type: str, label_class: str):
        self._title_ = title
        self._label_type_ = label_type
        self._label_class_ = label_class

    @enum.property
    def title(self):
        return self._title_

    @enum.property
    def label_type(self):
        return self._label_type_

    @enum.property
    def label_class(self):
        return self._label_class_

    @classmethod
    def _missing_(cls, value):
        return CaptureCreatedBy.OTHER

    @staticmethod
    def adapt(created_by):
        return created_by._value_

    @staticmethod
    def convert(created_by):
        return CaptureCreatedBy(created_by.decode("UTF-8"))


@dataclass
class CaptureRow:
    objects: list
    run: int
    model: str
    created_by: str
    timestamp: str


def process_row(row) -> CaptureRow:
    result = json.loads(row["result"])
    objects = [{ "label": label, "score": float(score), "box": box, } for label, score, box in zip(result["labels"], result["scores"], result["boxes"])]
    objects.sort(key=lambda item: item["score"], reverse=True)
    return CaptureRow(
        objects=objects,
        run=row["created_at"],
        model=row["model"],
        created_by=row["created_by"],
        timestamp=datetime.fromtimestamp(row["created_at"]).isoformat(sep=" ", timespec="seconds")
    )


class Db():
    def __init__(self, db_url, pagination_size=PAGINATION_SIZE):
        sqlite3.register_adapter(CaptureCreatedBy, CaptureCreatedBy.adapt)
        sqlite3.register_converter("capture_created_by", CaptureCreatedBy.convert)
        db_con = sqlite3.connect(db_url, detect_types=sqlite3.PARSE_DECLTYPES)
        db_con.row_factory = sqlite3.Row
        db_cur = db_con.cursor()
        db_cur.arraysize = pagination_size
        self.con = db_con
        self.cur = db_cur

    def _init_db_(self) -> None:
        self.cur.execute(
            """CREATE TABLE IF NOT EXISTS captures (
                id INTEGER PRIMARY KEY,
                model TEXT NOT NULL,
                result TEXT NOT NULL,
                filename TEXT NOT NULL,
                created_by capture_created_by NOT NULL,
                created_at INTEGER NOT NULL
            )"""
        )
        self.con.commit()

    def close(self) -> None:
        self.cur.close()
        self.con.close()

    def fetch_captures(self, limit=PAGINATION_SIZE) -> sqlite3.Row:
        return self.cur.execute("SELECT id, model, result, filename, created_by, created_at FROM captures ORDER BY created_at DESC LIMIT ?", (limit,)).fetchmany()

    def fetch_captures_processed(self, limit=PAGINATION_SIZE) -> list[tuple]:
        rows = self.fetch_captures(limit)
        return list(map(astuple, map(process_row, rows)))
    
    def fetch_latest_capture(self) -> sqlite3.Row:
        return self.cur.execute("SELECT id, model, result, filename, created_by, created_at FROM captures ORDER BY created_at DESC LIMIT 1").fetchone()

    def fetch_latest_capture_processed(self) -> dict:
        row = self.fetch_latest_capture()
        return asdict(process_row(row)) if row is not None else None

    def insert_capture(self, model, result, filename, created_by, created_at) -> None:
        self.cur.execute(
            "INSERT INTO captures (model, result, filename, created_by, created_at) VALUES (?, ?, ?, ?, ?)",
            (model, result, filename, created_by, created_at)
        )
        self.con.commit()

    def fetch_image(self, created_at) -> sqlite3.Row:
        return self.cur.execute("SELECT filename FROM captures WHERE created_at = ?", (created_at,)).fetchone()

