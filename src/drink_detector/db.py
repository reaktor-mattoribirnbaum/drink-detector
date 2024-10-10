import enum
import json
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional, Self
from uuid import UUID, uuid4

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


class CaptureType(enum.Enum):
    ORIG = "orig",
    ANNO = "anno"

    def __new__(cls, *args, **kwargs):
        obj = object.__new__(cls)
        obj._value_ = args[0]
        return obj

    @staticmethod
    def adapt(capture_type):
        return capture_type._value_

    @staticmethod
    def convert(capture_type):
        return CaptureType(capture_type.decode("UTF-8"))


@dataclass
class CaptureRow:
    """A completed capture, combining a row from captures and capture_results"""
    id: int
    uuid: str
    model: str
    result: object
    filenames: list[str]
    created_by: CaptureCreatedBy
    created_at: datetime
    timestamp: str = field(init=False)
    filename_divider: str = ":"

    def __post_init__(self):
        self.timestamp = datetime.fromtimestamp(self.created_at).isoformat(
            sep=" ", timespec="seconds"
        )

    @staticmethod
    def row_factory(cursor: sqlite3.Cursor, row: tuple) -> Self:
        fields = [column[0] for column in cursor.description]
        row = {key: value for key, value in zip(fields, row)}

    @staticmethod
    def from_row(row) -> Self:
        match row["created_by"]:
            case CaptureCreatedBy.LOOP | CaptureCreatedBy.REQUEST:
                cls = DetectionRow
            case CaptureCreatedBy.SIMILARITY:
                cls = SimilarityRow
            case _:
                cls = CaptureRow
        return cls(
            row["id"],
            row["uuid"],
            row["model"],
            json.loads(row["result"]) if row["result"] is not None else None,
            row["filenames"].split(CaptureRow.filename_divider),
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


sqlite3.register_adapter(CaptureCreatedBy, CaptureCreatedBy.adapt)
sqlite3.register_converter("capture_created_by", CaptureCreatedBy.convert)
sqlite3.register_adapter(CaptureType, CaptureType.adapt)
sqlite3.register_converter("capture_type", CaptureType.convert)

class Db:
    def __init__(self, db_url, pagination_size=PAGINATION_SIZE):
        self.pagination_size = pagination_size
        db_con = sqlite3.connect(db_url, detect_types=sqlite3.PARSE_DECLTYPES)
        db_con.row_factory = sqlite3.Row
        self.con = db_con

    def __new_cur__(self) -> sqlite3.Cursor:
        cur = self.con.cursor()
        cur.arraysize = self.pagination_size
        return cur

    def _init_db_(self) -> None:
        with self.con:
            cur = self.__new_cur__()
            cur.execute(
                """
                    CREATE TABLE IF NOT EXISTS captures (
                        id INTEGER PRIMARY KEY,
                        uuid TEXT NOT NULL UNIQUE,
                        model TEXT NOT NULL,
                        created_by capture_created_by NOT NULL,
                        created_at INTEGER NOT NULL
                    )
                """
            )
            cur.execute(
                """
                    CREATE TABLE IF NOT EXISTS capture_results (
                        id INTEGER PRIMARY KEY,
                        capture_id INTEGER NOT NULL UNIQUE,
                        result TEXT NOT NULL,
                        created_at INTEGER NOT NULL,
                        FOREIGN KEY(capture_id) REFERENCES captures(id)
                    )
                """
            )
            cur.execute(
                """
                    CREATE TABLE IF NOT EXISTS files (
                        id INTEGER PRIMARY KEY,
                        filename TEXT NOT NULL,
                        type capture_type NOT NULL,
                        created_at INTEGER NOT NULL,
                        UNIQUE(filename, type)
                    )
                """
            )
            cur.execute(
                """
                    CREATE TABLE IF NOT EXISTS capture_files (
                        capture_id INTEGER NOT NULL,
                        file_id INTEGER NOT NULL,
                        created_at INTEGER NOT NULL,
                        FOREIGN KEY(capture_id) REFERENCES captures(id),
                        FOREIGN KEY(file_id) REFERENCES files(id)
                    )
                """
            )

    def close(self) -> None:
        self.con.close()

    def __fetch_captures__(self, limit: int) -> list[CaptureRow]:
        return list(map(
            CaptureRow.from_row,
            self.__new_cur__().execute(
                f"""
                    SELECT c.id, c.uuid, c.model, r.result, c.created_by, r.created_at,
                        GROUP_CONCAT(f.filename, "{CaptureRow.filename_divider}")
                        AS filenames
                    FROM captures c
                    LEFT JOIN capture_files cf ON c.id = cf.capture_id
                    LEFT JOIN files f ON cf.file_id = f.id
                    INNER JOIN capture_results r ON c.id = r.capture_id
                    GROUP BY c.id
                    ORDER BY c.created_at DESC LIMIT ?
                """,
                (limit,),
            ).fetchmany()))
        

    def fetch_captures(self, limit: int=PAGINATION_SIZE) -> list[CaptureRow]:
        return self.__fetch_captures__(limit)

    def fetch_latest_capture(self) -> Optional[CaptureRow]:
        rows = self.__fetch_captures__(1)
        if len(rows) != 1:
            return None
        else:
            return rows[0]

    def create_in_progress_capture(
        self,
        uuid: UUID,
        model: str,
        created_by: CaptureCreatedBy,
        created_at: int
    ) -> int:
        with self.con:
            cur = self.__new_cur__()
            cur.execute(
                """
                    INSERT INTO CAPTURES (uuid, model, created_by, created_at)
                    VALUES (?, ?, ?, ?)
                """,
                (uuid, model, created_by, created_at)
            )
            return cur.lastrowid

    def complete_capture(
        self,
        capture_id: int,
        result: object,
        created_at: int,
        files: Optional[list[int]]=None
    ) -> int:
        if files is None:
            files = []
        with self.con:
            cur = self.__new_cur__()
            cur.execute(
                """
                    INSERT INTO capture_results (capture_id, result, created_at)
                    VALUES (?, ?, ?)
                """,
                (capture_id, json.dumps(result), created_at),
            )
            capture_id = cur.lastrowid
            for file_id in files:
                self.link_file(capture_id, file_id)
            return capture_id

    def create_capture_with_files(
        self,
        uuid: UUID,
        model: str,
        created_by: CaptureCreatedBy,
        created_at: int,
        files: Optional[list[int]]=None
    ) -> int:
        if files is None:
            files = []
        capture_id = self.create_in_progress_capture(
            uuid.hex, model, created_by, created_at
        )
        for file_id in files:
            self.link_file(capture_id, file_id)
        return capture_id

    def create_completed_capture(
        self,
        model: str,
        created_by: CaptureCreatedBy,
        created_at: int,
        result: object,
        files: Optional[list[int]]=None
    ) -> int:
        if files is None:
            files = []
        uuid = uuid4().hex
        capture_id = self.create_in_progress_capture(
            uuid, model, created_by, created_at
        )
        self.complete_capture(capture_id, result, created_at)
        for file_id in files:
            self.link_file(capture_id, file_id)
        return capture_id

    def insert_file(self, filename: str, type: CaptureType, created_at: int) -> int:
        with self.con:
            cur = self.__new_cur__()
            cur.execute(
                """
                    INSERT INTO files (filename, type, created_at)
                    VALUES (?, ?, ?)
                """,
                (filename, type, created_at)
            )
            return cur.lastrowid

    def link_file(self, capture_id: int, file_id: int) -> None:
        with self.con:
            self.__new_cur__().execute(
                """
                    INSERT INTO capture_files (capture_id, file_id, created_at)
                    VALUES (?, ?, ?)
                """,
                (capture_id, file_id, datetime.now().timestamp())
            )

    def fetch_image_name(self, file_id: int) -> Optional[str]:
        with self.con:
            row_opt = self.__new_cur__().execute(
                """
                    SELECT filename
                    FROM files
                    WHERE id = ?
                """,
                (file_id,)
            ).fetchone()
            if row_opt is None:
                return None
            return row_opt["filename"]

    def fetch_image_for_capture(self, capture_id: int, type: CaptureType, ind: int) -> Optional[str]:
        with self.con:
            rows = self.__new_cur__().execute(
                """
                    SELECT f.filename
                    FROM files f
                    LEFT JOIN capture_files cf ON f.id = cf.file_id
                    LEFT JOIN captures c ON cf.capture_id = c.id
                    WHERE c.id = ? AND f.type = ?
                    ORDER BY f.created_at DESC
                """,
                (capture_id, type)
            ).fetchmany()
            if len(rows) <= ind:
                return None
            return rows[ind]["filename"]

    # def fetch_image(self, created_at) -> Optional[list[str]]:
    #     # skip the usual row factory
    #     row_opt = self.cur.execute(
    #         """SELECT filenames
    #            FROM captures
    #            WHERE created_at = ?""",
    #         (created_at,),
    #     ).fetchone()
    #     if row_opt is not None:
    #         return json.loads(row_opt["filenames"])
    #     return None
