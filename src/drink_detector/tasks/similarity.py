import os
from datetime import datetime

import torch
from PIL import Image
from torch.nn.functional import cosine_similarity
from transformers import pipeline

from drink_detector.db import CaptureCreatedBy, Db

from . import DEVICE


def setup_model(config):
    return pipeline(
        task="image-feature-extraction",
        model=config["IMG_FEAT_MODEL"],
        device=DEVICE,
        pool=True,
    )


def process_images(pipe, img_1, img_2):
    outputs = pipe([img_1, img_2])
    return cosine_similarity(torch.Tensor(outputs[0]), torch.Tensor(outputs[1]), dim=1)


def save_results(db: Db, config, img_1, img_2, result):
    result = {"similarity": result}
    print("Saving similarity results")
    now = datetime.now()
    now_fmt = now.isoformat(timespec="seconds")

    img_1.save(os.path.join(config["ORIG_DIR"]), f"{now_fmt}_1.png")
    img_2.save(os.path.join(config["ORIG_DIR"]), f"{now_fmt}_2.png")

    db.insert_capture(
        config["IMG_FEAT_MODEL"],
        result,
        [img_1, img_2],
        CaptureCreatedBy.SIMILARITY,
        now.timestamp(),
    )


def find_similiarity(img_1_name, img_2_name, config):
    img_1 = Image.open(img_1_name)
    img_2 = Image.open(img_2_name)
    pipe = setup_model(config)
    db = Db(config["DB"])
    result = process_images(pipe, img_1, img_2).item()
    save_results(db, config, img_1, img_2, result)
    return result
