"""Tests for lazycomics.face_identity — the SFace model download and wiring.

The download is exercised offline: ``urllib.request.urlopen`` is replaced
with an in-memory payload, and a test-specific path / checksum is passed so
the real model is never touched.
"""

from __future__ import annotations

import hashlib
import io
import sys
import tempfile
from pathlib import Path
from unittest import mock

from PIL import Image

SRC = Path(__file__).parent.parent / "src"
sys.path.insert(0, str(SRC))

from lazycomics import face_identity  # noqa: E402

PAYLOAD = b"not really an onnx model"
PAYLOAD_SHA = hashlib.sha256(PAYLOAD).hexdigest()


def _serve(payload: bytes, calls: list):
    def urlopen(url, timeout=None, context=None):
        calls.append(url)
        return io.BytesIO(payload)
    return urlopen


def test_downloads_verifies_and_installs_once():
    with tempfile.TemporaryDirectory() as d:
        path = Path(d) / "models" / "model.onnx"
        calls: list = []
        with mock.patch.object(face_identity.urllib.request, "urlopen", _serve(PAYLOAD, calls)):
            assert face_identity.ensure_model(path, "https://example/m", PAYLOAD_SHA) == path
            assert face_identity.ensure_model(path, "https://example/m", PAYLOAD_SHA) == path
        assert path.read_bytes() == PAYLOAD
        assert calls == ["https://example/m"]            # second call cached
        assert list(path.parent.glob("*.part")) == []    # no temp file left behind


def test_existing_valid_model_is_not_downloaded():
    with tempfile.TemporaryDirectory() as d:
        path = Path(d) / "model.onnx"
        path.write_bytes(PAYLOAD)
        calls: list = []
        with mock.patch.object(face_identity.urllib.request, "urlopen", _serve(b"x", calls)):
            assert face_identity.ensure_model(path, "https://example/m", PAYLOAD_SHA) == path
        assert calls == []


def test_checksum_mismatch_disables_identity_loudly(capsys=None):
    import contextlib
    with tempfile.TemporaryDirectory() as d:
        path = Path(d) / "model.onnx"
        out = io.StringIO()
        with mock.patch.object(face_identity.urllib.request, "urlopen", _serve(b"tampered", [])), \
                contextlib.redirect_stdout(out):
            assert face_identity.ensure_model(path, "https://example/m", PAYLOAD_SHA) is None
        assert "WARNING" in out.getvalue() and "SHA-256" in out.getvalue()
        assert not path.exists()
        assert list(Path(d).glob("*.part")) == []


def test_offline_disables_identity_and_does_not_retry():
    with tempfile.TemporaryDirectory() as d:
        path = Path(d) / "model.onnx"
        calls = []

        def offline(url, timeout=None, context=None):
            calls.append(url)
            raise OSError("no network")

        with mock.patch.object(face_identity.urllib.request, "urlopen", offline):
            assert face_identity.ensure_model(path, "https://example/m", PAYLOAD_SHA) is None
            assert face_identity.ensure_model(path, "https://example/m", PAYLOAD_SHA) is None
        assert len(calls) == 1


def test_stale_partial_download_is_swept():
    # A hard crash mid-download (e.g. the SSLKEYLOGFILE abort) skips cleanup.
    with tempfile.TemporaryDirectory() as d:
        path = Path(d) / "model.onnx"
        stale = Path(d) / "model.onnx.abc123.part"
        stale.write_bytes(b"")
        with mock.patch.object(face_identity.urllib.request, "urlopen", _serve(PAYLOAD, [])):
            assert face_identity.ensure_model(path, "https://example/m", PAYLOAD_SHA) == path
        assert not stale.exists()


def test_tls_context_ignores_sslkeylogfile_but_still_verifies():
    import os
    import ssl
    with tempfile.TemporaryDirectory() as d, \
            mock.patch.dict(os.environ, {"SSLKEYLOGFILE": str(Path(d) / "keys.log")}):
        ctx = face_identity._tls_context()
    assert ctx.keylog_filename is None
    assert ctx.verify_mode == ssl.CERT_REQUIRED and ctx.check_hostname


def test_similarity_is_none_without_the_model():
    with mock.patch.object(face_identity, "ensure_model", lambda: None):
        assert face_identity.similarity(Image.new("RGB", (64, 64)), [(0, 0, 10, 10)],
                                        {"A": "/nope.png"}) is None


def test_iou():
    assert face_identity._iou((0, 0, 10, 10), (0, 0, 10, 10)) == 1.0
    assert face_identity._iou((0, 0, 10, 10), (20, 20, 30, 30)) == 0.0
    assert abs(face_identity._iou((0, 0, 10, 10), (5, 0, 15, 10)) - 1 / 3) < 1e-9
