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
from config import Config

# # sqlite database to store results
# DB_FILE = "drinks.db"
# # directory to store images
# IMAGE_OUT = "drinks_out"
# # number of video capture device, usually starts at 0
# CAPTURE_DEVICE = 0
# # seconds between captures
# CAPTURE_RATE = 60.0

# # dict of items to query for and the colors for their annotations
# QUERY_ITEMS = {
#     "a can": ImageColor.colormap["azure"],
#     "a bottle": ImageColor.colormap["fuchsia"],
#     "a juice box": ImageColor.colormap["tomato"]
# }
# # occasionally results other than the query are produced
# OTHER_COLOR = ImageColor.colormap["chocolate"]

def capture(db_con, db_cur):
    query_items = dict(map(lambda item: tuple(item.split(":")[0:2]), Config.QUERY.split(",")))
    query = " ".join(map(lambda key: "%s." % key, query_items.keys()))

    print("Opening camera")
    cap = cv.VideoCapture(Config.CAPTURE_DEVICE)
    if not cap.isOpened():
        print("Cannot open camera")
        exit()
    print("Camera interface opened, setting up model")

    device = "cuda" if torch.cuda.is_available() else "cpu"

    processor = AutoProcessor.from_pretrained(Config.MODEL)
    model = AutoModelForZeroShotObjectDetection.from_pretrained(Config.MODEL).to(device)
    print("Model ready")

    def capture_and_process(cap, query, query_items, other_color, processor, device):
        ret, frame = cap.read()
        if not ret:
            raise "Couldn't read from camera"
        # default color format for opencv is BGR for some reason
        frame = cv.cvtColor(frame, cv.COLOR_BGR2RGB)
        image = Image.fromarray(frame)
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

        return (frame, image, result)

    def capture_loop():
        while True:
            print("Capturing and processing")
            last_start = datetime.now()
            (frame, image, result) = capture_and_process(cap, query, query_items, Config.OTHER_COLOR, processor, device)
            result = {
                "scores": result['scores'].tolist(),
                "labels": result['labels'],
                "boxes": result['boxes'].tolist()
            }
            print("Saving results")
            filename = "%s.png" % last_start.isoformat(timespec="seconds")

            Image.fromarray(frame).save(os.path.join(Config.ORIG_DIR, filename))
            image.save(os.path.join(Config.ANNO_DIR, filename))

            db_cur.execute(
                "INSERT INTO captures (model, result, filename, created_at) VALUES (?, ?, ?, ?)",
                (Config.MODEL, json.dumps(result), filename, datetime.now().timestamp())
            )
            db_con.commit()
            next = last_start + timedelta(seconds=Config.RATE)
            rem = max((next - datetime.now()).seconds, 0)
            print("Finished, waiting until next start in %s seconds" % rem)
            sleep(rem)

    print("Starting capture loop at rate of once per %s seconds" % Config.RATE)
    capture_loop()

