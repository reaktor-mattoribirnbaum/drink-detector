import sqlite3

PAGINATION_SIZE = 10

def connect_db(db_url: str, pagination_size=PAGINATION_SIZE) -> (sqlite3.Connection, sqlite3.Cursor):
    db_con = sqlite3.connect(db_url)
    db_con.row_factory = sqlite3.Row
    db_cur = db_con.cursor()
    db_cur.arraysize = pagination_size
    return (db_con, db_cur)

def init_db(db_con: sqlite3.Connection, db_cur: sqlite3.Cursor) -> None:
    db_cur.execute(
        """CREATE TABLE IF NOT EXISTS captures (
            id INTEGER PRIMARY KEY,
            model TEXT NOT NULL,
            result TEXT NOT NULL,
            filename TEXT NOT NULL,
            created_at INTEGER NOT NULL
        )"""
    )
    db_con.commit()
