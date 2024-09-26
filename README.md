# Drink Detector

Identifies objects in an image using computer vision and displays results in a web frontend.

## Installation/Running

This project uses [Poetry](https://python-poetry.org/) for structure and dependency management, so please install it first. After that, to install dependencies run

```
poetry install
```

To start the web frontend, run

```
poetry run serve
```

From there, you can start the capture loop using the button on the frontend or submit images if your choosing on the Request page. You can also start the capture loop independently by running

```
poetry run capture
```

## Primary Technologies

### Server

* Python
* [asyncio](https://docs.python.org/3/library/asyncio.html)
* [Quart (asyncio-oriented web framework similar to Flask)](https://quart.palletsprojects.com/en/latest/)
* [transformers (for running models from HuggingFace)](https://huggingface.co/docs/transformers/index)
* [OpenCV (for camera capture)](https://pypi.org/project/opencv-python/)

### Client

* [Fomantic (open source extension to Semantic)](https://fomantic-ui.com/)
* [server-sent events](https://developer.mozilla.org/en-US/docs/Web/API/Server-sent_events)

## Sample dataset

Included is a sample dataset to be able to immediately explore the features of the web frontend. To setup, simply unpack `sample-drinks.zip` and output `drinks.db` and the `drinks_out` directory into the project root directory.
