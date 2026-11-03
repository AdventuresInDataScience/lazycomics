#!/usr/bin/env python3
"""CLI: export an ESRGAN upscaler (.pth) to the ONNX file ``upscale_panels`` runs.

Usage::

    <python with torch> tools/export_upscaler_onnx.py <model.pth> <model.onnx>

A one-off install step (README, "Upscaling"). lazycomics' own env has no
torch, so run this with any Python that does: Wan2GP's Pinokio env works,
``C:\\pinokio\\api\\wan.git\\app\\env\\Scripts\\python.exe``. It needs
``torch``, ``onnx`` and ``onnxruntime``, and doesn't import lazycomics.

Handles the 4x RRDBNet ("ESRGAN") family in both key layouts: basicsr's
(``conv_first``, ``body.N.rdbM``, e.g. Real-ESRGAN's own releases, wrapped
in ``params_ema``/``params``) and the original ESRGAN's (``model.0``,
``model.1.sub.N.RDBM``, e.g. most OpenModelDB models). The block count is
read from the weights. Loads with ``weights_only=True``, so no pickled code
runs. The export has dynamic height and width, and is checked against torch
on a random image before the tool exits.
"""

from __future__ import annotations

import argparse
import re
import sys

import torch
import torch.nn as nn
import torch.nn.functional as F


class _RDB(nn.Module):
    def __init__(self, nf: int = 64, gc: int = 32):
        super().__init__()
        self.conv1 = nn.Conv2d(nf, gc, 3, 1, 1)
        self.conv2 = nn.Conv2d(nf + gc, gc, 3, 1, 1)
        self.conv3 = nn.Conv2d(nf + 2 * gc, gc, 3, 1, 1)
        self.conv4 = nn.Conv2d(nf + 3 * gc, gc, 3, 1, 1)
        self.conv5 = nn.Conv2d(nf + 4 * gc, nf, 3, 1, 1)
        self.lrelu = nn.LeakyReLU(0.2, inplace=True)

    def forward(self, x):
        x1 = self.lrelu(self.conv1(x))
        x2 = self.lrelu(self.conv2(torch.cat((x, x1), 1)))
        x3 = self.lrelu(self.conv3(torch.cat((x, x1, x2), 1)))
        x4 = self.lrelu(self.conv4(torch.cat((x, x1, x2, x3), 1)))
        x5 = self.conv5(torch.cat((x, x1, x2, x3, x4), 1))
        return x5 * 0.2 + x


class _RRDB(nn.Module):
    def __init__(self, nf: int = 64, gc: int = 32):
        super().__init__()
        self.rdb1, self.rdb2, self.rdb3 = _RDB(nf, gc), _RDB(nf, gc), _RDB(nf, gc)

    def forward(self, x):
        return self.rdb3(self.rdb2(self.rdb1(x))) * 0.2 + x


class RRDBNet(nn.Module):
    """basicsr's 4x RRDBNet (3 channels in and out, 64 features)."""

    def __init__(self, num_blocks: int, nf: int = 64, gc: int = 32):
        super().__init__()
        self.conv_first = nn.Conv2d(3, nf, 3, 1, 1)
        self.body = nn.Sequential(*[_RRDB(nf, gc) for _ in range(num_blocks)])
        self.conv_body = nn.Conv2d(nf, nf, 3, 1, 1)
        self.conv_up1 = nn.Conv2d(nf, nf, 3, 1, 1)
        self.conv_up2 = nn.Conv2d(nf, nf, 3, 1, 1)
        self.conv_hr = nn.Conv2d(nf, nf, 3, 1, 1)
        self.conv_last = nn.Conv2d(nf, 3, 3, 1, 1)
        self.lrelu = nn.LeakyReLU(0.2, inplace=True)

    def forward(self, x):
        feat = self.conv_first(x)
        feat = feat + self.conv_body(self.body(feat))
        feat = self.lrelu(self.conv_up1(F.interpolate(feat, scale_factor=2, mode="nearest")))
        feat = self.lrelu(self.conv_up2(F.interpolate(feat, scale_factor=2, mode="nearest")))
        return self.conv_last(self.lrelu(self.conv_hr(feat)))


def _from_original_keys(sd: dict) -> dict:
    """Rename the original ESRGAN's keys to basicsr's."""
    blocks = 1 + max(int(m[1]) for k in sd if (m := re.match(r"model\.1\.sub\.(\d+)\.RDB", k)))
    top = {"model.0": "conv_first", f"model.1.sub.{blocks}": "conv_body", "model.3": "conv_up1",
           "model.6": "conv_up2", "model.8": "conv_hr", "model.10": "conv_last"}
    out = {}
    for key, value in sd.items():
        if m := re.match(r"model\.1\.sub\.(\d+)\.RDB(\d)\.conv(\d)\.0\.(weight|bias)", key):
            out[f"body.{m[1]}.rdb{m[2]}.conv{m[3]}.{m[4]}"] = value
        else:
            prefix, leaf = key.rsplit(".", 1)
            out[f"{top[prefix]}.{leaf}"] = value
    return out


def load_rrdbnet(path: str) -> RRDBNet:
    sd = torch.load(path, map_location="cpu", weights_only=True)
    for wrapper in ("params_ema", "params"):
        if wrapper in sd:
            sd = sd[wrapper]
            break
    if "model.0.weight" in sd:
        sd = _from_original_keys(sd)
    if "conv_up2.weight" not in sd or "conv_first.weight" not in sd:
        raise SystemExit(f"{path}: not a 4x RRDBNet (ESRGAN) state dict")
    blocks = len({k.split(".")[1] for k in sd if k.startswith("body.")})
    net = RRDBNet(blocks)
    net.load_state_dict({k: v.float() for k, v in sd.items()}, strict=True)
    return net.eval()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("pth", help="the ESRGAN .pth to export")
    ap.add_argument("onnx", help="where to write the .onnx")
    args = ap.parse_args(argv)

    net = load_rrdbnet(args.pth)
    torch.onnx.export(
        net, torch.rand(1, 3, 64, 64), args.onnx,
        input_names=["input"], output_names=["output"],
        dynamic_axes={"input": {2: "height", 3: "width"}, "output": {2: "height", 3: "width"}},
        opset_version=17, dynamo=False,
    )

    import numpy as np
    import onnxruntime as ort

    x = torch.rand(1, 3, 96, 80)
    with torch.no_grad():
        want = net(x).numpy()
    got = ort.InferenceSession(args.onnx, providers=["CPUExecutionProvider"]).run(
        None, {"input": x.numpy()})[0]
    err = float(np.abs(want - got).max())
    print(f"{args.onnx}: {len(net.body)} blocks, 4x; max |torch - onnx| = {err:.1e}")
    if err > 1e-4:
        print("export doesn't match torch", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
