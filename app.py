from config import Config
import json
from flask import Flask, g, render_template, request, url_for, send_from_directory, request
import os
from datetime import datetime
import sqlite3

def create_app(test_config=None):
    Config.setup()
    app = Flask(__name__)

    def get_db():
        if "db" not in g:
            db_con = sqlite3.connect(Config.DB)
            db_con.row_factory = sqlite3.Row
            db_cur = db_con.cursor()
            g.db_con = db_con
            g.db_cur = db_cur

        return (g.db_con, g.db_cur)

    def close_db():
        db = g.pop("db", None)
        if db is not None:
            db.close()

    @app.cli.command("init-db")
    def init_db():
        (db_con, db_cur) = get_db()
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

    @app.cli.command("capture")
    def capture():
        pass

    @app.route("/feed")
    def feed():
        (db_con, db_cur) = get_db()
        row = db_cur.execute("SELECT model, result, filename, created_at FROM captures LIMIT 1").fetchone()
        if row is None:
            return render_template("empty_feed.html")
        result = json.loads(row["result"])
        objects = [{ "label": label, "score": float(score), "box": box, } for label, score, box in zip(result["labels"], result["scores"], result["boxes"])]
        objects.sort(key=lambda item: item["score"], reverse=True)
        return render_template(
            "feed.html",
            run=row["created_at"],
            model=row["model"],
            objects=objects,
            timestamp=datetime.fromtimestamp(row["created_at"]).isoformat(sep=" ", timespec="seconds")
        )

    @app.route("/image/<run>")
    def image(run):
        print('run', run)
        (db_con, db_cur) = get_db()
        row = db_cur.execute("SELECT filename FROM captures WHERE created_at = ?", (run,)).fetchone()
        if "annotated" in request.args:
            dir = Config.ANNO_DIR
        else:
            dir = Config.ORIG_DIR
        return send_from_directory(dir, row["filename"])

    return app