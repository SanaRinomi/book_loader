"""Deterministic random bytes for test data, so builders give the same output every run."""

from __future__ import annotations

import functools
import hashlib

from Crypto.PublicKey import RSA


class DeterministicRandom:
    """SHA-256 in counter mode. Test data only; never use for real keys."""

    def __init__(self, seed: str):
        self._seed = seed.encode("utf-8")
        self._counter = 0
        self._buffer = b""

    def read(self, n: int) -> bytes:
        while len(self._buffer) < n:
            block = hashlib.sha256(self._seed + self._counter.to_bytes(8, "big")).digest()
            self._buffer += block
            self._counter += 1
        out, self._buffer = self._buffer[:n], self._buffer[n:]
        return out


@functools.lru_cache(maxsize=None)
def rsa_key(seed: str, bits: int = 1024) -> RSA.RsaKey:
    """A reproducible RSA key. Adobe ADEPT uses 1024-bit keys."""
    return RSA.generate(bits, randfunc=DeterministicRandom(f"rsa:{seed}").read)
