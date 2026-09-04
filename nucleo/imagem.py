"""Manipulação de imagem — só Pillow, nada de OpenCV/torch.

Pillow lê TIFF LZW/Deflate nativamente (via libtiff), então a dor de cabeça
com `imagecodecs` e `numpy<2` do app antigo simplesmente não existe aqui.
"""

from __future__ import annotations

import base64
import io
import logging
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageFile

# Pranchas de 250 megapixels são normais neste acervo; o limite de "bomba de
# descompressão" do Pillow só atrapalha.
Image.MAX_IMAGE_PIXELS = None
# Não desistir de um JPG com o final truncado — melhor ler o que der.
ImageFile.LOAD_TRUNCATED_IMAGES = True

_log = logging.getLogger("cv2.imagem")

Caixa = tuple[float, float, float, float]  # x0, y0, x1, y1 normalizados 0-1


@dataclass
class ImagemParaEnvio:
    base64: str
    media_type: str
    largura: int
    altura: int

    @property
    def tokens_estimados(self) -> int:
        """Aproximação da conta de tokens de imagem: largura*altura/750."""
        return int(self.largura * self.altura / 750)


def abrir(caminho: Path) -> Image.Image:
    """Abre a imagem em RGB. Para TIFF de várias páginas, usa a primeira."""
    img = Image.open(caminho)
    try:
        img.seek(0)
    except (EOFError, AttributeError):
        pass
    if img.mode not in ("RGB", "L"):
        img = img.convert("RGB")
    return img


def reduzir(img: Image.Image, lado_maximo: int) -> Image.Image:
    """Reduz mantendo proporção. Nunca amplia."""
    maior = max(img.size)
    if maior <= lado_maximo:
        return img
    fator = lado_maximo / maior
    novo = (max(1, round(img.width * fator)), max(1, round(img.height * fator)))
    return img.resize(novo, Image.LANCZOS)


def recortar(img: Image.Image, caixa: Caixa, margem: float = 0.0) -> Image.Image:
    """Recorta pela caixa normalizada, com margem proporcional, sem estourar bordas."""
    x0, y0, x1, y1 = caixa
    x0, x1 = min(x0, x1), max(x0, x1)
    y0, y1 = min(y0, y1), max(y0, y1)
    dx = (x1 - x0) * margem
    dy = (y1 - y0) * margem
    x0 = max(0.0, x0 - dx)
    y0 = max(0.0, y0 - dy)
    x1 = min(1.0, x1 + dx)
    y1 = min(1.0, y1 + dy)
    caixa_px = (
        int(x0 * img.width),
        int(y0 * img.height),
        max(int(x1 * img.width), int(x0 * img.width) + 1),
        max(int(y1 * img.height), int(y0 * img.height) + 1),
    )
    return img.crop(caixa_px)


def girar(img: Image.Image, graus: int) -> Image.Image:
    """Gira no sentido horário (0/90/180/270)."""
    if graus % 360 == 0:
        return img
    # PIL.rotate gira anti-horário; expand mantém a imagem inteira.
    return img.rotate(-graus % 360, expand=True)


def para_envio(
    img: Image.Image, lado_maximo: int, qualidade: int = 85
) -> ImagemParaEnvio:
    """Reduz, comprime em JPEG e devolve em base64 pronto para a API."""
    reduzida = reduzir(img, lado_maximo)
    if reduzida.mode != "RGB":
        reduzida = reduzida.convert("RGB")
    buffer = io.BytesIO()
    reduzida.save(buffer, format="JPEG", quality=qualidade, optimize=True)
    dados = buffer.getvalue()
    return ImagemParaEnvio(
        base64=base64.b64encode(dados).decode("ascii"),
        media_type="image/jpeg",
        largura=reduzida.width,
        altura=reduzida.height,
    )


def listar_imagens(pasta: Path, extensoes: tuple[str, ...]) -> list[Path]:
    """Lista imagens da pasta (não recursivo), ordenadas e sem arquivos ocultos."""
    exts = {e.lower() for e in extensoes}
    achados = [
        p
        for p in pasta.iterdir()
        if p.is_file() and p.suffix.lower() in exts and not p.name.startswith(".")
    ]
    return sorted(achados, key=lambda p: p.name.lower())
