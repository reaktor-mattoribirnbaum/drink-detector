import mimetypes
import os
from datetime import datetime
from tempfile import NamedTemporaryFile
from typing import BinaryIO, Optional

from aiofiles import open
from PIL import Image

from .db import CaptureType, Db


async def async_pipe(in_io: BinaryIO, out_path: os.PathLike, buffer_size: int = 16384) -> int:
    written = 0
    async with open(out_path, "wb") as file_:
        data = in_io.read(buffer_size)
        while data != b"":
            written += await file_.write(data)
            data = in_io.read(buffer_size)
    return written

async def save_raw_orig(
    db: Db,
    config,
    f: BinaryIO,
    ext: str,
    dt: Optional[datetime] = None,
    ind: Optional[int] = None
) -> int:
    dt = dt or datetime.now()
    ts = dt.timestamp()

    with NamedTemporaryFile() as out_temp:
        written = await async_pipe(f, out_temp.name)
        if written == 0:
            raise OSError("input file was empty")

        fmt = f"{ts}{ext}" if ind is None else f"{ts}_{ind}{ext}"

        os.rename(out_temp.name, os.path.join(config["ORIG_DIR"], fmt))

    return db.insert_file(fmt, CaptureType.ORIG, datetime.now().timestamp())

async def save_orig(
    db: Db,
    config,
    f: BinaryIO,
    mimetype: str,
    dt: Optional[datetime] = None,
    ind: Optional[int] = None
) -> int:
    ext = mimetypes.guess_extension(mimetype)
    if ext is None:
        raise Exception(f"unknown mime type: {mimetype}")
    return await save_raw_orig(db, config, f, ext, dt, ind)

def save_anno(
    db: Db,
    config,
    image: Image,
    ext: str,
    dt: Optional[datetime],
    ind: Optional[int] = None
) -> int:
    dt = dt or datetime.now()
    ts = dt.timestamp()

    fmt = f"{ts}{ext}" if ind is None else f"{ts}_{ind}{ext}"

    image.save(os.path.join(config["ANNO_DIR"], fmt))

    return db.insert_file(fmt, CaptureType.ANNO, datetime.now().timestamp())
    
