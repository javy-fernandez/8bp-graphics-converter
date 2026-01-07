# Conversor de gráficos para la librería 8BP

Herramientas para **convertir gráficos entre PNG y ASM** para Amstrad CPC, pensadas para integrarse directamente en el flujo de trabajo de la librería **8BP**.

El conversor funciona **en ambos sentidos**:

- **PNG → ASM** (conversión en batch)
- **ASM → PNG** (conversión individual o en batch)

Compatible con **MODE 0 / MODE 1 / MODE 2**.

---

## Requisitos

- Python 3
- Pillow

En macOS puede ser necesario instalar Pillow manualmente:

```bash
python3 -m pip install --user pillow
```

---

## PNG2ASM  
### Convierte todos los PNG de la carpeta `GRAFICOS/` en un único archivo `ASM/graficos.asm`

### MODE 0 (16 colores)
```bash
python3 png2asm.py --mode 0 -o graficos.asm
```

### MODE 1 (4 colores)
```bash
python3 png2asm.py --mode 1 -o graficos.asm
```

### MODE 2 (2 colores)
```bash
python3 png2asm.py --mode 2 -o graficos.asm
```

### Transparencia
Convierte píxeles con **alpha = 0** al **INK indicado** (por ejemplo INK 0):

```bash
python3 png2asm.py --mode 0 -o graficos.asm --transparent-ink 0
```

### Paleta y dithering
El conversor:

- Detecta automáticamente **colores fuera de la paleta CPC**
- Si la imagen tiene **dithering** o colores no exactos:
  - **no falla**
  - ajusta automáticamente al **INK más cercano**
  - comportamiento equivalente a `--tol -1`
  - muestra un aviso una sola vez

Esto permite usar PNGs no perfectamente adaptados a CPC sin romper el proceso.

---

## ASM2PNG  
### Convierte un gráfico `.asm` individual a `.png`

### MODE 0
```bash
python3 asm2png_cpc.py sprite.asm --mode 0 -o sprite.png
```

### MODE 1 / MODE 2
```bash
python3 asm2png_cpc.py sprite.asm --mode 1 -o sprite.png
python3 asm2png_cpc.py sprite.asm --mode 2 -o sprite.png
```

### Fondo fijo si no hay paleta
Si el `.asm` **no contiene líneas `INK pen,ink`** (o están incompletas), se puede forzar un INK de fondo:

```bash
python3 asm2png_cpc.py sprite.asm --mode 0 -o sprite.png --bg-ink 0
```

---

## ASM2PNGS  
### Convierte todos los gráficos de `ASM/*.asm` a PNGs individuales en `GRAFICOS/`

### Convertir todo ASM → PNG (MODE 0)
```bash
python3 asm2pngs.py --mode 0
```

### Evitar colisiones de nombres
Si hay varios `.asm` con labels repetidos, se puede prefijar el nombre del archivo:

```bash
python3 asm2pngs.py --mode 0 --prefix-file
```

Ejemplo de salida:

```
GRAFICOS/GRAFICOS__LOGO.png
GRAFICOS/GRAFICOS__SPRITE.png
```

---

## Notas

- Un archivo `graficos.asm` puede contener **múltiples gráficos**, que se exportan automáticamente como PNGs independientes.
- Se soportan labels con y sin `:` (`LABEL:` o `LABEL`).
- El proceso es **no destructivo**: si un gráfico da error, el resto continúa.
- Los labels se generan siempre en **MAYÚSCULAS** para compatibilidad con ensambladores Z80.

---

© Javy Fernández — 2026
