#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
unpack_asm_to_pngs.py
Convierte ASM(s) en ./ASM/*.asm a PNG(s) en ./GRAFICOS/*.png (uno por sprite/label).

Soporta:
- Bloques múltiples en un solo .asm (p.ej. graficos.asm generado por pack_graficos_to_asm.py)
- Formatos por bloque:
  A) label: + db width_bytes + db height + (width_bytes*height bytes) + (opcional INK pen,ink)
  B) Si no hay labels, intenta tratar todo el archivo como un único bloque "IMG"

Parseo numérico:
- db / defb
- decimal (123) y hex estilo &F0 / 0xF0 / $F0

Uso:
  python3 unpack_asm_to_pngs.py --mode 0
  python3 unpack_asm_to_pngs.py --mode 0 --recursive
  python3 unpack_asm_to_pngs.py --mode 0 --prefix-file
"""

from __future__ import annotations
import argparse
import os
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

def sanitize_name(s: str) -> str:
    s = s.strip()
    s = re.sub(r"[^A-Za-z0-9_]", "_", s)
    s = re.sub(r"_+", "_", s).strip("_")
    if not s:
        s = "IMG"
    if re.match(r"^\d", s):
        s = "IMG_" + s
    return s

def parse_blocks_from_asm(text: str) -> List[Dict]:
    """
    Devuelve lista de bloques:
      {
        "label": str,
        "db_bytes": List[int],        # números de db/defb en orden dentro del bloque
        "pen_to_ink": Dict[int,int],  # INK detectado dentro del bloque (aunque esté comentado)
      }

    Detecta labels en dos formatos:
    1) "LABEL:" (clásico)
    2) "LABEL" en una línea sola (como los .asm generados por pack_graficos_to_asm.py cuando se pidió sin ':')
       Heurística: línea con identificador y la siguiente línea relevante parece inicio de bloque (db/defb o marcador BEGIN IMAGE).
    """
    lines = text.splitlines()

    label_colon_re = re.compile(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*:\s*$")
    # label "solo" (sin ':') — una palabra identificador, sin espacios extras
    label_solo_re = re.compile(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*$")

    data_re = re.compile(r"\b(db|defb)\b(.*)$", re.IGNORECASE)
    ink_re = re.compile(r"\bINK\s+(\d+)\s*,\s*(\d+)\b", re.IGNORECASE)

    blocks: List[Dict] = []
    current: Optional[Dict] = None
    saw_any_label = False

    def start_block(lbl: str):
        nonlocal current, blocks, saw_any_label
        if current is not None:
            blocks.append(current)
        current = {"label": lbl, "db_bytes": [], "pen_to_ink": {}}
        saw_any_label = True

    def next_relevant_line(idx: int) -> str:
        # devuelve siguiente línea no vacía (incluye comentarios)
        j = idx + 1
        while j < len(lines):
            s = lines[j].strip()
            if s:
                return s
            j += 1
        return ""

    i = 0
    while i < len(lines):
        line = lines[i]

        # 1) LABEL:
        m = label_colon_re.match(line)
        if m:
            start_block(m.group(1))
            i += 1
            continue

        # 2) LABEL (solo) con heurística de lookahead
        m = label_solo_re.match(line)
        if m:
            candidate = m.group(1)
            cand_low = candidate.lower()

            # Evitar falsos positivos: directivas / mnemonics / palabras comunes
            if cand_low not in ("db", "defb", "dw", "defw", "equ", "org", "include", "section", "macro", "endm"):
                nxt = next_relevant_line(i)
                # Si la siguiente línea sugiere inicio de imagen/bloque, lo tomamos como label
                # Ejemplos:
                #   ;------ BEGIN IMAGE --------
                #   db 8
                #   defb &08,&18
                if (nxt.startswith(";------") and "BEGIN" in nxt.upper()) or data_re.search(nxt):
                    start_block(candidate)
                    i += 1
                    continue

        # Si no hay bloque activo todavía, seguimos buscando label
        if current is None:
            i += 1
            continue

        # INK (aunque esté comentado con ';' lo detectamos igual)
        m_ink = ink_re.search(line)
        if m_ink:
            current["pen_to_ink"][int(m_ink.group(1))] = int(m_ink.group(2))

        # Datos db/defb (ignorando comentarios a partir de ';')
        core = line.split(";", 1)[0]
        m_data = data_re.search(core)
        if m_data:
            tail = m_data.group(2)
            tokens = re.findall(r"(&[0-9A-Fa-f]+|0x[0-9A-Fa-f]+|\$[0-9A-Fa-f]+|-?\d+)", tail)
            for t in tokens:
                n = parse_num(t)
                if n is not None:
                    current["db_bytes"].append(n & 0xFF)

        i += 1

    if current is not None:
        blocks.append(current)

    if not saw_any_label:
        # Sin labels: un único bloque con todo el archivo
        pen_to_ink: Dict[int, int] = {}
        db_bytes: List[int] = []
        for line in lines:
            m_ink = ink_re.search(line)
            if m_ink:
                pen_to_ink[int(m_ink.group(1))] = int(m_ink.group(2))
            core = line.split(";", 1)[0]
            m_data = data_re.search(core)
            if not m_data:
                continue
            tail = m_data.group(2)
            tokens = re.findall(r"(&[0-9A-Fa-f]+|0x[0-9A-Fa-f]+|\$[0-9A-Fa-f]+|-?\d+)", tail)
            for t in tokens:
                n = parse_num(t)
                if n is not None:
                    db_bytes.append(n & 0xFF)
        blocks = [{"label": "IMG", "db_bytes": db_bytes, "pen_to_ink": pen_to_ink}]

    return blocks

def decode_block_to_png(
    block: Dict,
    mode: int,
    out_path: str,
    bg_ink: Optional[int] = None,
) -> Tuple[int, int]:
    db_bytes: List[int] = block["db_bytes"]
    if len(db_bytes) < 2:
        raise ValueError("bloque sin header (faltan width_bytes/height en db/defb)")

    width_bytes = db_bytes[0]
    height = db_bytes[1]
    data = db_bytes[2:]

    needed = width_bytes * height
    if len(data) < needed:
        raise ValueError(f"datos insuficientes: necesito {needed} bytes y solo hay {len(data)}")

    data = data[:needed]
    pen_to_ink: Dict[int, int] = block.get("pen_to_ink", {}) or {}

    if mode == 0:
        px_per_byte, max_pens = 2, 16
    elif mode == 1:
        px_per_byte, max_pens = 4, 4
    else:
        px_per_byte, max_pens = 8, 2

    width_px = width_bytes * px_per_byte

    def pen_to_rgba(pen: int) -> Tuple[int, int, int, int]:
        if pen in pen_to_ink:
            ink = pen_to_ink[pen]
        else:
            ink = bg_ink if bg_ink is not None else pen
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
            if mode == 0:
                pens = list(decode_mode0(b))
            elif mode == 1:
                pens = list(decode_mode1(b))
            else:
                pens = decode_mode2(b)
            for pen in pens:
                if pen >= max_pens:
                    pen = pen % max_pens
                pix[x, y] = pen_to_rgba(pen)
                x += 1

    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    img.save(out_path)
    return width_px, height

def iter_asms(root: str, recursive: bool) -> List[str]:
    out: List[str] = []
    if recursive:
        for d, _, files in os.walk(root):
            for f in files:
                if f.lower().endswith(".asm"):
                    out.append(os.path.join(d, f))
    else:
        for f in os.listdir(root):
            if f.lower().endswith(".asm"):
                out.append(os.path.join(root, f))
    out.sort()
    return out

def print_summary(rows: List[Dict[str, str]]) -> None:
    if not rows:
        return
    headers = ["ASM", "LABEL", "PNG", "SIZE(PX)", "STATUS"]
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
    ap.add_argument("--asm-dir", default="ASM", help="Carpeta donde buscar ASM (default: ./ASM)")
    ap.add_argument("--out-dir", default="GRAFICOS", help="Carpeta destino de PNG (default: ./GRAFICOS). Se crea si no existe.")
    ap.add_argument("--recursive", action="store_true", help="Busca ASM recursivamente")
    ap.add_argument("--mode", type=int, choices=[0,1,2], required=True, help="Modo CPC (0/1/2)")
    ap.add_argument("--bg-ink", type=int, default=None, help="Si faltan INK pen,ink, usa este INK como fallback (si no, pen==ink)")
    ap.add_argument("--prefix-file", action="store_true",
                    help="Prefija el nombre del PNG con el nombre del .asm para evitar colisiones")
    args = ap.parse_args()

    asm_dir = args.asm_dir
    out_dir = args.out_dir

    if not os.path.isdir(asm_dir):
        raise SystemExit(f"No existe la carpeta: {asm_dir}")

    asms = iter_asms(asm_dir, args.recursive)
    if not asms:
        raise SystemExit(f"No se han encontrado .asm en: {asm_dir}")

    os.makedirs(out_dir, exist_ok=True)

    summary: List[Dict[str, str]] = []
    ok = 0
    fail = 0
    total_blocks = 0

    for asm_path in asms:
        asm_name = os.path.splitext(os.path.basename(asm_path))[0]
        with open(asm_path, "r", encoding="utf-8", errors="ignore") as f:
            text = f.read()

        blocks = parse_blocks_from_asm(text)
        total_blocks += len(blocks)

        for b in blocks:
            label = sanitize_name(b.get("label", "IMG")).upper()
            if args.prefix_file:
                png_name = f"{sanitize_name(asm_name).upper()}__{label}.png"
            else:
                png_name = f"{label}.png"

            out_path = os.path.join(out_dir, png_name)

            try:
                wpx, hpx = decode_block_to_png(b, args.mode, out_path, bg_ink=args.bg_ink)
                summary.append({
                    "ASM": os.path.relpath(asm_path, asm_dir),
                    "LABEL": label,
                    "PNG": os.path.relpath(out_path, out_dir),
                    "SIZE(PX)": f"{wpx}x{hpx}",
                    "STATUS": "OK",
                })
                ok += 1
            except Exception as e:
                summary.append({
                    "ASM": os.path.relpath(asm_path, asm_dir),
                    "LABEL": label,
                    "PNG": os.path.relpath(out_path, out_dir),
                    "SIZE(PX)": "-",
                    "STATUS": f"ERROR: {e}",
                })
                fail += 1

    print(f"\nASM encontrados: {len(asms)}  | Bloques: {total_blocks}  | PNG OK: {ok}  | Errores: {fail}")
    print(f"Salida PNG en: {os.path.abspath(out_dir)}")
    print_summary(summary)

if __name__ == "__main__":
    main()
