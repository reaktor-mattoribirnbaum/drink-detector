from .db import Db, CaptureCreatedBy
from transformers import AutoProcessor, AutoModelForZeroShotObjectDetection
import torch
from PIL import Image, ImageDraw, ImageFont, ImageColor
import cv2 as cv
from datetime import datetime, timedelta
from time import sleep
import sqlite3
import os
import os.path
import json


def open_capture_device(capture_device: int) -> cv.VideoCapture:
    cap = cv.VideoCapture(capture_device)
    if not cap.isOpened():
        print("Cannot open camera")
        exit()
    return cap

def capture_image(cap) -> Image:
    ret, frame = cap.read()
    if not ret:
        raise "Couldn't read from camera"
    # default color format for opencv is BGR for some reason
    frame = cv.cvtColor(frame, cv.COLOR_BGR2RGB)
    image = Image.fromarray(frame)
    return image

def process_image(image: Image, model, query: str, query_items, other_color, processor, device) -> (Image, dict):
    draw = ImageDraw.Draw(image)

    inputs = processor(images=image, text=query, return_tensors="pt").to(device)
    with torch.no_grad():
        outputs = model(**inputs)

    results = processor.post_process_grounded_object_detection(
        outputs,
        inputs.input_ids,
        box_threshold=0.3,
        text_threshold=0.3,
        target_sizes=[image.size[::-1]]
    )

    # only processed one image
    result = results[0]
    for score, label, box in zip(result["scores"], result["labels"], result["boxes"]):
        print(
                f"Detected {label} with confidence "
                f"{round(score.item(), 3)} at location {box[0]}, {box[1]} to {box[2]}, {box[3]}"
        )

    font = ImageFont.load_default()

    for score, label, box in zip(result["scores"], result["labels"], result["boxes"]):
        color = query_items.get(label, other_color)
        x, y, x2, y2 = tuple([round(i.item(), 2) for i in box])
        print(x, y, x2, y2)
        draw.rectangle((x, y, x2, y2), outline=color, width=1)
        text = "{}: {}%".format(label, round(score.item() * 100, 3))
        text_left, text_top, text_right, text_bottom = font.getbbox(text)
        text_width = text_right - text_left
        text_height = text_bottom - text_top
        draw.rectangle((x, y, x + text_width, y + text_height), fill=color)
        draw.text((x, y), text, fill="black")

    return (image, result)

def setup_model(config):
    query_items = dict(map(lambda item: tuple(item.split(":")[0:2]), config.QUERY.split(",")))
    query = " ".join(map(lambda key: "%s." % key, query_items.keys()))

    device = "cuda" if torch.cuda.is_available() else "cpu"

    processor = AutoProcessor.from_pretrained(config.MODEL)
    model = AutoModelForZeroShotObjectDetection.from_pretrained(config.MODEL).to(device)
    return (query_items, query, device, processor, model)

def save_results(db: Db, config, orig_image, image, result, last_start, created_by: CaptureCreatedBy) -> None:
    result = {
        "scores": result['scores'].tolist(),
        "labels": result['labels'],
        "boxes": result['boxes'].tolist()
    }
    print("Saving results")
    filename = "%s.png" % last_start.isoformat(timespec="seconds")

    orig_image.save(os.path.join(config.ORIG_DIR, filename))
    image.save(os.path.join(config.ANNO_DIR, filename))

    db.insert_capture(
        config.MODEL, json.dumps(result), filename, created_by, datetime.now().timestamp()
    )

def setup_and_process_image(orig_image_file: os.PathLike, config):
    orig_image = Image.open(orig_image_file)
    db = Db(config.DB)
    (query_items, query, device, processor, model) = setup_model(config)
    start = datetime.now()
    (image, result) = process_image(orig_image, model, query, query_items, config.OTHER_COLOR, processor, device)
    save_results(db, config, orig_image, image, result, start, CaptureCreatedBy.REQUEST)

def drink_detection(config):
    db = Db(config.DB)
    print("Opening camera")
    cap = open_capture_device(config.CAPTURE_DEVICE)
    print("Camera interface opened, setting up model")

    (query_items, query, device, processor, model) = setup_model(config)
    print("Model ready")

    print("Starting capture loop at rate of once per %s seconds" % config.RATE)

    while True:
        print("Capturing and processing")
        last_start = datetime.now()
        orig_image = capture_image(cap)
        (image, result) = process_image(orig_image.copy(), model, query, query_items, config.OTHER_COLOR, processor, device)

        save_results(db, config, orig_image, image, result, last_start, CaptureCreatedBy.LOOP)
        
        next = last_start + timedelta(seconds=config.RATE)
        rem = max((next - datetime.now()).seconds, 0)
        print("Finished, waiting until next start in %s seconds" % rem)
        sleep(rem)

