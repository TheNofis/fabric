#!/usr/bin/env -S uv run --quiet --script
# /// script
# requires-python = "==3.12.*"
# dependencies = ["faster-whisper", "nvidia-cublas-cu12", "nvidia-cudnn-cu12"]
# ///
"""Voice input as JSON lines: listens to the mic, types each phrase into the focused field after a pause.

Runs in its own uv env (CTranslate2 has no wheels for the system Python). Emits
{"state": "loading" | "listening" | "speaking", "partial": str, "last": str, "device": str}.
`--check` runs the self-check without loading the model.
"""

from __future__ import annotations

import json
import os
import queue
import subprocess
import sys
import threading
import time

RATE = 16000
FRAME = RATE // 10  # 100 ms of s16 mono
SILENCE_RMS = 500  # calibration knob: raise if room noise starts phrases, lower if quiet speech is missed
PAUSE_MS = 700  # silence that ends a phrase
PARTIAL_MS = 800  # how often the in-progress phrase is re-transcribed for the overlay
MIN_SPEECH_MS = 300
MAX_PHRASE_S = 20  # force a phrase out during long monologues
# whisper invents these on silence/noise (trained on subtitles)
HALLUCINATIONS = ("продолжение следует", "субтитры", "редактор субтитров", "спасибо за просмотр", "thanks for watching", "thank you for watching")


def clean(text: str) -> str:
    text = " ".join(text.split())
    lowered = text.lower().strip(" .!?…")
    if not lowered or any(lowered.startswith(junk) for junk in HALLUCINATIONS):
        return ""
    return text


def emit(**value: str) -> None:
    print(json.dumps(value, ensure_ascii=False), flush=True)


def cuda_env() -> None:
    """CTranslate2 dlopens cuBLAS/cuDNN by soname: point the loader at the pip wheels, then re-exec."""
    if os.environ.get("VOICE_CUDA_ENV"):
        return
    try:
        import nvidia.cublas.lib
        import nvidia.cudnn.lib
    except ImportError:
        return
    paths = [*nvidia.cublas.lib.__path__, *nvidia.cudnn.lib.__path__, os.environ.get("LD_LIBRARY_PATH", "")]
    os.execve(sys.executable, [sys.executable, *sys.argv], {**os.environ, "VOICE_CUDA_ENV": "1", "LD_LIBRARY_PATH": ":".join(filter(None, paths))})


def load_model():
    from faster_whisper import WhisperModel

    try:
        # int8: float16 is slow on Pascal (GTX 10xx)
        return WhisperModel("small", device="cuda", compute_type="int8"), "cuda"
    except Exception as error:  # noqa: BLE001 - no GPU / broken CUDA libs: CPU still works
        print(f"cuda unavailable, falling back to cpu: {error}", file=sys.stderr)
        return WhisperModel("base", device="cpu", compute_type="int8"), "cpu"


def main() -> None:
    import numpy as np

    cuda_env()
    emit(state="loading")
    model, device = load_model()

    frames: queue.Queue[bytes] = queue.Queue()
    recorder = subprocess.Popen(
        ["pw-record", "--raw", "--rate", str(RATE), "--channels", "1", "--format", "s16", "-"],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
    )

    def read() -> None:
        # own thread: transcription blocks the main loop, and pw-record must not stall on a full pipe
        while chunk := recorder.stdout.read(FRAME * 2):
            frames.put(chunk)
        frames.put(b"")

    threading.Thread(target=read, daemon=True).start()

    def transcribe(audio, final: bool, prompt: str) -> str:
        segments, _ = model.transcribe(
            audio.astype(np.float32) / 32768,
            beam_size=5 if final else 1,
            temperature=0,  # no fallback re-decodes: on noise they take 15 s+ and produce garbage anyway
            condition_on_previous_text=False,
            initial_prompt=prompt or None,
            vad_filter=False,
        )
        # repetitive loops (high compression) and non-speech are what whisper outputs on noise
        return clean(" ".join(s.text for s in segments if s.compression_ratio < 2.4 and s.no_speech_prob < 0.6))

    emit(state="listening", device=device)
    phrase: list = []
    speech_ms = silence_ms = 0
    last_partial = 0.0
    previous = ""
    while chunk := frames.get():
        samples = np.frombuffer(chunk, dtype=np.int16)
        # ponytail: energy VAD; swap for silero (faster_whisper.vad) if noise keeps triggering phrases
        loud = np.sqrt(np.mean(samples.astype(np.float32) ** 2)) > SILENCE_RMS
        if not phrase and not loud:
            continue
        phrase.append(samples)
        if loud:
            speech_ms += 100
            silence_ms = 0
        else:
            silence_ms += 100
        ended = silence_ms >= PAUSE_MS or len(phrase) >= MAX_PHRASE_S * 10
        if ended:
            audio = np.concatenate(phrase)
            text = transcribe(audio, True, previous) if speech_ms >= MIN_SPEECH_MS else ""
            phrase, speech_ms, silence_ms = [], 0, 0
            if text:
                subprocess.run(["xdotool", "type", "--clearmodifiers", "--delay", "0", "--", text + " "], check=False)
                previous = text
            emit(state="listening", last=previous, device=device)
        elif frames.empty() and time.monotonic() - last_partial > PARTIAL_MS / 1000 and speech_ms >= MIN_SPEECH_MS:
            emit(state="speaking", partial=transcribe(np.concatenate(phrase), False, previous), last=previous, device=device)
            last_partial = time.monotonic()
    recorder.wait()


def self_check() -> None:
    assert clean("  Привет,   как дела?  ") == "Привет, как дела?"
    assert clean("Продолжение следует...") == ""
    assert clean(" Thanks for watching!") == ""
    assert clean("...") == ""
    assert clean("Субтитры сделал DimaTorzok") == ""
    assert clean("Open the PR, пожалуйста") == "Open the PR, пожалуйста"
    print("ok")


if __name__ == "__main__":
    self_check() if "--check" in sys.argv else main()
