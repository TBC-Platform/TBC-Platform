#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""A fake Wall-E, for testing the server without any hardware.

It speaks the real protocol: connects, says hello, streams an utterance as
20 ms frames, then collects the speech that comes back and writes it to a WAV
file you can play.

    # send a recording of yourself
    python3 scripts/simulate_robot.py --wav question.wav

    # or just prove the link and the wiring work
    python3 scripts/simulate_robot.py --silence 2.0

Anything the server asks the robot to do (faces, movement, camera) is printed,
so this doubles as a protocol debugger.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import os
import sys
from array import array
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import websockets  # noqa: E402

from walle import protocol as proto  # noqa: E402
from walle.audio import pcm_from_wav, resample_linear, wav_from_pcm  # noqa: E402

# A 1x1 grey JPEG, so --camera can answer a capture request without a camera.
TINY_JPEG = bytes.fromhex(
    "ffd8ffe000104a46494600010100000100010000ffdb004300ffffffffffffffffffffff"
    "ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff"
    "ffffffffffffffffffffffffffffffffffffc00011080001000103012200021101031101"
    "ffc4001f0000010501010101010100000000000000000102030405060708090a0bffc400"
    "b5100002010303020403050504040000017d01020300041105122131410613516107"
    "227114328191a1082342b1c11552d1f02433627282090a161718191a25262728292a3435"
    "363738393a434445464748494a535455565758595a636465666768696a73747576777879"
    "7a838485868788898a92939495969798999aa2a3a4a5a6a7a8a9aab2b3b4b5b6b7b8b9ba"
    "c2c3c4c5c6c7c8c9cad2d3d4d5d6d7d8d9dae1e2e3e4e5e6e7e8e9eaf1f2f3f4f5f6f7f8"
    "f9faffda0008010100003f00fbfeffd9"
)


def load_utterance(path: Path) -> array:
    data = path.read_bytes()
    samples, rate = pcm_from_wav(data)
    if rate != proto.AUDIO_SAMPLE_RATE:
        print(f"resampling {rate} Hz -> {proto.AUDIO_SAMPLE_RATE} Hz")
        samples = resample_linear(samples, rate, proto.AUDIO_SAMPLE_RATE)
    return samples


async def run(args: argparse.Namespace) -> int:
    scheme = "wss" if args.tls else "ws"
    url = f"{scheme}://{args.host}:{args.port}/ws"
    headers = {"X-Walle-Token": args.token, "X-Walle-Device": args.device}

    if args.wav:
        samples = load_utterance(Path(args.wav))
    else:
        samples = array("h", bytes(int(proto.AUDIO_SAMPLE_RATE * args.silence) * 2))

    print(f"connecting to {url} as {args.device}")
    async with websockets.connect(url, additional_headers=headers, max_size=2**20) as ws:
        await ws.send(proto.encode_json(
            proto.MSG_HELLO, dev=args.device, fw="sim-1.0.0",
            proto=proto.PROTO_VERSION, sr=proto.AUDIO_SAMPLE_RATE,
            caps=["mic", "speaker", "display", "camera", "tracks"],
        ))

        received = array("h")
        done = asyncio.Event()

        async def reader() -> None:
            async for message in ws:
                if isinstance(message, str):
                    msg = proto.decode_json(message)
                    kind = msg.get("t")
                    if kind == proto.MSG_HELLO_ACK:
                        print(f"server ready: {msg}")
                    elif kind == proto.MSG_FACE:
                        print(f"  [face]  {msg.get('e')}")
                    elif kind == proto.MSG_MOVE:
                        print(f"  [move]  {msg.get('cmd')} speed={msg.get('speed')} ms={msg.get('ms')}")
                    elif kind == proto.MSG_HEAD:
                        print(f"  [head]  {msg.get('deg')} degrees")
                    elif kind == proto.MSG_SAY_BEGIN:
                        print(f"  [say]   {msg.get('text')!r}")
                    elif kind == proto.MSG_SAY_END:
                        print("  [say]   finished")
                        done.set()
                    elif kind == proto.MSG_CAM:
                        print("  [cam]   capture requested")
                        await send_frame(ws)
                    elif kind == proto.MSG_ERROR:
                        print(f"  [error] {msg.get('msg')}")
                        done.set()
                    else:
                        print(f"  [?]     {msg}")
                else:
                    frame = proto.decode_bin(message)
                    if frame.type is proto.BinType.AUDIO_DOWN and frame.payload:
                        chunk = array("h")
                        chunk.frombytes(frame.payload)
                        received.extend(chunk)

        reader_task = asyncio.create_task(reader())

        # Stream the utterance in real time, exactly as the firmware does.
        print(f"streaming {len(samples) / proto.AUDIO_SAMPLE_RATE:.1f}s of audio")
        await ws.send(proto.encode_json(proto.MSG_UTT_BEGIN, sr=proto.AUDIO_SAMPLE_RATE))
        raw = samples.tobytes()
        for index, chunk in enumerate(proto.chunk_audio(raw)):
            await ws.send(proto.encode_bin(
                proto.BinType.AUDIO_UP, chunk,
                flags=proto.FLAG_FIRST if index == 0 else proto.FLAG_NONE, seq=index,
            ))
            if args.realtime:
                await asyncio.sleep(0.02)
        await ws.send(proto.encode_bin(proto.BinType.AUDIO_UP, b"", flags=proto.FLAG_LAST))
        await ws.send(proto.encode_json(
            proto.MSG_UTT_END,
            ms=int(len(samples) * 1000 / proto.AUDIO_SAMPLE_RATE),
            speech=not args.no_speech,
        ))

        try:
            await asyncio.wait_for(done.wait(), timeout=args.timeout)
        except asyncio.TimeoutError:
            print(f"timed out after {args.timeout}s waiting for a reply", file=sys.stderr)
            reader_task.cancel()
            return 1

        reader_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await reader_task

    if received:
        out = Path(args.out)
        out.write_bytes(wav_from_pcm(received, proto.AUDIO_SAMPLE_RATE))
        seconds = len(received) / proto.AUDIO_SAMPLE_RATE
        print(f"\nwrote {seconds:.1f}s of speech to {out}")
        print(f"play it with:  afplay {out}   (macOS)  /  aplay {out}   (Linux)")
    else:
        print("\nno audio came back")
    return 0


async def send_frame(ws) -> None:
    await ws.send(proto.encode_json(proto.MSG_CAM_META, len=len(TINY_JPEG), w=1, h=1))
    await ws.send(proto.encode_bin(
        proto.BinType.JPEG_UP, TINY_JPEG, flags=proto.FLAG_FIRST | proto.FLAG_LAST
    ))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=int(os.environ.get("WALLE_PORT", 8765)))
    parser.add_argument("--tls", action="store_true", help="connect with wss://")
    parser.add_argument("--token", default=os.environ.get("WALLE_AUTH_TOKEN", ""))
    parser.add_argument("--device", default="walle-sim")
    parser.add_argument("--wav", help="WAV file to send as the user's question")
    parser.add_argument("--silence", type=float, default=1.5,
                        help="seconds of silence to send when no --wav is given")
    parser.add_argument("--no-speech", action="store_true",
                        help="set the utt_end speech flag to false, as the device does on a false wake")
    parser.add_argument("--realtime", action="store_true",
                        help="pace the upload at 1x instead of blasting it")
    parser.add_argument("--out", default="reply.wav")
    parser.add_argument("--timeout", type=float, default=60.0)
    args = parser.parse_args()

    try:
        return asyncio.run(run(args))
    except KeyboardInterrupt:
        return 130
    except OSError as exc:
        print(f"could not connect: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
