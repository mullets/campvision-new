"""Passada 1 — preparo (método de leitura §2). Só código, nenhuma chamada de modelo.

- Imagem de leitura: 2000 px no lado maior, JPEG 78 (§2.1). O master não é tocado.
- Orientação PELA LEITURA, nunca pela geometria (§2.2): as 4 rotações da imagem
  reduzida passam por um OCR rápido (tesseract, português) e vence a que produz
  mais texto plausível. Empate (< 15%) → `orientacao_incerta`. Nenhuma rotação
  produz texto → testa a imagem espelhada (texto espelhado não pontua).
- Hashes (§2.3): md5 do arquivo (duplicata exata) e hash perceptual (dHash 64
  bits) da imagem já orientada (mesma folha digitalizada duas vezes).
- Preview para o site (§8.4): ~3000 px, JPEG 85, sRGB, já girado.

Sem tesseract instalado a orientação não é decidida: fica 0 e
`orientacao_incerta = true`, para revisão humana na folha de contatos.
"""

from __future__ import annotations

import hashlib
import logging
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageOps

_log = logging.getLogger("cv2.preparo")

LADO_LEITURA = 2000
QUALIDADE_LEITURA = 78
LADO_PREVIEW = 3000
QUALIDADE_PREVIEW = 85
# UX-59: a 1400 px o texto do carimbo de uma prancha grande (A1 a 300 dpi) fica com 5-9 px
# e o tesseract só devolvia lixo; o lixo da folha de cabeça para baixo ganhava e o preview
# saía girado 180°. A 2400 px (a bruta tem 3000) o carimbo volta a ser lido.
LADO_OCR = 2400
EMPATE = 0.15
PISO_TEXTO = 12  # pontos mínimos para considerar que achou texto de verdade
# Abaixo disto não há indício nenhum: a folha fica como veio do scanner (o mais provável).
INDICIO_MINIMO = 4
DISTANCIA_PERCEPTUAL = 6  # bits de diferença no dHash de 64 bits

# Palavras frequentes em pranchas: pesam a favor de português de verdade.
PALAVRAS = set("""
de da do das dos e em para com a o as os no na nos nas por planta corte fachada
detalhe projeto arquiteto arquitetura escala folha data desenho desenhista rua
avenida av residencia edificio casa obra cliente proprietario terreo pavimento
cobertura situacao implantacao elevacao locacao sao paulo sp rio janeiro rev
estrutura instalacoes hidraulica eletrica esquadria esquadrias banheiro cozinha
sala quarto dormitorio garagem area lote quadra caixilho porta janela piso forro
""".split())
NUMERO_PLAUSIVEL = re.compile(
    r"^(\d{1,2}[./-]\d{1,2}[./-]\d{2,4}|1:\d{1,4}|\d{1,4}|[fF]\.?\d{1,3}|\d{1,3}/\d{1,3})$"
)


def tem_ocr() -> bool:
    return shutil.which("tesseract") is not None


def _normalizar(token: str) -> str:
    import unicodedata

    sem_acento = "".join(c for c in unicodedata.normalize("NFD", token)
                         if unicodedata.category(c) != "Mn")
    return re.sub(r"[^a-z0-9:./-]", "", sem_acento.lower())


_PONTA = re.compile(r"^[^a-z0-9]+|[^a-z0-9]+$")
NUMERO_FORTE = re.compile(r"^(\d{1,2}[./-]\d{1,2}[./-]\d{2,4}|1:\d{1,4}|[fF]\.?\d{1,3}|\d{1,3}/\d{1,3})$")


def pontuar_texto(texto: str) -> float:
    """Quanto o texto parece português/número de prancha. Lixo de OCR não pontua.

    UX-59: o texto de cabeça para baixo também sai do tesseract como "palavras" de
    letras simétricas (VIVA, ONVINA, LINOWY…). Por isso só a prova forte pesa:
    palavra do vocabulário (+3; com 1-2 letras, +1, porque DE/DO/OS leem igual
    viradas), data/escala/fração (+2). Palavra apenas "plausível" vale 0,5 e
    número solto 0,5."""
    pontos = 0.0
    for bruto in texto.split():
        token = _normalizar(bruto)
        palavra = _PONTA.sub("", token)
        if len(token) < 2:
            continue
        if palavra in PALAVRAS:
            pontos += 3 if len(palavra) > 2 else 1
        elif NUMERO_FORTE.match(token) or NUMERO_FORTE.match(palavra):
            pontos += 2
        elif NUMERO_PLAUSIVEL.match(palavra):
            pontos += 0.5
        elif re.fullmatch(r"[a-z]{4,}", palavra) and re.search(r"[aeiou]", palavra) \
                and not re.search(r"[^aeiou]{4,}", palavra):
            pontos += 0.5
    return pontos


def _ocr(img: Image.Image) -> str:
    import os

    with tempfile.NamedTemporaryFile(suffix=".png") as f:
        img.save(f.name)
        try:
            proc = subprocess.run(
                ["tesseract", f.name, "-", "-l", "por+eng", "--psm", "11"],
                capture_output=True, text=True, timeout=120,
                # As 4 rotações rodam em paralelo (UX-59): uma linha de execução por tesseract.
                env={**os.environ, "OMP_THREAD_LIMIT": "1"},
            )
        except (OSError, subprocess.TimeoutExpired):
            return ""
    return proc.stdout


