"""PLT (HPGL / HPGL-2) → imagem, para a leitura (ticket CV-20).

Cobre o que aparece nos arquivos de escritório: IN, SP, PU, PD, PA, PR, CI,
AA/AR (arcos aproximados por segmentos), ER/EA (retângulos) e PE (Polyline
Encoded, base 64 com bit de sinal; `<` caneta levantada, `=` coordenada
absoluta, `7` modo de 7 bits, `:` troca de caneta). Texto (LB) é ignorado.

Coordenadas espúrias (lixo de plotter) são cortadas nos percentis 0,1% e
99,9%. O Y do plotter cresce para cima: renderiza invertido e endireita.
Sem dependências além do Pillow.
"""

from __future__ import annotations

import math
import re
from pathlib import Path

from PIL import Image, ImageDraw

LADO = 3000
_CMD = re.compile(r"([A-Z]{2})([^A-Z;]*)", re.S)


def _numeros(texto: str) -> list[float]:
    return [float(n) for n in re.findall(r"-?\d+(?:\.\d+)?", texto)]


def _pe(dados: str, x: float, y: float, segmentos: list, caneta_baixa: bool) -> tuple[float, float]:
    """Decodifica um bloco PE. Devolve a posição final."""
    sete_bits = False
    levantar = False
    absoluto = False
    valores: list[int] = []
    acumulado, deslocamento = 0, 0
    i = 0
    while i < len(dados):
        c = ord(dados[i])
        ch = dados[i]
        i += 1
        if ch == "7":
            sete_bits = True
            continue
        if ch == "<":
            levantar = True
            continue
        if ch == "=":
            absoluto = True
            continue
        if ch == ":":  # seleção de caneta: próximo número, ignorado
            levantar = True
            continue
        if ch in ">":  # fração: ignorada
            continue
        if sete_bits:
            if 63 <= c <= 94:
                acumulado |= (c - 63) << deslocamento
                deslocamento += 5
                continue
            if 95 <= c <= 126:
                acumulado |= (c - 95) << deslocamento
            else:
                continue
        else:
            if 63 <= c <= 126:
                acumulado |= (c - 63) << deslocamento
                deslocamento += 6
                continue
            if 191 <= c <= 254:
                acumulado |= (c - 191) << deslocamento
            else:
                continue
        numero = -(acumulado >> 1) if acumulado & 1 else acumulado >> 1
        acumulado, deslocamento = 0, 0
        valores.append(numero)
        if len(valores) == 2:
            dx, dy = valores
            valores = []
            nx, ny = (dx, dy) if absoluto else (x + dx, y + dy)
            if not levantar:
                segmentos.append((x, y, nx, ny))
            x, y = nx, ny
            levantar = absoluto = False
    return x, y


def segmentos(texto: str) -> list[tuple[float, float, float, float]]:
    segs: list[tuple[float, float, float, float]] = []
    x = y = 0.0
    baixa = False
    relativo = False
    for cmd, args in _CMD.findall(texto):
        if cmd == "PE":
            x, y = _pe(args, x, y, segs, baixa)
            continue
        if cmd == "LB":
            continue
        n = _numeros(args)
        if cmd in ("PU", "PD", "PA", "PR"):
            if cmd == "PU":
                baixa = False
            elif cmd == "PD":
                baixa = True
            elif cmd == "PA":
                relativo = False
            else:
                relativo = True
            for j in range(0, len(n) - 1, 2):
                nx, ny = (x + n[j], y + n[j + 1]) if relativo else (n[j], n[j + 1])
                if baixa:
                    segs.append((x, y, nx, ny))
                x, y = nx, ny
        elif cmd == "CI" and n:
            r = n[0]
            pts = [(x + r * math.cos(a / 36 * 2 * math.pi), y + r * math.sin(a / 36 * 2 * math.pi))
                   for a in range(37)]
            segs += [(*pts[k], *pts[k + 1]) for k in range(36)]
        elif cmd in ("AA", "AR") and len(n) >= 3:
            cx, cy = (n[0], n[1]) if cmd == "AA" else (x + n[0], y + n[1])
            r = math.hypot(x - cx, y - cy)
            ini = math.atan2(y - cy, x - cx)
            passos = max(2, int(abs(n[2]) / 10))
            for k in range(1, passos + 1):
                a = ini + math.radians(n[2]) * k / passos
                nx, ny = cx + r * math.cos(a), cy + r * math.sin(a)
                if baixa:
                    segs.append((x, y, nx, ny))
                x, y = nx, ny
        elif cmd in ("ER", "EA") and len(n) >= 2:
            x2, y2 = (x + n[0], y + n[1]) if cmd == "ER" else (n[0], n[1])
            segs += [(x, y, x2, y), (x2, y, x2, y2), (x2, y2, x, y2), (x, y2, x, y)]
    return segs


def _corte(valores: list[float]) -> tuple[float, float]:
    ordenados = sorted(valores)
    if not ordenados:
        return 0.0, 1.0
    lo = ordenados[int(len(ordenados) * 0.001)]
    hi = ordenados[min(len(ordenados) - 1, int(len(ordenados) * 0.999))]
    return lo, hi if hi > lo else lo + 1


def renderizar(origem: Path, lado: int = LADO) -> Image.Image | None:
    texto = origem.read_bytes().decode("latin-1", errors="ignore")
    segs = segmentos(texto)
    if not segs:
        return None
    xs = [v for s in segs for v in (s[0], s[2])]
    ys = [v for s in segs for v in (s[1], s[3])]
    x0, x1 = _corte(xs)
    y0, y1 = _corte(ys)
    escala = (lado - 40) / max(x1 - x0, y1 - y0)
    largura, altura = int((x1 - x0) * escala) + 40, int((y1 - y0) * escala) + 40
    img = Image.new("L", (max(largura, 50), max(altura, 50)), 255)
    d = ImageDraw.Draw(img)
    for ax, ay, bx, by in segs:
        if not (x0 <= ax <= x1 and x0 <= bx <= x1 and y0 <= ay <= y1 and y0 <= by <= y1):
            continue
        # Y do plotter cresce para cima: desenha já invertido (equivale ao FLIP_TOP_BOTTOM).
        d.line([(20 + (ax - x0) * escala, altura - 20 - (ay - y0) * escala),
                (20 + (bx - x0) * escala, altura - 20 - (by - y0) * escala)], fill=0, width=2)
    return img.convert("RGB")
