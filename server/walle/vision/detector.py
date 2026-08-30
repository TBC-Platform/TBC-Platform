# SPDX-License-Identifier: MIT
"""Object detection on camera frames from the robot.

Runs a quantised SSD-MobileNet through the TensorFlow Lite *runtime* - the same
model family that TensorFlow Lite Micro would run on the ESP32 itself, but
executed here where there is real memory and a real CPU. That is the whole
architectural bet of this project restated in one module: the ESP32 is the eye,
the server is the visual cortex. An S3 can technically run a tiny person-detect
model at a couple of frames per second; the server does 300x300 COCO detection
in 20-40 ms and gives you eighty classes instead of one.

Optional dependencies: ``tflite-runtime`` (or full ``tensorflow``) and
``Pillow``. Without them, detection degrades to "camera works, robot cannot
name things" rather than failing.
"""

from __future__ import annotations

import io
import logging
import time
from dataclasses import dataclass
from pathlib import Path

from ..config import VisionConfig

log = logging.getLogger(__name__)


@dataclass(slots=True)
class Detection:
    label: str
    score: float
    # Normalised box, 0..1: (ymin, xmin, ymax, xmax) as TFLite emits it.
    box: tuple[float, float, float, float]

    @property
    def where(self) -> str:
        """Turns a box into words the robot can actually say."""
        _ymin, xmin, _ymax, xmax = self.box
        centre = (xmin + xmax) / 2
        if centre < 0.38:
            return "on my left"
        if centre > 0.62:
            return "on my right"
        return "right in front of me"


def describe(detections: list[Detection]) -> str:
    """Renders detections as a short phrase for the LLM prompt or direct speech.

    Duplicates are counted rather than listed, because "three chairs" is what a
    person would say and "a chair, a chair and a chair" is not.
    """
    if not detections:
        return "nothing I recognise"

    counts: dict[str, int] = {}
    for det in detections:
        counts[det.label] = counts.get(det.label, 0) + 1

    words = {2: "two", 3: "three", 4: "four", 5: "five"}
    parts = []
    for label, count in counts.items():
        if count == 1:
            article = "an" if label[0] in "aeiou" else "a"
            parts.append(f"{article} {label}")
        else:
            parts.append(f"{words.get(count, str(count))} {label}s")

    if len(parts) == 1:
        return parts[0]
    return ", ".join(parts[:-1]) + " and " + parts[-1]


