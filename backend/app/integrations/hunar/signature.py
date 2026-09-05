"""HMAC verification for inbound Hunar webhooks.

Pure functions over bytes, no framework and no config, so the router stays
readable and the tests can sign a payload with exactly the code the app verifies
with. Nothing here logs, and the key is only ever an argument.
"""

import base64
import hashlib
import hmac
import time


def signed_payload(timestamp: str, body: bytes) -> bytes:
    """The bytes Hunar actually signs: the timestamp, a literal dot, then the
    body exactly as sent.

    `body` must be the raw request bytes. Parsing to JSON and re-serialising
    reorders keys, changes whitespace and rewrites unicode escapes, all of which
    change the digest and turn a genuine webhook into a 401.
    """
    return timestamp.encode("utf-8") + b"." + body


def compute_signature(secret: str, timestamp: str, body: bytes) -> str:
    digest = hmac.new(
        secret.encode("utf-8"), signed_payload(timestamp, body), hashlib.sha256
    ).digest()
    return base64.b64encode(digest).decode("ascii")


def verify_signature(secret: str, timestamp: str, body: bytes, header: str) -> bool:
    """True if any segment of the header matches.

    The header can carry several comma-separated signatures: during a key
    rotation Hunar signs with both the old and the new key so neither side has
    to cut over at the same instant. Accepting any match is what makes the
    rotation non-breaking.

    Every comparison uses `hmac.compare_digest`, never `==`. String equality
    short-circuits at the first differing byte, and the time it takes leaks how
    much of a guess was correct, which is enough to recover a valid signature a
    byte at a time.
    """
    expected = compute_signature(secret, timestamp, body)
    return any(
        hmac.compare_digest(expected, segment.strip())
        for segment in header.split(",")
        if segment.strip()
    )


def timestamp_within_skew(timestamp: str, max_skew_seconds: int) -> bool:
    """Bounds how long a captured webhook stays replayable.

    Checked in both directions: a future timestamp is as suspicious as an old
    one, and clock drift on either side is small compared to the window.
    """
    try:
        sent_at = int(timestamp)
    except (TypeError, ValueError):
        return False
    return abs(time.time() - sent_at) <= max_skew_seconds
