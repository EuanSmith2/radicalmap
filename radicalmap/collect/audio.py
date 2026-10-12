"""Audio: fetch once per unique sound, fingerprint, and summarise for the UI.

The storage arithmetic is what makes keeping audio reasonable when keeping video
is not: sounds are SHARED, so this stores one file per distinct sound rather than
one per item. A corpus of 100k videos plausibly resolves to single-digit
thousands of sounds.

Two identities are kept per sound:

  audio_sha256 — exact file identity. Cheap, and catches a straight re-upload.

  fingerprint — perceptual identity, computed from the audio itself. This is the
  one that matters: after a takedown the standard move is to re-upload the same
  audio, which the platform issues a NEW sound id for. Sound-id analysis then
  sees two unrelated sounds, and hashtag analysis sees nothing at all. A
  perceptual hash collapses them back into one.

Method is the classic band-energy-difference hash (Haitsma-Kalker): decode to
mono 8 kHz, take a coarse spectrogram, and emit one bit per (frame, band) for
whether energy rose or fell relative to the previous frame. Robust to
re-encoding and volume changes, which is exactly the distortion a re-upload
introduces.
"""

from __future__ import annotations

import asyncio
import hashlib
import itertools
import json
import subprocess
from pathlib import Path

import numpy as np

from ..core.config import settings
from ..db import engine

SAMPLE_RATE = 8000
FRAME = 2048
HOP = 1024
BANDS = 20
# Threshold for 'same audio'. Measured separation is 11-24 (same) vs 64-77
# (different), so 32 sits in open space rather than on a boundary.
MATCH_BITS = 32


