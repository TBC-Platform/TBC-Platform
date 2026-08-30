#!/usr/bin/env bash
# SPDX-License-Identifier: MIT
#
# Downloads the models Wall-E needs into server/models/.
#
#   ./scripts/fetch_models.sh            # everything
#   ./scripts/fetch_models.sh whisper    # just one
#
# Total download is roughly 250 MB. Everything here is redistributable:
# Whisper models are MIT, Piper voices are MIT/CC-BY, the detector is Apache 2.
set -euo pipefail

cd "$(dirname "$0")/.."
MODELS_DIR="models"
mkdir -p "$MODELS_DIR"

info() { printf '\033[1;34m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m!!\033[0m %s\n' "$*" >&2; }

fetch() {
    local url="$1" dest="$2"
    if [ -s "$dest" ]; then
        info "already have $(basename "$dest")"
        return 0
    fi
    info "downloading $(basename "$dest")"
    # --fail so a 404 does not leave a zero byte file that later looks valid.
    if ! curl --fail --location --progress-bar --output "$dest.part" "$url"; then
        rm -f "$dest.part"
        warn "failed to download $url"
        return 1
    fi
    mv "$dest.part" "$dest"
}

fetch_whisper() {
    # base.en quantised to q5_1: ~57 MB, and the best accuracy-per-millisecond
    # point for short English commands. See docs/03-research-notes.md.
    fetch \
      "https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-base.en-q5_1.bin" \
      "$MODELS_DIR/ggml-base.en-q5_1.bin"

    # Optional: small.en is noticeably more accurate on accented speech and
    # still real time on an M-series Mac. Uncomment if base.en mishears you.
    # fetch \
    #   "https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-small.en-q5_1.bin" \
    #   "$MODELS_DIR/ggml-small.en-q5_1.bin"
}

fetch_piper() {
    local base="https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_US/lessac/medium"
    fetch "$base/en_US-lessac-medium.onnx"      "$MODELS_DIR/en_US-lessac-medium.onnx"
    fetch "$base/en_US-lessac-medium.onnx.json" "$MODELS_DIR/en_US-lessac-medium.onnx.json"
}

fetch_vision() {
    # COCO SSD MobileNet v1, quantised. The same model family TensorFlow Lite
    # Micro would run on the ESP32, executed server-side where it is 100x faster.
    fetch \
      "https://storage.googleapis.com/download.tensorflow.org/models/tflite/coco_ssd_mobilenet_v1_1.0_quant_2018_06_29.zip" \
      "$MODELS_DIR/coco_ssd.zip" || return 1

    if command -v unzip >/dev/null 2>&1; then
        unzip -o -q "$MODELS_DIR/coco_ssd.zip" -d "$MODELS_DIR"
        [ -f "$MODELS_DIR/detect.tflite" ] && mv -f "$MODELS_DIR/detect.tflite" "$MODELS_DIR/ssd_mobilenet_v1.tflite"
        [ -f "$MODELS_DIR/labelmap.txt" ] && mv -f "$MODELS_DIR/labelmap.txt" "$MODELS_DIR/coco_labels.txt"
        rm -f "$MODELS_DIR/coco_ssd.zip"
    else
        warn "unzip not found - extract $MODELS_DIR/coco_ssd.zip by hand"
    fi
}

target="${1:-all}"
case "$target" in
    whisper) fetch_whisper ;;
    piper)   fetch_piper ;;
    vision)  fetch_vision ;;
    all)
        fetch_whisper
        fetch_piper
        fetch_vision || warn "vision model failed; the robot will still talk, it just cannot see"
        ;;
    *) echo "usage: $0 [all|whisper|piper|vision]" >&2; exit 1 ;;
esac

info "done. Models are in $(pwd)/$MODELS_DIR"
ls -lh "$MODELS_DIR" 2>/dev/null || true
