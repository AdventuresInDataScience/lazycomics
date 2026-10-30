"""Face identity — which character does this face belong to? (OpenCV SFace).

Used by ``text_renderer`` to nudge character→face matching when the
generator drew characters in a different left-to-right order than the
enricher predicted (Docs/PLAN.md W3.2). SFace is trained on photos, so on
comic art its similarities are weak — cosine ~0.1–0.55, mostly under its own
0.363 same-person threshold — and it's only ever used as evidence blended
with the positional prior, never on its own (alone it scored 4/9 on held-out
panels; blended, it fixed a mirrored panel and broke none of 9).

The model (~37 MB) isn't committed: :func:`ensure_model` downloads it from
the OpenCV Zoo on first use into ``assets/models/`` and checks its SHA-256.
If that fails (offline, checksum mismatch) it says so and identity is
skipped, leaving matching to position alone. To pin a local copy, drop the
file at ``assets/models/face_recognition_sface_2021dec.onnx``.

Faces are aligned with YuNet's five landmarks (SFace's expected input). A
face YuNet can't find landmarks for gets no embedding.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import ssl
import tempfile
import urllib.request
from functools import lru_cache
from pathlib import Path

import cv2
import numpy as np
from PIL import Image

from lazycomics import face_detector

__all__ = ["ensure_model", "similarity"]


_MODEL_URL = (
    "https://github.com/opencv/opencv_zoo/raw/main/models/"
    "face_recognition_sface/face_recognition_sface_2021dec.onnx"
)
_MODEL_SHA256 = "0ba9fbfa01b5270c96627c4ef784da859931e02f04419c829e83484087c34e79"
_MODEL_PATH = Path(__file__).parent / "assets" / "models" / "face_recognition_sface_2021dec.onnx"
# YuNet is re-run only for alignment landmarks, below the detector's 0.5 so
# stylised faces the hybrid detector accepted still get aligned.
_LANDMARK_SCORE_THRESH = 0.3
_ALIGN_IOU = 0.4

_checked: dict[Path, Path | None] = {}  # one download attempt per process


def ensure_model(
    path: Path = _MODEL_PATH,
    url: str = _MODEL_URL,
    sha256: str = _MODEL_SHA256,
) -> Path | None:
    """Return the verified model path, downloading it on first use.

    Returns ``None`` (after printing why) when the model can't be had.
    Checked once per process, so an offline run warns once, not per panel.
    """
    if path in _checked:
        return _checked[path]
    result: Path | None = None
    try:
        if not (path.is_file() and _sha256(path) == sha256):
            print(f"[identity] downloading the SFace face-recognition model (~37 MB) "
                  f"to {path} ...", flush=True)
            path.parent.mkdir(parents=True, exist_ok=True)
            # A hard crash mid-download skips the cleanup below; sweep those.
            for stale in path.parent.glob(f"{path.name}.*.part"):
                stale.unlink(missing_ok=True)
            fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=f"{path.name}.", suffix=".part")
            os.close(fd)  # reopened below; an open handle would block replace() on Windows
            tmp = Path(tmp_name)
            try:
                with urllib.request.urlopen(url, timeout=120, context=_tls_context()) as resp, \
                        tmp.open("wb") as out:
                    shutil.copyfileobj(resp, out)
                if _sha256(tmp) != sha256:
                    raise ValueError("downloaded file failed its SHA-256 check")
                tmp.replace(path)
            finally:
                tmp.unlink(missing_ok=True)
        result = path
    except Exception as e:
        print(f"[identity] WARNING: SFace model unavailable ({e}); speaker matching "
              f"uses position only. Put the file at {path} to enable it.", flush=True)
    _checked[path] = result
    return result


def _tls_context() -> ssl.SSLContext:
    """A verifying TLS context that ignores ``SSLKEYLOGFILE``.

    ``ssl.create_default_context()`` opens the key-log file named by
    ``SSLKEYLOGFILE`` through OpenSSL's own file I/O, which hard-crashes
    Windows Python builds ("OPENSSL_Uplink: no OPENSSL_Applink") — the whole
    process aborts. A plain client context doesn't read that variable and
    still verifies certificates and hostnames.
    """
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.load_default_certs()
    return ctx


def similarity(
    img: Image.Image,
    faces: list[tuple[int, int, int, int]],
    refs: dict[str, str],
) -> dict[str, list[float | None]] | None:
    """Cosine similarity of each face in ``img`` to each character's reference.

    ``refs`` maps character → reference image path. Returns
    ``{character: [similarity per face, in order]}``, with ``None`` where a
    face or reference couldn't be aligned; ``None`` overall when the model
    is unavailable.
    """
    model = ensure_model()
    if model is None:
        return None
    rec = _recognizer(str(model))
    ref_emb = {c: _reference_embedding(str(model), str(p), Path(p).stat().st_mtime)
               for c, p in refs.items() if Path(p).is_file()}
    bgr = cv2.cvtColor(np.asarray(img.convert("RGB")), cv2.COLOR_RGB2BGR)
    rows = _landmark_rows(bgr)
    face_emb = [_embed(rec, bgr, f, rows) for f in faces]
    return {
        c: [None if e is None or r is None
            else float(rec.match(e, r, cv2.FaceRecognizerSF_FR_COSINE))
            for e in face_emb]
        for c, r in ref_emb.items()
    }


@lru_cache(maxsize=2)
def _recognizer(model_path: str):
    return cv2.FaceRecognizerSF.create(model_path, "")


@lru_cache(maxsize=64)
def _reference_embedding(model_path: str, ref_path: str, mtime: float):
    """Embedding of the most confident face in a reference image (cached per file version)."""
    pil = Image.open(ref_path).convert("RGB")
    boxes = face_detector.detect_faces(pil)
    if not boxes:
        return None
    best = max(boxes, key=lambda f: f.score)
    bgr = cv2.cvtColor(np.asarray(pil), cv2.COLOR_RGB2BGR)
    return _embed(_recognizer(model_path), bgr, (best.x0, best.y0, best.x1, best.y1),
                  _landmark_rows(bgr))


def _landmark_rows(bgr: np.ndarray) -> np.ndarray:
    """YuNet detections (box, 5 landmarks, score) for alignment."""
    h, w = bgr.shape[:2]
    det = cv2.FaceDetectorYN.create(str(face_detector._YUNET_MODEL_PATH), "", (w, h),
                                    _LANDMARK_SCORE_THRESH)
    _, rows = det.detect(bgr)
    return rows if rows is not None else np.zeros((0, 15), np.float32)


def _embed(rec, bgr: np.ndarray, box, rows: np.ndarray):
    """SFace feature for ``box``, aligned on the YuNet row that overlaps it; else ``None``."""
    for r in rows:
        if _iou((r[0], r[1], r[0] + r[2], r[1] + r[3]), box) > _ALIGN_IOU:
            return rec.feature(rec.alignCrop(bgr, r))
    return None


def _iou(a, b) -> float:
    ix = max(0.0, min(a[2], b[2]) - max(a[0], b[0]))
    iy = max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
    inter = ix * iy
    union = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / union if union > 0 else 0.0


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()
