import asyncio
import multiprocessing
import os
import os.path
from datetime import datetime, timedelta
from io import BytesIO
from uuid import uuid4

import cv2 as cv
import torch
from PIL import Image, ImageDraw, ImageFont
from transformers import AutoModelForZeroShotObjectDetection, AutoProcessor

from drink_detector.db import CaptureCreatedBy, Db
from drink_detector.files import save_anno, save_raw_orig

from . import DEVICE

IMG_FMT = "PNG"
IMG_EXT = ".png"

def open_capture_device(capture_device: int) -> cv.VideoCapture:
    cap = cv.VideoCapture(capture_device)
    if not cap.isOpened():
        print("Cannot open camera")
        exit()
    return cap


def capture_image(cap) -> Image:
    ret, frame = cap.read()
    if not ret:
        raise Exception("Couldn't read from camera")
    # default color format for opencv is BGR for some reason
    frame = cv.cvtColor(frame, cv.COLOR_BGR2RGB)
    image = Image.fromarray(frame)
    return image


def process_image(
    image: Image, model, query: str, query_items, other_color, processor, device
) -> (Image, dict):
    draw = ImageDraw.Draw(image)

    inputs = processor(images=image, text=query, return_tensors="pt").to(device)
    with torch.no_grad():
        outputs = model(**inputs)

    results = processor.post_process_grounded_object_detection(
        outputs,
        inputs.input_ids,
        box_threshold=0.3,
        text_threshold=0.3,
        target_sizes=[image.size[::-1]],
    )

    # only processed one image
    result = results[0]
    scores_labels_boxes = list(zip(result["scores"], result["labels"], result["boxes"]))
    if len(scores_labels_boxes) == 0:
        print("No objects detected")
    else:
        for score, label, box in scores_labels_boxes:
            print(
                f"Detected {label} with confidence "
                f"{round(score.item(), 3)} at location "
                f"{box[0]}, {box[1]} to {box[2]}, {box[3]}"
            )

    font = ImageFont.load_default()

    for score, label, box in zip(result["scores"], result["labels"], result["boxes"]):
        color = query_items.get(label, other_color)
        x, y, x2, y2 = tuple([round(i.item(), 2) for i in box])
        print(x, y, x2, y2)
        draw.rectangle((x, y, x2, y2), outline=color, width=1)
        text = f"{label}: {round(score.item() * 100, 3)}%"
        text_left, text_top, text_right, text_bottom = font.getbbox(text)
        text_width = text_right - text_left
        text_height = text_bottom - text_top
        draw.rectangle((x, y, x + text_width, y + text_height), fill=color)
        draw.text((x, y), text, fill="black")

    return (image, result)


def setup_model(config):
    query_items = dict(
        map(lambda item: tuple(item.split(":")[0:2]), config["QUERY"].split(","))
    )
    query = " ".join(map(lambda key: f"{key}.", query_items.keys()))

    processor = AutoProcessor.from_pretrained(config["OBJ_DET_MODEL"])
    model = AutoModelForZeroShotObjectDetection.from_pretrained(
        config["OBJ_DET_MODEL"]
    ).to(DEVICE)
    return (query_items, query, DEVICE, processor, model)


def extract_results(result: dict) -> dict:
    return {
        "scores": result["scores"].tolist(),
        "labels": result["labels"],
        "boxes": result["boxes"].tolist(),
    }
    
   
def save_results(
    db: Db,
    config,
    capture_id,
    image,
    ext,
    result,
    last_start,
    created_by: CaptureCreatedBy
) -> None:
    result = extract_results(result)
    print("Saving object detection results")
    file_id = save_anno(db, config, image, ext, last_start)

    capture_id = db.complete_capture(
        capture_id,
        result,
        datetime.now().timestamp(),
        [file_id]
    )


def setup_and_process_image(capture_id: int, file_id: int, config, dt: datetime):
    db = Db(config["DB"])
    filename = db.fetch_image_name(file_id)
    if filename is None:
        raise Exception(f"couldn't find file: {file_id}")
    orig_image = Image.open(os.path.join(config["ORIG_DIR"], filename))
    ext = os.path.splitext(filename)[1]
    (query_items, query, device, processor, model) = setup_model(config)
    (image, result) = process_image(
        orig_image.copy(),
        model,
        query,
        query_items,
        config["OTHER_COLOR"],
        processor,
        device,
    )
    save_results(
        db,
        config,
        capture_id,
        image,
        ext,
        result,
        dt,
        CaptureCreatedBy.REQUEST
    )


def drink_detection(config, stop_event: multiprocessing.Event):
    async def run():
        try:
            db = Db(config["DB"])
            print("Opening camera")
            cap = open_capture_device(config["CAPTURE_DEVICE"])
            print("Camera interface opened, setting up model")

            if stop_event.is_set():
                return
            (query_items, query, device, processor, model) = setup_model(config)
            print("Model ready")

            if stop_event.is_set():
                return
            print(f"Starting capture loop at rate of once per {config["RATE"]} seconds")

            while True:
                if stop_event.is_set():
                    return
                print("Capturing and processing")
                last_start = datetime.now()
                orig_image = capture_image(cap)
                if stop_event.is_set():
                    return
                (image, result) = process_image(
                    orig_image.copy(),
                    model,
                    query,
                    query_items,
                    config["OTHER_COLOR"],
                    processor,
                    device,
                )
                if stop_event.is_set():
                    return

                with BytesIO() as orig_bytes:
                    orig_image.save(orig_bytes, IMG_FMT)
                    orig_bytes.seek(0)
                    orig_file_id = await save_raw_orig(db, config, orig_bytes, IMG_EXT, last_start)
                if stop_event.is_set():
                    return

                result = extract_results(result)
                print("Saving object detection results")
                file_id = save_anno(db, config, image, IMG_EXT, last_start)
                db.create_completed_capture(
                    config["OBJ_DET_MODEL"],
                    CaptureCreatedBy.LOOP,
                    last_start,
                    result,
                    [orig_file_id, file_id]
                )

                next = last_start + timedelta(seconds=config["RATE"])
                rem = max((next - datetime.now()).seconds, 0)
                print(f"Finished, waiting until next start in {rem} seconds")
                stop_event.wait(rem)
        except asyncio.CancelledError:
            print("Capture loop task cancelled")
        except Exception as e:
            print(f"Exception raised in capture loop: {e}")
        finally:
            print("Ending capture loop")
    asyncio.run(run())
