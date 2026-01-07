#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
png2asm.py
Convierte todos los PNG dentro de una carpeta (por defecto ./GRAFICOS) a un único ASM.

NOVEDADES:
- Guarda el ASM dentro de ./ASM (se crea si no existe)
- Muestra un resumen en terminal de los PNG convertidos (y errores si los hay)

Salida ASM (por imagen):
  <label>:
    db <width_bytes> ; ancho en bytes (según modo)
    db <height>
    db ...
    ; INK pen,ink  (comentado: PEN->INK detectado en el PNG)
"""

from __future__ import annotations
import argparse
import os
import re
from dataclasses import dataclass
from typing import Dict, List, Tuple, Optional
from PIL import Image

# Firmware ink -> (R%, G%, B%) 0/50/100 (aprox a 0/128/255)
INK_RGB_PCT: Dict[int, Tuple[int, int, int]] = {
    0:  (0,   0,   0),
    1:  (0,   0,  50),
    2:  (0,   0, 100),
    3:  (50,  0,   0),
    4:  (50,  0,  50),
    5:  (50,  0, 100),
    6:  (100, 0,   0),
    7:  (100, 0,  50),
    8:  (100, 0, 100),
    9:  (0,  50,   0),
    10: (0,  50,  50),
    11: (0,  50, 100),
    12: (50, 50,   0),
    13: (50, 50,  50),
    14: (50, 50, 100),
    15: (100,50,   0),
    16: (100,50,  50),
    17: (100,50, 100),
    18: (0, 100,   0),
    19: (0, 100,  50),
    20: (0, 100, 100),
    21: (50,100,   0),
    22: (50,100,  50),
    23: (50,100, 100),
    24: (100,100,  0),
    25: (100,100, 50),
    26: (100,100,100),
}

def pct_to_8bit(p: int) -> int:
    if p == 0: return 0
    if p == 50: return 128
    if p == 100: return 255
    return round(p * 255 / 100)

INK_RGB: Dict[int, Tuple[int, int, int]] = {
    k: tuple(pct_to_8bit(x) for x in v) for k, v in INK_RGB_PCT.items()
}

@dataclass
class ModeSpec:
    mode: int
    colors: int
    px_per_byte: int
    bits_per_px: int

MODE_SPECS: Dict[int, ModeSpec] = {
    0: ModeSpec(0, 16, 2, 4),
    1: ModeSpec(1, 4,  4, 2),
    2: ModeSpec(2, 2,  8, 1),
}

def nearest_ink(rgb: Tuple[int, int, int], tol: int) -> int:
    r, g, b = rgb
    best = 0
    best_d = 10**18
    for ink, (ir, ig, ib) in INK_RGB.items():
        dr = r - ir
        dg = g - ig
        db = b - ib
        d = dr*dr + dg*dg + db*db
        if d < best_d:
            best_d = d
            best = ink
    if tol >= 0:
        ir, ig, ib = INK_RGB[best]
        if max(abs(r-ir), abs(g-ig), abs(b-ib)) > tol:
            raise ValueError(f"Color {rgb} no coincide con paleta CPC (más cercano INK {best}={INK_RGB[best]})")
    return best

def pack_mode0(p0: int, p1: int) -> int:
    b = 0
    b |= ((p0 >> 0) & 1) << 7
    b |= ((p1 >> 0) & 1) << 6
    b |= ((p0 >> 2) & 1) << 5
    b |= ((p1 >> 2) & 1) << 4
    b |= ((p0 >> 1) & 1) << 3
    b |= ((p1 >> 1) & 1) << 2
    b |= ((p0 >> 3) & 1) << 1
    b |= ((p1 >> 3) & 1) << 0
    return b

def pack_mode1(p0: int, p1: int, p2: int, p3: int) -> int:
    b = 0
    b |= ((p0 >> 1) & 1) << 7
    b |= ((p1 >> 1) & 1) << 6
    b |= ((p2 >> 1) & 1) << 5
    b |= ((p3 >> 1) & 1) << 4
    b |= ((p0 >> 0) & 1) << 3
    b |= ((p1 >> 0) & 1) << 2
    b |= ((p2 >> 0) & 1) << 1
    b |= ((p3 >> 0) & 1) << 0
    return b

def pack_mode2(pxs: List[int]) -> int:
    b = 0
    for i, p in enumerate(pxs):
        b |= (p & 1) << (7 - i)
    return b

def to_rgba(img: Image.Image) -> Image.Image:
    return img.convert("RGBA")

def safe_label(path: str, base_dir: str) -> str:
    rel = os.path.relpath(path, base_dir)
    rel_no_ext = os.path.splitext(rel)[0]
    s = rel_no_ext.replace(os.sep, "_")
    s = re.sub(r"[^A-Za-z0-9_]", "_", s)
    if re.match(r"^\d", s):
        s = "img_" + s
    return s

def iter_pngs(root: str, recursive: bool) -> List[str]:
    out: List[str] = []
    if recursive:
        for d, _, files in os.walk(root):
            for f in files:
                if f.lower().endswith(".png"):
                    out.append(os.path.join(d, f))
    else:
        for f in os.listdir(root):
            if f.lower().endswith(".png"):
                out.append(os.path.join(root, f))
    out.sort()
    return out

def convert_png_to_rows(
    png_path: str,
    spec: ModeSpec,
    tol: int,
    transparent_ink: Optional[int],
) -> Tuple[int, int, List[List[int]], List[int], bool]:
    """
    Returns:
      width_bytes, height, rows(bytes), used_inks(list in order), auto_tol_used(bool)
    """
    img = to_rgba(Image.open(png_path))
    w, h = img.size

    if w % spec.px_per_byte != 0:
        raise ValueError(f"ancho {w}px no divisible por {spec.px_per_byte} (MODE {spec.mode}).")

    width_bytes = w // spec.px_per_byte
    px = img.load()

    ink_grid: List[List[int]] = [[0]*w for _ in range(h)]
    used_inks: List[int] = []
    auto_tol_used = False
    warned = False

    def register_ink(i: int):
        if i not in used_inks:
            used_inks.append(i)

    for y in range(h):
        for x in range(w):
            r, g, b, a = px[x, y]
            if a == 0 and transparent_ink is not None:
                ink = int(transparent_ink)
            else:
                try:
                    ink = nearest_ink((r, g, b), tol)
                except ValueError:
                    # Fallback automático = equivalente a --tol -1
                    ink = nearest_ink((r, g, b), -1)
                    auto_tol_used = True
                    if not warned:
                        print(f"⚠️  {os.path.basename(png_path)}: color fuera de paleta -> fallback (equiv. --tol -1)")
                        warned = True

            ink_grid[y][x] = ink
            register_ink(ink)

    if len(used_inks) > spec.colors:
        raise ValueError(f"usa {len(used_inks)} INKs pero MODE {spec.mode} permite {spec.colors}.")

    ink_to_pen: Dict[int, int] = {ink: pen for pen, ink in enumerate(used_inks)}

    rows: List[List[int]] = []
    for y in range(h):
        row_bytes: List[int] = []
        if spec.mode == 0:
            for x in range(0, w, 2):
                p0 = ink_to_pen[ink_grid[y][x]]
                p1 = ink_to_pen[ink_grid[y][x+1]]
                row_bytes.append(pack_mode0(p0, p1))
        elif spec.mode == 1:
            for x in range(0, w, 4):
                p0 = ink_to_pen[ink_grid[y][x]]
                p1 = ink_to_pen[ink_grid[y][x+1]]
                p2 = ink_to_pen[ink_grid[y][x+2]]
                p3 = ink_to_pen[ink_grid[y][x+3]]
                row_bytes.append(pack_mode1(p0, p1, p2, p3))
        else:
            for x in range(0, w, 8):
                ps = [ink_to_pen[ink_grid[y][x+i]] for i in range(8)]
                row_bytes.append(pack_mode2(ps))
        rows.append(row_bytes)

    return width_bytes, h, rows, used_inks, auto_tol_used

def print_summary(rows: List[Dict[str, str]]) -> None:
    if not rows:
        return
    headers = ["PNG", "Label", "Size(px)", "Bytes/line", "Colors", "Fallback", "Status"]
    widths = {h: len(h) for h in headers}
    for r in rows:
        for h in headers:
            widths[h] = max(widths[h], len(str(r.get(h, ""))))
    def fmt_row(d):
        return "  ".join(str(d.get(h, "")).ljust(widths[h]) for h in headers)
    print("\nResumen:")
    print(fmt_row({h:h for h in headers}))
    print(fmt_row({h:"-"*widths[h] for h in headers}))
    for r in rows:
        print(fmt_row(r))

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", default="GRAFICOS", help="Carpeta donde buscar PNGs (default: ./GRAFICOS)")
    ap.add_argument("--recursive", action="store_true", help="Busca PNGs recursivamente")
    ap.add_argument("--mode", type=int, choices=[0,1,2], required=True, help="Modo CPC (0/1/2)")
    ap.add_argument("-o", "--out", required=True, help="Nombre del ASM de salida (se guardará dentro de ./ASM/)")
    ap.add_argument("--out-dir", default="ASM", help="Carpeta destino (default: ./ASM). Se crea si no existe.")
    ap.add_argument("--tol", type=int, default=8, help="Tolerancia RGB por canal (0 exacto). Si falla, fallback auto a -1.")
    ap.add_argument("--transparent-ink", type=int, default=None, help="Alpha=0 -> este INK (0..26)")
    args = ap.parse_args()

    spec = MODE_SPECS[args.mode]
    root = args.dir

    if not os.path.isdir(root):
        raise SystemExit(f"No existe la carpeta: {root}")

    pngs = iter_pngs(root, args.recursive)
    if not pngs:
        raise SystemExit(f"No se han encontrado PNGs en: {root}")

    # Salida en ./ASM (o --out-dir)
    out_dir = args.out_dir
    os.makedirs(out_dir, exist_ok=True)
    out_path = args.out
    if not os.path.isabs(out_path):
        out_path = os.path.join(out_dir, out_path)

    summary: List[Dict[str, str]] = []
    converted_ok = 0
    converted_fail = 0

    with open(out_path, "w", encoding="utf-8") as f:
        f.write(f"; MODE {spec.mode}\n\n")

        for path in pngs:
            rel = os.path.relpath(path, root)
            label = safe_label(path, root)

            try:
                width_bytes, height, rows, used_inks, auto_tol_used = convert_png_to_rows(
                    path, spec, args.tol, args.transparent_ink
                )
                # Ancho en píxeles para resumen
                width_px = width_bytes * spec.px_per_byte

                if auto_tol_used:
                    f.write("; (nota) se usó fallback de tolerancia (equiv. --tol -1) en algún píxel\n")
                f.write(f"{label.upper()}\n")
                f.write(f";------ BEGIN IMAGE --------\n")
                f.write(f"  db {width_bytes} ; ancho en bytes\n")
                f.write(f"  db {height} ; alto\n")
                for row in rows:
                    f.write("  db " + ", ".join(str(b) for b in row) + "\n")
                f.write(f";------ END IMAGE --------\n")
                f.write("  ; Paleta (PEN -> INK) detectada en el PNG\n")
                for pen, ink in enumerate(used_inks):
                    f.write(f"  ; INK {pen},{ink}\n")
                f.write("\n")

                summary.append({
                    "PNG": rel,
                    "Label": label,
                    "Size(px)": f"{width_px}x{height}",
                    "Bytes/line": str(width_bytes),
                    "Colors": str(len(used_inks)),
                    "Fallback": "sí" if auto_tol_used else "no",
                    "Status": "OK",
                })
                converted_ok += 1

            except Exception as e:
                # No abortamos el pack completo: dejamos constancia y seguimos
                summary.append({
                    "PNG": rel,
                    "Label": label,
                    "Size(px)": "-",
                    "Bytes/line": "-",
                    "Colors": "-",
                    "Fallback": "-",
                    "Status": f"ERROR: {e}",
                })
                converted_fail += 1

        # Índice de labels
        f.write("; --- Índice de labels ---\n")
        for path in pngs:
            rel = os.path.relpath(path, root)
            label = safe_label(path, root)
            f.write(f"; {label} = {rel}\n")

    print(f"\nOK: {out_path}")
    print(f"PNGs encontrados: {len(pngs)}  | Convertidos OK: {converted_ok}  | Errores: {converted_fail}")
    print_summary(summary)

if __name__ == "__main__":
    main()
