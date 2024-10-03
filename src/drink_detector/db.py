import enum
import json
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional, Self

PAGINATION_SIZE = 10


class CaptureCreatedBy(enum.Enum):
    LOOP = "capture_loop", "Capture Loop", "olive", "cog"
    REQUEST = "detection_request", "Detection Request", "green", "file upload"
    SIMILARITY = "similarity_request", "Similarity Request", "orange", "balance scale"
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
    model: str
    result: object
    filenames: list[str]
    created_by: CaptureCreatedBy
    created_at: datetime
    timestamp: str = field(init=False)

    def __post_init__(self):
        self.timestamp = datetime.fromtimestamp(self.created_at).isoformat(
            sep=" ", timespec="seconds"
        )

    @staticmethod
    def row_factory(cursor: sqlite3.Cursor, row: tuple) -> Self:
        fields = [column[0] for column in cursor.description]
        row = {key: value for key, value in zip(fields, row)}

        match row.get("created_by"):
            case CaptureCreatedBy.LOOP | CaptureCreatedBy.REQUEST:
                cls = DetectionRow
            case CaptureCreatedBy.SIMILARITY:
                cls = SimilarityRow
            case _:
                cls = CaptureRow
        return cls(
            row["model"],
            json.loads(row["result"]),
            json.loads(row["filenames"]),
            row["created_by"],
            row["created_at"],
        )


@dataclass
class DetectionRow(CaptureRow):
    objects: list = field(init=False)

    def __post_init__(self):
        super().__post_init__()
        self.objects = [
            {
                "label": label,
                "score": float(score),
                "box": box,
            }
            for label, score, box in zip(
                self.result["labels"], self.result["scores"], self.result["boxes"]
            )
        ]


@dataclass
class SimilarityRow(CaptureRow):
    similarity: float = field(init=False)

    def __post_init__(self):
        super().__post_init__()
        self.similarity = self.result["similarity"]


class Db:
    def __init__(self, db_url, pagination_size=PAGINATION_SIZE):
        self.pagination_size = pagination_size
        sqlite3.register_adapter(CaptureCreatedBy, CaptureCreatedBy.adapt)
        sqlite3.register_converter("capture_created_by", CaptureCreatedBy.convert)
        db_con = sqlite3.connect(db_url, detect_types=sqlite3.PARSE_DECLTYPES)
        db_con.row_factory = CaptureRow.row_factory
        db_cur = db_con.cursor()
        self.__init_cur__(db_cur)
        self.con = db_con
        self.cur = db_cur

    def __init_cur__(self, cur: sqlite3.Cursor) -> None:
        cur.arraysize = self.pagination_size

    def _init_db_(self) -> None:
        self.cur.execute(
            """CREATE TABLE IF NOT EXISTS captures (
                id INTEGER PRIMARY KEY,
                model TEXT NOT NULL,
                result TEXT NOT NULL,
                filenames TEXT NOT NULL,
                created_by capture_created_by NOT NULL,
                created_at INTEGER NOT NULL
            )"""
        )
        self.con.commit()

    def close(self) -> None:
        self.cur.close()
        self.con.close()

    def fetch_captures(self, limit=PAGINATION_SIZE) -> list[CaptureRow]:
        return self.cur.execute(
            """SELECT id, model, result, filenames, created_by, created_at
               FROM captures
               ORDER BY created_at DESC LIMIT ?""",
            (limit,),
        ).fetchmany()

    def fetch_latest_capture(self) -> CaptureRow:
        return self.cur.execute(
            """SELECT id, model, result, filenames, created_by, created_at
               FROM captures
               ORDER BY created_at DESC LIMIT 1"""
        ).fetchone()

    def insert_capture(
        self,
        model: str,
        result: object,
        filenames: list[str],
        created_by: CaptureCreatedBy,
        created_at: int,
    ) -> None:
        self.cur.execute(
            """INSERT INTO captures (model, result, filenames, created_by, created_at)
               VALUES (?, ?, ?, ?, ?)""",
            (model, json.dumps(result), json.dumps(filenames), created_by, created_at),
        )
        self.con.commit()

    def fetch_image(self, created_at) -> Optional[list[str]]:
        # skip the usual row factory
        cur = self.con.cursor()
        self.__init_cur__(cur)
        cur.row_factory = sqlite3.Row
        row_opt = cur.execute(
            """SELECT filenames
               FROM captures
               WHERE created_at = ?""",
            (created_at,),
        ).fetchone()
        if row_opt is not None:
            return json.loads(row_opt["filenames"])
        return None