@dataclass
class Orientacao:
    rotacao: int = 0          # graus no sentido horário aplicados
    espelhada: bool = False
    incerta: bool = False
    pontos: dict | None = None


def transformar(img: Image.Image, o: Orientacao) -> Image.Image:
    if o.espelhada:
        img = ImageOps.mirror(img)
    if o.rotacao:
        img = img.rotate(-o.rotacao, expand=True)  # PIL gira anti-horário
    return img


def orientar(img: Image.Image) -> Orientacao:
    """Decide rotação e espelhamento pela pontuação do texto lido."""
    if not tem_ocr():
        return Orientacao(incerta=True)
    base = img.convert("L")
    base.thumbnail((LADO_OCR, LADO_OCR))

    def rodada(espelhada: bool) -> dict[int, float]:
        from concurrent.futures import ThreadPoolExecutor

        fonte = ImageOps.mirror(base) if espelhada else base
        giradas = {r: fonte.rotate(-r, expand=True) for r in (0, 90, 180, 270)}
        with ThreadPoolExecutor(max_workers=4) as grupo:
            textos = dict(zip(giradas, grupo.map(_ocr, giradas.values())))
        return {r: pontuar_texto(textos[r]) for r in giradas}

    def incerto(p: dict[int, float]) -> bool:
        o = sorted(p.values(), reverse=True)
        return o[0] < PISO_TEXTO or (o[1] > 0 and (o[0] - o[1]) / o[0] < EMPATE)

    pontos = rodada(False)
    espelhada = False
    melhor = _mais_provavel(pontos)
    # Espelhar a folha é a decisão mais perigosa (preview publicado ao contrário),
    # então só acontece com prova forte: a leitura normal não acha texto nenhum
    # e a espelhada acha bastante. Na dúvida: não espelha e marca incerta.
    incerta = incerto(pontos)
    # O espelho é sempre conferido: em algumas versões do tesseract a folha
    # espelhada ainda "lê" bem na rotação certa sem espelho.
    espelhos = rodada(True)
    m2 = max(espelhos, key=espelhos.get)
    if espelhos[m2] >= PISO_TEXTO * 2 and espelhos[m2] >= 2 * max(pontos[melhor], 1):
        pontos, melhor, espelhada = espelhos, m2, True
        incerta = incerto(pontos)
    elif espelhos[m2] >= PISO_TEXTO and espelhos[m2] * 2 >= pontos[melhor]:
        incerta = True  # espelho leu quase tanto: não decide sozinho
    return Orientacao(rotacao=melhor, espelhada=espelhada, incerta=incerta,
                      pontos={str(k): v for k, v in pontos.items()})


def _mais_provavel(pontos: dict[int, float]) -> int:
    """UX-59: a rotação a aplicar no preview/leitura, MESMO quando incerta (o painel mostra
    a folha nessa posição; o aviso de incerteza continua). Sem indício (folha sem texto
    legível) ou empatada com "como veio", fica como veio do scanner: 0. Antes valia o
    maior número de pontos de lixo, e folha sem texto saía girada à toa."""
    melhor = max(pontos, key=pontos.get)
    if pontos[melhor] < INDICIO_MINIMO:
        return 0
    if melhor != 0 and pontos.get(0, 0.0) >= pontos[melhor] * (1 - EMPATE):
        return 0
    return melhor


def md5(caminho: Path) -> str:
    h = hashlib.md5()
    with caminho.open("rb") as f:
        for bloco in iter(lambda: f.read(1024 * 1024), b""):
            h.update(bloco)
    return h.hexdigest()


def dhash(img: Image.Image) -> str:
    """Hash perceptual de 64 bits (diferença horizontal), em hexadecimal."""
    pequena = img.convert("L").resize((9, 8), Image.LANCZOS)
    px = list(pequena.tobytes())
    bits = 0
    for linha in range(8):
        for col in range(8):
            esq, dir_ = px[linha * 9 + col], px[linha * 9 + col + 1]
            bits = (bits << 1) | (1 if esq > dir_ else 0)
    return f"{bits:016x}"


def distancia(a: str, b: str) -> int:
    try:
        return bin(int(a, 16) ^ int(b, 16)).count("1")
    except ValueError:
        return 64


def salvar(img: Image.Image, destino: Path, lado: int, qualidade: int) -> Path:
    from .formatos import salvar_jpg

    return salvar_jpg(img, destino, lado, qualidade)


@dataclass
class Preparo:
    leitura: Path
    preview: Path | None
    orientacao: Orientacao
    md5: str
    hash_perceptual: str


def preparar(imagem_bruta: Path, master: Path, destino_leitura: Path,
             destino_preview: Path | None) -> Preparo:
    """A partir da imagem de leitura já extraída (JPG grande), orienta e gera as saídas."""
    with Image.open(imagem_bruta) as aberta:
        aberta.load()
        o = orientar(aberta)
        orientada = transformar(aberta, o)
    salvar(orientada, destino_leitura, LADO_LEITURA, QUALIDADE_LEITURA)
    preview = salvar(orientada, destino_preview, LADO_PREVIEW, QUALIDADE_PREVIEW) if destino_preview else None
    return Preparo(destino_leitura, preview, o, md5(master), dhash(orientada))
