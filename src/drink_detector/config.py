import os
from dataclasses import dataclass, field

from dotenv import dotenv_values

env = dotenv_values(".env")


@dataclass
class Config:
    DB = env.get("DRINKS_DB", "drinks.db")
    IMAGE_OUT = env.get("DRINKS_IMAGE_OUT", "drinks_out")
    CAPTURE_DEVICE = int(env.get("DRINKS_CAPTURE_DEVICE", "0"))
    RATE = int(env.get("DRINKS_CAPTURE_RATE", 60))
    QUERY = env.get("DRINKS_QUERY", "a can:azure,a bottle:fuchsia,a juice box:tomato")
    QUERY_ITEMS: dict[str, str] = field(init=False)
    OTHER_COLOR = env.get("DRINKS_OTHER_COLOR", "chocolate")
    OBJ_DET_MODEL = env.get("DRINKS_OBJ_DET_MODEL", "IDEA-Research/grounding-dino-base")
    IMG_FEAT_MODEL = env.get(
        "DRINKS_IMG_FEAT_MODEL", "google/vit-base-patch16-224-in21k"
    )
    OUT_DIR = os.path.join(os.getcwd(), IMAGE_OUT)
    ORIG_DIR = os.path.join(OUT_DIR, "orig")
    ANNO_DIR = os.path.join(OUT_DIR, "anno")

    def __post_init__(self):
        print("post init")
        self.QUERY_ITEMS = dict(
            map(lambda item: tuple(item.split(":")[0:2]), self.QUERY.split(","))
        )
    
    @staticmethod
    def setup():
        os.makedirs(Config.ORIG_DIR, exist_ok=True)
        os.makedirs(Config.ANNO_DIR, exist_ok=True)