class TfliteDetector:
    name = "tflite"

    def __init__(self, cfg: VisionConfig) -> None:
        self.cfg = cfg
        self._interpreter = None
        self._labels: list[str] = []
        self._input_size = (300, 300)
        self._quantised = True
        self._unavailable_reason: str | None = None

    # ---------------------------- model loading ----------------------------

    def _load(self) -> bool:
        if self._interpreter is not None:
            return True
        if self._unavailable_reason is not None:
            return False

        try:
            try:
                from tflite_runtime.interpreter import Interpreter  # type: ignore
            except ImportError:
                from tensorflow.lite.python.interpreter import Interpreter  # type: ignore
        except ImportError:
            self._unavailable_reason = (
                "neither tflite-runtime nor tensorflow is installed "
                "(pip install tflite-runtime)"
            )
            log.warning("vision disabled: %s", self._unavailable_reason)
            return False

        model_path = Path(self.cfg.model_path)
        if not model_path.is_file():
            self._unavailable_reason = f"model not found: {model_path}"
            log.warning("vision disabled: %s", self._unavailable_reason)
            return False

        interpreter = Interpreter(model_path=str(model_path), num_threads=2)
        interpreter.allocate_tensors()
        details = interpreter.get_input_details()[0]
        self._input_size = (details["shape"][2], details["shape"][1])  # (w, h)
        self._quantised = details["dtype"].__name__ == "uint8"
        self._interpreter = interpreter
        self._labels = _load_labels(Path(self.cfg.labels_path))
        log.info(
            "vision ready: %s, input %sx%s, %s labels",
            model_path.name, *self._input_size, len(self._labels),
        )
        return True

    # ------------------------------ inference ------------------------------

    def detect_sync(self, jpeg: bytes) -> list[Detection]:
        """Blocking inference. Call through ``asyncio.to_thread``."""
        if not self._load():
            return []
        try:
            from PIL import Image  # type: ignore
        except ImportError:
            if self._unavailable_reason is None:
                self._unavailable_reason = "Pillow is not installed (pip install Pillow)"
                log.warning("vision disabled: %s", self._unavailable_reason)
            return []

        started = time.monotonic()
        try:
            image = Image.open(io.BytesIO(jpeg)).convert("RGB")
        except Exception:
            log.warning("could not decode camera JPEG (%d bytes)", len(jpeg))
            return []

        resized = image.resize(self._input_size)
        interpreter = self._interpreter
        assert interpreter is not None

        input_index = interpreter.get_input_details()[0]["index"]
        # Build the tensor without NumPy at the call site: PIL gives us bytes,
        # and the interpreter wants an array-like of the right dtype. NumPy is
        # a hard dependency of every tflite build, so importing it here is free.
        import numpy as np  # noqa: PLC0415 - deliberately lazy

        array = np.asarray(resized, dtype=np.uint8)
        if not self._quantised:
            # Float models expect [-1, 1] with the standard MobileNet scaling.
            array = (array.astype(np.float32) - 127.5) / 127.5
        interpreter.set_tensor(input_index, np.expand_dims(array, axis=0))
        interpreter.invoke()

        detections = self._read_outputs(interpreter)
        detections.sort(key=lambda d: d.score, reverse=True)
        detections = detections[: self.cfg.max_results]
        log.info("vision: %d ms, %d objects: %s",
                 int((time.monotonic() - started) * 1000),
                 len(detections), describe(detections))
        return detections

    def _read_outputs(self, interpreter) -> list[Detection]:
        """Reads the four standard SSD post-processing outputs.

        Output *ordering* varies between exports of the same model, which is a
        classic source of "why does it think everything is a toaster". We
        identify tensors by shape instead of trusting the index order.
        """
        outputs = interpreter.get_output_details()
        boxes = classes = scores = None
        for detail in outputs:
            tensor = interpreter.get_tensor(detail["index"])
            shape = tuple(tensor.shape)
            if len(shape) == 3 and shape[2] == 4:
                boxes = tensor[0]
            elif len(shape) == 2 and boxes is not None and classes is None:
                classes = tensor[0]
            elif len(shape) == 2 and classes is not None and scores is None:
                scores = tensor[0]
        # Fall back to the documented order if shape sniffing was inconclusive.
        if boxes is None or classes is None or scores is None:
            try:
                boxes = interpreter.get_tensor(outputs[0]["index"])[0]
                classes = interpreter.get_tensor(outputs[1]["index"])[0]
                scores = interpreter.get_tensor(outputs[2]["index"])[0]
            except (IndexError, KeyError):
                log.warning("unexpected detector output layout; skipping frame")
                return []

        results: list[Detection] = []
        for i in range(len(scores)):
            score = float(scores[i])
            if score < self.cfg.min_score:
                continue
            class_id = int(classes[i])
            label = self._labels[class_id] if 0 <= class_id < len(self._labels) else f"object {class_id}"
            ymin, xmin, ymax, xmax = (float(v) for v in boxes[i])
            results.append(
                Detection(
                    label=label,
                    score=score,
                    box=(max(0.0, ymin), max(0.0, xmin), min(1.0, ymax), min(1.0, xmax)),
                )
            )
        return results

    async def detect(self, jpeg: bytes) -> list[Detection]:
        import asyncio

        return await asyncio.to_thread(self.detect_sync, jpeg)

    @property
    def unavailable_reason(self) -> str | None:
        return self._unavailable_reason


class NullDetector:
    """Used when vision is switched off; keeps the session code branch-free."""

    name = "none"
    unavailable_reason = "vision is disabled in the configuration"

    async def detect(self, jpeg: bytes) -> list[Detection]:
        return []


def _load_labels(path: Path) -> list[str]:
    """Reads a COCO labels file.

    Handles both formats in the wild: one label per line, and
    ``<index> <label>`` per line (which is sparse, hence the padding).
    """
    if not path.is_file():
        log.warning("labels file not found: %s (detections will be numbered)", path)
        return []
    labels: dict[int, str] = {}
    for lineno, raw in enumerate(path.read_text(encoding="utf-8").splitlines()):
        line = raw.strip()
        if not line:
            continue
        first, _, rest = line.partition(" ")
        if first.isdigit() and rest:
            labels[int(first)] = rest.strip()
        else:
            labels[lineno] = line
    if not labels:
        return []
    return [labels.get(i, f"object {i}") for i in range(max(labels) + 1)]
