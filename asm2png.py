#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import annotations
import argparse
import re
from typing import Dict, List, Tuple, Optional
from PIL import Image

# CPC firmware INK -> RGB (aprox 0/128/255 para 0/50/100)
INK_RGB_PCT: Dict[int, Tuple[int, int, int]] = {
    0:(0,0,0), 1:(0,0,50), 2:(0,0,100), 3:(50,0,0), 4:(50,0,50), 5:(50,0,100),
    6:(100,0,0), 7:(100,0,50), 8:(100,0,100), 9:(0,50,0), 10:(0,50,50), 11:(0,50,100),
    12:(50,50,0), 13:(50,50,50), 14:(50,50,100), 15:(100,50,0), 16:(100,50,50), 17:(100,50,100),
    18:(0,100,0), 19:(0,100,50), 20:(0,100,100), 21:(50,100,0), 22:(50,100,50), 23:(50,100,100),
    24:(100,100,0), 25:(100,100,50), 26:(100,100,100),
}

def pct_to_8bit(p: int) -> int:
    if p == 0: return 0
    if p == 50: return 128
    if p == 100: return 255
    return round(p * 255 / 100)

INK_RGB: Dict[int, Tuple[int, int, int]] = {
    k: tuple(pct_to_8bit(x) for x in v) for k, v in INK_RGB_PCT.items()
}

def decode_mode0(b: int) -> Tuple[int, int]:
    p0 = 0
    p1 = 0
    p0 |= ((b >> 7) & 1) << 0
    p1 |= ((b >> 6) & 1) << 0
    p0 |= ((b >> 5) & 1) << 2
    p1 |= ((b >> 4) & 1) << 2
    p0 |= ((b >> 3) & 1) << 1
    p1 |= ((b >> 2) & 1) << 1
    p0 |= ((b >> 1) & 1) << 3
    p1 |= ((b >> 0) & 1) << 3
    return p0, p1

def decode_mode1(b: int) -> Tuple[int, int, int, int]:
    p0 = (((b >> 7) & 1) << 1) | (((b >> 3) & 1) << 0)
    p1 = (((b >> 6) & 1) << 1) | (((b >> 2) & 1) << 0)
    p2 = (((b >> 5) & 1) << 1) | (((b >> 1) & 1) << 0)
    p3 = (((b >> 4) & 1) << 1) | (((b >> 0) & 1) << 0)
    return p0, p1, p2, p3

def decode_mode2(b: int) -> List[int]:
    return [ (b >> (7 - i)) & 1 for i in range(8) ]

def parse_num(token: str) -> Optional[int]:
    token = token.strip().rstrip(",")
    if not token:
        return None
    if token.startswith("&"):
        return int(token[1:], 16)
    if token.lower().startswith("0x"):
        return int(token, 16)
    if token.startswith("$"):
        return int(token[1:], 16)
    if re.fullmatch(r"-?\d+", token):
        return int(token, 10)
    return None

def parse_asm_flexible(path: str) -> Tuple[List[List[int]], List[int], Dict[int, int]]:
    """
    Returns:
      rows: list of db/defb rows (each is list of bytes)
      flat: all bytes concatenated (in the same order rows appear)
      pen_to_ink: parsed INK pen,ink lines (even if commented)
    """
    with open(path, "r", encoding="utf-8", errors="ignore") as f:
        lines = f.readlines()

    pen_to_ink: Dict[int, int] = {}
    ink_re = re.compile(r"\bINK\s+(\d+)\s*,\s*(\d+)\b", re.IGNORECASE)
    for line in lines:
        m = ink_re.search(line)
        if m:
            pen_to_ink[int(m.group(1))] = int(m.group(2))

    data_re = re.compile(r"\b(db|defb)\b(.*)$", re.IGNORECASE)

    rows: List[List[int]] = []
    flat: List[int] = []

    for line in lines:
        core = line.split(";", 1)[0]  # quita comentarios tipo '; line X'
        m = data_re.search(core)
        if not m:
            continue
        tail = m.group(2)
        tokens = re.findall(r"(&[0-9A-Fa-f]+|0x[0-9A-Fa-f]+|\$[0-9A-Fa-f]+|-?\d+)", tail)
        vals: List[int] = []
        for t in tokens:
            n = parse_num(t)
            if n is not None:
                vals.append(n & 0xFF)
        if vals:
            rows.append(vals)
            flat.extend(vals)

    return rows, flat, pen_to_ink

