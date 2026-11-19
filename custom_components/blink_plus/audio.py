"""Speak an audio file through a camera's open live session (``blink_plus.play_audio``)."""

import asyncio
import time

import av

from .live_http import adts_duration
from .microphone import AACEncoder

# Like a talk request: one minute at most.
MAX_AUDIO_SECONDS = 60
MAX_FILE_BYTES = 8 * 1024 * 1024
PCM_BYTES_PER_SECOND = 16000 * 2


def encode_file(path):
    """Decode any file PyAV reads into 16 kHz mono AAC-LC ADTS frames (blocking)."""
    encoder = AACEncoder()
    resampler = av.AudioResampler(format="s16", layout="mono", rate=16000)
    frames = []
    pcm_bytes = 0
    limit = MAX_AUDIO_SECONDS * PCM_BYTES_PER_SECOND
    with av.open(path) as container:
        if not container.streams.audio:
            raise ValueError("The file has no audio")
        for decoded in container.decode(audio=0):
            for converted in resampler.resample(decoded):
                pcm = bytes(converted.planes[0])[: converted.samples * 2]
                if pcm_bytes + len(pcm) > limit:
                    pcm = pcm[: limit - pcm_bytes]
                pcm_bytes += len(pcm)
                if pcm:
                    frames.extend(encoder.encode(pcm))
                if pcm_bytes >= limit:
                    return frames + encoder.finish()
        for converted in resampler.resample(None):
            pcm = bytes(converted.planes[0])[: converted.samples * 2]
            pcm = pcm[: max(0, limit - pcm_bytes)]
            pcm_bytes += len(pcm)
            if pcm:
                frames.extend(encoder.encode(pcm))
    return frames + encoder.finish()


async def speak(relay, frames):
    """Send ADTS frames in real time through the open live session.

    Uses the same speaker sequence and per-camera talk lock as the talk endpoint
    and the dashboard microphone. Returns the seconds spoken.
    """
    stream = relay.stream
    if stream is None:
        raise RuntimeError("Open live view before playing audio")
    if relay.talk_lock.locked():
        raise RuntimeError("Someone is already talking to this camera")
    spoken = 0.0
    async with relay.talk_lock:
        await relay.start_talk()
        try:
            clock = None
            for frame in frames:
                now = time.monotonic()
                if clock is None or clock < now - 0.5:
                    clock = now
                elif clock > now:
                    await asyncio.sleep(clock - now)
                if relay.stream is not stream:
                    raise RuntimeError("Live view ended while playing")
                await relay.send_audio(frame)
                clock += adts_duration(frame)
                spoken += adts_duration(frame)
        finally:
            if relay.stream is stream:
                await relay.stop_talk()
    return round(spoken, 1)
