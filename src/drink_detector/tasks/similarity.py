import os
from datetime import datetime

import torch
from PIL import Image
from torch.nn.functional import cosine_similarity
from transformers import pipeline

from drink_detector.db import Db

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


def save_results(db: Db, config, img_1_id: int, img_2_id: int, capture_id: int, result):
    result = {"similarity": result}
    print("Saving similarity results")

    capture_id = db.complete_capture(
        capture_id,
        result,
        datetime.now().timestamp()
    )


def find_similarity(img_1_id: int, img_2_id: int, capture_id: int, config) -> float:
    db = Db(config["DB"])
    img_1_name = db.fetch_image_name(img_1_id)
    img_2_name = db.fetch_image_name(img_2_id)
    full_img_1_name = os.path.join(config["ORIG_DIR"], img_1_name)
    full_img_2_name = os.path.join(config["ORIG_DIR"], img_2_name)
    img_1 = Image.open(full_img_1_name)
    img_2 = Image.open(full_img_2_name)

    pipe = setup_model(config)
    result = process_images(pipe, img_1, img_2).item()
    save_results(db, config, img_1_id, img_2_id, capture_id, result)
    return result