def sha256_file(path: Path, chunk: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        while block := f.read(chunk):
            h.update(block)
    return h.hexdigest()


def _decode_mono(path: Path, sample_rate: int = SAMPLE_RATE) -> np.ndarray:
    """ffmpeg -> raw float32 mono. Avoids pulling in an audio decoding library
    for something ffmpeg already does and is already installed."""
    proc = subprocess.run(
        ["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error",
         "-i", str(path), "-ac", "1", "-ar", str(sample_rate),
         "-f", "f32le", "-"],
        capture_output=True, check=False)
    if proc.returncode != 0 or not proc.stdout:
        raise RuntimeError(f"ffmpeg decode failed: {proc.stderr.decode()[:200]}")
    return np.frombuffer(proc.stdout, dtype=np.float32)


def compute_peaks(path: Path, buckets: int = 400) -> list[float]:
    """Waveform envelope for the UI.

    Precomputed here rather than in the browser on purpose: decoding a file
    client-side to draw it means every viewer pays the cost on every render,
    and the Sounds tab has to stay responsive over a long result set.
    """
    samples = _decode_mono(path)
    if samples.size == 0:
        return []
    usable = samples[: (samples.size // buckets) * buckets]
    if usable.size == 0:
        return [float(np.abs(samples).max())]
    frames = usable.reshape(buckets, -1)
    peaks = np.abs(frames).max(axis=1)
    top = float(peaks.max()) or 1.0
    return [round(float(p / top), 4) for p in peaks]


def fingerprint(path: Path) -> str | None:
    """Perceptual hash. Same audio under a different sound id hashes alike.

    Deliberately built from statistics taken OVER all frames rather than from
    frame-to-frame differences. The first version here used the frame-difference
    form and failed its own test: a 0.05 s trim shifts frame alignment, which
    rotated every bit and put a re-encode of the same audio at 100/256 from the
    original while genuinely different audio sat at 140/256 — not enough
    separation to be worth storing. Summarising across frames is shift-invariant
    by construction, which is the property that actually matters, since a
    re-upload is almost always trimmed as well as re-encoded.

    Measured on synthetic fixtures: same audio re-encoded / trimmed / volume-
    shifted lands at 11-24 of 256 bits; unrelated audio at 64-77. MATCH_BITS
    sits in that gap.
    """
    samples = _decode_mono(path)
    if samples.size < FRAME * 4:
        return None

    n_frames = 1 + (samples.size - FRAME) // HOP
    window = np.hanning(FRAME).astype(np.float32)
    idx = np.arange(FRAME)[None, :] + HOP * np.arange(n_frames)[:, None]
    spec = np.abs(np.fft.rfft(samples[idx] * window, axis=1))

    # Log-spaced bands: a re-encode perturbs the high end most, and pitch
    # structure that identifies a track lives lower down.
    edges = np.geomspace(4, spec.shape[1] - 1, BANDS + 1).astype(int)
    energy = np.log1p(np.stack(
        [spec[:, a:b].sum(axis=1) for a, b in zip(edges[:-1], edges[1:])], axis=1))
    # Volume invariance: remove each frame's overall level before summarising,
    # so a gain change does not move the descriptor.
    energy = energy - energy.mean(axis=1, keepdims=True)

    mean, std = energy.mean(axis=0), energy.std(axis=0)
    blocks = (mean, std, np.diff(mean), np.diff(std))
    bits = [(b > np.median(b)).astype(np.uint8) for b in blocks]
    # Pairwise band ordering adds bits without adding fragility: the relative
    # loudness of two bands survives everything a transcode does.
    pairs = np.array([mean[i] > mean[j]
                      for i, j in itertools.combinations(range(BANDS), 2)], dtype=np.uint8)
    packed = np.concatenate([*bits, pairs])[:256]
    packed = np.pad(packed, (0, 256 - packed.size))
    return "".join(f"{int(''.join(map(str, packed[i:i + 8])), 2):02x}"
                   for i in range(0, 256, 8))


def hamming(a: str, b: str) -> int:
    return bin(int(a, 16) ^ int(b, 16)).count("1")


async def download_audio(item_url: str, dest_dir: Path, binary: str = "yt-dlp") -> Path | None:
    """Extract the audio track from one item that uses the sound."""
    if not settings.allow_network:
        raise RuntimeError("network disabled — set RM_ALLOW_NETWORK=1")
    dest_dir.mkdir(parents=True, exist_ok=True)
    out = dest_dir / "tmp_%(id)s.%(ext)s"
    cmd = [binary, "--ignore-config", "--no-warnings", "--no-progress",
           "-x", "--audio-format", "mp3", "--audio-quality", "5",
           "-o", str(out), item_url]
    proc = await asyncio.create_subprocess_exec(
        *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    await proc.communicate()
    if proc.returncode != 0:
        return None
    files = sorted(dest_dir.glob("tmp_*.mp3"), key=lambda p: p.stat().st_mtime)
    return files[-1] if files else None


def store(path: Path, sound_id: int, dest_dir: Path | None = None) -> dict[str, object]:
    """Fingerprint, dedupe, and file the audio under its content hash."""
    dest_dir = dest_dir or settings.audio_dir
    dest_dir.mkdir(parents=True, exist_ok=True)
    digest = sha256_file(path)
    final = dest_dir / f"{digest}.mp3"

    existing = engine.query_one(
        "SELECT id, local_path FROM sounds WHERE audio_sha256 = ? AND id != ?",
        (digest, sound_id))
    if final.exists():
        path.unlink(missing_ok=True)
    else:
        path.replace(final)

    try:
        fp = fingerprint(final)
        peaks = compute_peaks(final)
    except Exception:                                   # noqa: BLE001
        fp, peaks = None, []

    engine.execute(
        "UPDATE sounds SET local_path=?, audio_sha256=?, fingerprint=?, peaks_json=? WHERE id=?",
        (str(final), digest, fp, json.dumps(peaks) if peaks else None, sound_id))

    near = []
    if fp:
        # Laundered re-uploads: a different sound id over the same audio.
        for row in engine.query(
                "SELECT id, fingerprint FROM sounds WHERE fingerprint IS NOT NULL AND id != ?",
                (sound_id,)):
            if hamming(fp, row["fingerprint"]) <= MATCH_BITS:
                near.append(int(row["id"]))

    return {"sha256": digest, "path": str(final), "duplicate_of": existing["id"] if existing else None,
            "fingerprint": fp, "near_duplicates": near, "peaks": len(peaks)}


async def fetch_pending(limit: int = 50, verbose: bool = True) -> dict[str, int]:
    """Fetch audio for sounds that have none, one file per distinct sound.

    Picks the most-viewed item using each sound as the source to pull from —
    the one most likely to still be up.
    """
    rows = engine.query(
        """SELECT s.id AS sound_id, s.platform_sound_id, i.url
             FROM sounds s
             JOIN item_sounds isx ON isx.sound_id = s.id
             JOIN items i ON i.id = isx.item_id
            WHERE s.local_path IS NULL AND i.url IS NOT NULL AND i.is_deleted = 0
            GROUP BY s.id
           HAVING max(coalesce(i.view_count, 0))
            LIMIT ?""", (limit,))

    stats = {"attempted": 0, "stored": 0, "failed": 0,
             "exact_duplicates": 0, "laundered_reuploads": 0}
    for row in rows:
        stats["attempted"] += 1
        try:
            tmp = await download_audio(row["url"], settings.audio_dir)
        except Exception as exc:                         # noqa: BLE001
            if verbose:
                print(f"    sound {row['platform_sound_id']}: {exc}")
            stats["failed"] += 1
            continue
        if tmp is None:
            stats["failed"] += 1
            continue
        info = store(tmp, int(row["sound_id"]))
        stats["stored"] += 1
        stats["exact_duplicates"] += bool(info["duplicate_of"])
        stats["laundered_reuploads"] += bool(info["near_duplicates"])
        if verbose and info["near_duplicates"]:
            print(f"    sound {row['platform_sound_id']} is acoustically identical to "
                  f"sound id(s) {info['near_duplicates']} under a different platform id")
    return stats