def guess_format(rows: List[List[int]], flat: List[int]) -> Tuple[str, int, int, List[int]]:
    """
    Decide between:
      - "header": width_bytes, height, then data
      - "lines": rows are image lines (maybe with a short meta line at start)
    Returns: (fmt, width_bytes, height, data_bytes)
    """
    # Heurística A: si los dos primeros bytes parecen width_bytes/height y encajan
    if len(flat) >= 2:
        w = flat[0]
        h = flat[1]
        # rangos razonables (ajusta si quieres)
        if 1 <= w <= 255 and 1 <= h <= 255:
            needed = w * h
            if len(flat) >= 2 + needed:
                # chequeo extra: muchos assets en "lines" tienen 1ª fila corta (2 bytes)
                # y luego filas largas constantes; en ese caso preferimos "lines".
                # Si rows[0] tiene 2 bytes y rows[1] es mucho más larga, NO es header.
                if len(rows) >= 2 and len(rows[0]) == 2 and len(rows[1]) >= 4:
                    pass
                else:
                    return ("header", w, h, flat[2:2+needed])

    # Heurística B: formato por líneas
    if not rows:
        raise ValueError("No se han encontrado líneas db/defb con números.")

    # Si la primera fila es muy corta (p.ej. 2 bytes) y la mayoría son de un ancho estable, saltarla.
    if len(rows) >= 3:
        lengths = [len(r) for r in rows]
        # ancho candidato: el más común a partir de la segunda fila
        from_second = lengths[1:]
        common_w = max(set(from_second), key=from_second.count)
        # si la primera es corta y el resto tiene un ancho común claro, la tratamos como meta
        if lengths[0] < common_w and from_second.count(common_w) >= max(2, len(from_second)//2):
            img_rows = rows[1:]
        else:
            img_rows = rows
    else:
        img_rows = rows

    width_bytes = max(set(len(r) for r in img_rows), key=[len(r) for r in img_rows].count)
    # normaliza: recorta/rellena cada fila a width_bytes
    fixed = [ (r + [0]*width_bytes)[:width_bytes] for r in img_rows if len(r) > 0 ]
    height = len(fixed)
    data = [b for r in fixed for b in r]
    return ("lines", width_bytes, height, data)

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("input_asm", help="ASM de entrada")
    ap.add_argument("--mode", type=int, choices=[0, 1, 2], required=True, help="Modo CPC (0/1/2)")
    ap.add_argument("-o", "--out", required=True, help="PNG de salida")
    ap.add_argument("--bg-ink", type=int, default=None,
                    help="Si faltan INKs, usa este INK (si no, pen==ink).")
    ap.add_argument("--verbose", action="store_true", help="Muestra formato detectado")
    args = ap.parse_args()

    rows, flat, pen_to_ink = parse_asm_flexible(args.input_asm)
    fmt, width_bytes, height, data = guess_format(rows, flat)

    if args.mode == 0:
        px_per_byte, max_pens = 2, 16
    elif args.mode == 1:
        px_per_byte, max_pens = 4, 4
    else:
        px_per_byte, max_pens = 8, 2

    width_px = width_bytes * px_per_byte

    def pen_to_rgba(pen: int) -> Tuple[int, int, int, int]:
        if pen in pen_to_ink:
            ink = pen_to_ink[pen]
        else:
            ink = args.bg_ink if args.bg_ink is not None else pen
        ink = max(0, min(26, int(ink)))
        r, g, b = INK_RGB.get(ink, (0, 0, 0))
        return (r, g, b, 255)

    img = Image.new("RGBA", (width_px, height))
    pix = img.load()

    i = 0
    for y in range(height):
        x = 0
        for _ in range(width_bytes):
            b = data[i] & 0xFF
            i += 1
            if args.mode == 0:
                pens = list(decode_mode0(b))
            elif args.mode == 1:
                pens = list(decode_mode1(b))
            else:
                pens = decode_mode2(b)

            for pen in pens:
                if pen >= max_pens:
                    pen = pen % max_pens
                pix[x, y] = pen_to_rgba(pen)
                x += 1

    img.save(args.out)

    if args.verbose:
        print(f"Detectado formato: {fmt}  width_bytes={width_bytes} height={height}")
        if pen_to_ink:
            print("PEN->INK:", pen_to_ink)
        else:
            print("Sin INK: usando pen==ink o --bg-ink")

    print(f"OK: {args.out} ({width_px}x{height}px, MODE {args.mode})")

if __name__ == "__main__":
    main()
