"""Recording bytes, fetched or fabricated.

Two reasons this is not a URL handed to the browser:

1. Hunar's `recording_url` is a raw S3 URL (see fixtures/observed_shapes.md).
   Putting it in a page makes the recording of a real person's phone call
   reachable by anyone who reads the HTML, forever, with no way to revoke it.
2. In demo mode the URL points at `demo-recordings.invalid`, which resolves to
   nothing. A player wired straight to it would just be broken.

So the backend is the only thing that ever sees the S3 URL, and demo mode gets
generated silence instead of a dead link.
"""

import struct

import httpx

# Deliberately short. It exists so the player has something real to load, has a
# duration, and can be scrubbed — not so anyone listens to it.
DEMO_SECONDS = 2
DEMO_SAMPLE_RATE = 8000

# 30 seconds at 8kHz mono 16-bit is under half a megabyte, and a real Hunar
# recording of a two-minute call is a few megabytes. Anything far above that is
# not a call recording, and streaming it would tie up a free-tier worker.
MAX_RECORDING_BYTES = 25 * 1024 * 1024
CHUNK_BYTES = 64 * 1024
FETCH_TIMEOUT_SECONDS = 30.0


class RecordingUnavailable(Exception):
    """The upstream recording could not be fetched. Carries a readable reason."""


def silent_wav(seconds: int = DEMO_SECONDS, sample_rate: int = DEMO_SAMPLE_RATE) -> bytes:
    """A valid mono 16-bit PCM WAV of pure silence.

    Written by hand rather than pulled from a fixture file: it is 20 lines, and a
    binary asset in the repo is a thing to explain, license and keep.
    """
    samples = seconds * sample_rate
    data_bytes = samples * 2
    header = b"".join(
        [
            b"RIFF",
            struct.pack("<I", 36 + data_bytes),
            b"WAVEfmt ",
            struct.pack("<I", 16),          # fmt chunk size
            struct.pack("<H", 1),           # PCM
            struct.pack("<H", 1),           # mono
            struct.pack("<I", sample_rate),
            struct.pack("<I", sample_rate * 2),  # byte rate
            struct.pack("<H", 2),           # block align
            struct.pack("<H", 16),          # bits per sample
            b"data",
            struct.pack("<I", data_bytes),
        ]
    )
    return header + b"\x00" * data_bytes


async def stream_recording(url: str, meta: dict[str, str] | None = None):
    """Yield the upstream recording in chunks.

    Streamed rather than loaded whole: the worker holds one chunk at a time
    instead of an entire multi-megabyte file, which matters on a 512MB free-tier
    instance serving one process.

    `meta` is filled with the upstream `content-length` before the first chunk is
    yielded, so the caller can pass it on. Without it Starlette sends the body
    chunked, the browser cannot compute a duration, and the player renders as
    `0:00` with a dead scrub bar — observed on the first real recording this
    proxy ever served.
    """
    timeout = httpx.Timeout(FETCH_TIMEOUT_SECONDS)
    try:
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
            async with client.stream("GET", url) as response:
                if response.status_code != 200:
                    raise RecordingUnavailable(
                        f"the recording host answered {response.status_code}"
                    )
                declared = response.headers.get("content-length")
                if declared and int(declared) > MAX_RECORDING_BYTES:
                    raise RecordingUnavailable("the recording is larger than we will proxy")
                if meta is not None and declared:
                    meta["content-length"] = declared

                sent = 0
                async for chunk in response.aiter_bytes(CHUNK_BYTES):
                    sent += len(chunk)
                    if sent > MAX_RECORDING_BYTES:
                        # A host that lies about content-length, or does not send
                        # one, must not be able to stream us out of memory.
                        raise RecordingUnavailable(
                            "the recording is larger than we will proxy"
                        )
                    yield chunk
    except httpx.HTTPError as exc:
        raise RecordingUnavailable(f"could not reach the recording host: {exc}") from exc
