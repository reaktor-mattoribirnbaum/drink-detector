import json
import os
from dataclasses import InitVar, dataclass, field

from dotenv import dotenv_values
from jsonschema import validate

env = dotenv_values(".env")


@dataclass
class Config:
    DB = env.get("DRINKS_DB", "drinks.db")
    IMAGE_OUT = env.get("DRINKS_IMAGE_OUT", "drinks_out")
    CAPTURE_DEVICE = int(env.get("DRINKS_CAPTURE_DEVICE", "0"))
    RATE = int(env.get("DRINKS_CAPTURE_RATE", 60))
    # QUERY = env.get("DRINKS_QUERY", "a can:azure,a bottle:fuchsia,a juice box:tomato")
    # QUERY_ITEMS: dict[str, str] = field(init=False)
    STOCK_TYPES_FILE = env.get("DRINKS_STOCK_TYPES_FILE", "stock_types.json")
    STOCK_TYPES: dict = field(init=False)
    STOCK_TYPES_BY_QUERY: dict = field(init=False)
    stock_types_schema: InitVar[dict] = {
        "type": "array",
        "items": {
            "type": "object",
            "properties": {
                "name": { "type": "string" },
                "query": { "type": "string" },
                "color": { "type": "string" },
                "categories": {
                    "type": "array",
                    "items": { "type": "string" }
                }
            }
        }
    }
    OTHER_COLOR = env.get("DRINKS_OTHER_COLOR", "chocolate")
    OBJ_DET_MODEL = env.get("DRINKS_OBJ_DET_MODEL", "IDEA-Research/grounding-dino-base")
    IMG_FEAT_MODEL = env.get(
        "DRINKS_IMG_FEAT_MODEL", "google/vit-base-patch16-224-in21k"
    )
    OUT_DIR = os.path.join(os.getcwd(), IMAGE_OUT)
    ORIG_DIR = os.path.join(OUT_DIR, "orig")
    ANNO_DIR = os.path.join(OUT_DIR, "anno")

    def __post_init__(self, stock_types_schema):
        print("post init")
        try:
            with open(Config.STOCK_TYPES_FILE) as f:
                data = json.load(f)
                validate(data, stock_types_schema)
                self.STOCK_TYPES = data
                self.STOCK_TYPES_BY_QUERY = dict([[st["query"], st] for st in data])
        except OSError as e:
            print(f"Failed to read stock types file: {e}")
            raise e

    @staticmethod
    def setup():
        os.makedirs(Config.ORIG_DIR, exist_ok=True)
        os.makedirs(Config.ANNO_DIR, exist_ok=True)
