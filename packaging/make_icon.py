"""Genera packaging/icon.ico: el mosaico de 3×3 de la marca, con los colores pastel de la app.

Sin dependencias (PNG con zlib dentro de un .ico). Uso: py packaging/make_icon.py
"""
from __future__ import annotations

import struct
import zlib
from pathlib import Path

# Mismo orden y colores que .brand-mark en app/ui/styles.css
MINT, LAV, SKY, PEACH, ROSE = (0xBD, 0xEB, 0xD3), (0xCF, 0xC4, 0xF2), (0xBF, 0xE3, 0xF5), (0xFF, 0xD9, 0xB8), (0xFF, 0xC2, 0xC7)
GRID = [MINT, LAV, SKY,
        PEACH, MINT, LAV,
        SKY, ROSE, MINT]
SIZES = (16, 24, 32, 48, 64, 128, 256)


def _inside_rounded(x: float, y: float, x0: float, y0: float, size: float, r: float) -> bool:
    cx = min(max(x, x0 + r), x0 + size - r)
    cy = min(max(y, y0 + r), y0 + size - r)
    return (x - cx) ** 2 + (y - cy) ** 2 <= r * r


def pixels(n: int) -> list[list[tuple[int, int, int, int]]]:
    pad = max(1.0, n * 0.04)
    gap = max(1.0, n * 0.06)
    cell = (n - 2 * pad - 2 * gap) / 3
    radius = cell * 0.24
    img = [[(0, 0, 0, 0)] * n for _ in range(n)]
    ss = 4                                                    # 4×4 muestras por píxel: bordes suaves
    for py in range(n):
        for px in range(n):
            hits: dict[int, int] = {}
            for sy in range(ss):
                for sx in range(ss):
                    x, y = px + (sx + .5) / ss, py + (sy + .5) / ss
                    col, row = int((x - pad) // (cell + gap)), int((y - pad) // (cell + gap))
                    if 0 <= col < 3 and 0 <= row < 3:
                        x0, y0 = pad + col * (cell + gap), pad + row * (cell + gap)
                        if _inside_rounded(x, y, x0, y0, cell, radius):
                            hits[row * 3 + col] = hits.get(row * 3 + col, 0) + 1
            if hits:
                k, count = max(hits.items(), key=lambda kv: kv[1])
                r, g, b = GRID[k]
                img[py][px] = (r, g, b, round(255 * sum(hits.values()) / (ss * ss)))
    return img


def png(n: int) -> bytes:
    raw = b"".join(b"\x00" + bytes(c for px in row for c in px) for row in pixels(n))

    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", n, n, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b""))


def ico(sizes=SIZES) -> bytes:
    images = [png(n) for n in sizes]
    header = struct.pack("<HHH", 0, 1, len(images))
    offset = 6 + 16 * len(images)
    entries = b""
    for n, data in zip(sizes, images):
        entries += struct.pack("<BBBBHHII", n % 256, n % 256, 0, 0, 1, 32, len(data), offset)
        offset += len(data)
    return header + entries + b"".join(images)


def main() -> Path:
    out = Path(__file__).resolve().parent / "icon.ico"
    out.write_bytes(ico())
    (out.parent / "icon-256.png").write_bytes(png(256))
    return out


if __name__ == "__main__":
    print(main())
