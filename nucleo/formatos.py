"""Formatos que chegam dos scanners: JPG, PNG, TIF, DNG e PDF.

Cada DOCUMENTO pode vir em várias versões com o mesmo nome-base (TIF de alta +
JPG de preview + DNG da câmera). O documento é lido UMA vez, a partir da
versão mais leve, e a leitura vale para todas.

Para ler, gera-se uma imagem de leitura (JPG reduzido) fora do acervo. O
original nunca é aberto para escrita aqui.

- DNG: preview embutido via exiftool (JpgFromRaw/PreviewImage); sem preview,
  rawpy se estiver instalado.
- PDF: primeira página, via pdftoppm (poppler-utils) ou pypdfium2. O PDF é
  um documento só; o carimbo fica na primeira página.
"""

from __future__ import annotations

import io
import logging
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from PIL import Image

_log = logging.getLogger("cv2.formatos")

EXTENSOES = (".jpg", ".jpeg", ".png", ".tif", ".tiff", ".dng", ".pdf")
# Ordem de preferência para LER (a mais leve primeiro).
PREFERENCIA = (".jpg", ".jpeg", ".png", ".tif", ".tiff", ".dng", ".pdf")
LADO_LEITURA = 3000  # bruta: daqui saem a leitura (2000) e o preview (3000)


@dataclass
class Documento:
    """Um documento físico e todas as suas versões digitais."""

    base: str
    versoes: list[Path] = field(default_factory=list)

    @property
    def para_ler(self) -> Path:
        return sorted(self.versoes, key=lambda p: (PREFERENCIA.index(p.suffix.lower()), str(p)))[0]


def agrupar(arquivos: list[Path]) -> list[Documento]:
    """Junta as versões pelo nome-base, mantendo a ordem da pasta original."""
    por_base: dict[str, Documento] = {}
    for arquivo in sorted(arquivos, key=lambda p: (p.stem.lower(), str(p))):
        chave = arquivo.stem.lower()
        por_base.setdefault(chave, Documento(arquivo.stem)).versoes.append(arquivo)
    return list(por_base.values())


def suportado(caminho: Path) -> bool:
    return caminho.suffix.lower() in EXTENSOES


def _reduzir_e_salvar(img: Image.Image, destino: Path) -> Path:
    img = img.convert("RGB")
    img.thumbnail((LADO_LEITURA, LADO_LEITURA))
    destino.parent.mkdir(parents=True, exist_ok=True)
    img.save(destino, "JPEG", quality=90)
    return destino


def _dng(origem: Path) -> Image.Image | None:
    if shutil.which("exiftool"):
        for tag in ("-JpgFromRaw", "-PreviewImage", "-OtherImage"):
            try:
                saida = subprocess.run(["exiftool", "-b", tag, str(origem)],
                                       capture_output=True, timeout=120).stdout
            except (OSError, subprocess.TimeoutExpired):
                continue
            if len(saida) > 1000:
                try:
                    return Image.open(io.BytesIO(saida))
                except OSError:
                    continue
    try:
        import rawpy  # type: ignore

        with rawpy.imread(str(origem)) as raw:
            return Image.fromarray(raw.postprocess(half_size=True))
    except Exception:  # noqa: BLE001 - rawpy ausente ou DNG ilegível
        pass
    try:
        return Image.open(origem)  # alguns DNG abrem como TIFF
    except OSError:
        return None


def _pdf(origem: Path, temporario: Path) -> Image.Image | None:
    if shutil.which("pdftoppm"):
        prefixo = temporario.with_suffix("")
        try:
            subprocess.run(["pdftoppm", "-f", "1", "-l", "1", "-r", "150", "-jpeg",
                            "-singlefile", str(origem), str(prefixo)],
                           capture_output=True, timeout=180, check=True)
            gerado = prefixo.with_suffix(".jpg")
            if gerado.exists():
                img = Image.open(gerado)
                img.load()
                gerado.unlink(missing_ok=True)
                return img
        except (OSError, subprocess.SubprocessError):
            pass
    try:
        import pypdfium2  # type: ignore

        pdf = pypdfium2.PdfDocument(str(origem))
        try:
            pagina = pdf[0]
            img = pagina.render(scale=150 / 72).to_pil()
            img.load()
            pagina.close()
            return img
        finally:
            pdf.close()
    except Exception:  # noqa: BLE001
        return None


def imagem_de_leitura(origem: Path, destino: Path) -> Path | None:
    """Gera o JPG de leitura em `destino`. None se não deu para abrir."""
    if destino.exists() and destino.stat().st_mtime >= origem.stat().st_mtime:
        return destino
    ext = origem.suffix.lower()
    destino.parent.mkdir(parents=True, exist_ok=True)  # o pdftoppm grava direto aqui
    img: Image.Image | None = None
    try:
        if ext == ".dng":
            img = _dng(origem)
        elif ext == ".pdf":
            img = _pdf(origem, destino)
        else:
            img = Image.open(origem)
            img.load()
    except OSError as erro:
        _log.warning("Não abri %s: %s", origem.name, erro)
        return None
    if img is None:
        _log.warning("Sem imagem de leitura para %s (%s).", origem.name, ext)
        return None
    return _reduzir_e_salvar(img, destino)


def folha_de_contatos(itens: list[tuple[str, Path]], destino: Path, colunas: int = 6,
                      lado: int = 300) -> Path | None:
    """`contatos.jpg`: miniaturas numeradas pelo código do documento."""
    from PIL import ImageDraw

    itens = [(rotulo, p) for rotulo, p in itens if p and p.exists()]
    if not itens:
        return None
    linhas = (len(itens) + colunas - 1) // colunas
    legenda = 28
    folha = Image.new("RGB", (colunas * lado, linhas * (lado + legenda)), "white")
    desenho = ImageDraw.Draw(folha)
    for i, (rotulo, caminho) in enumerate(itens):
        x, y = (i % colunas) * lado, (i // colunas) * (lado + legenda)
        try:
            with Image.open(caminho) as img:
                mini = img.convert("RGB")
                mini.thumbnail((lado - 10, lado - 10))
                folha.paste(mini, (x + (lado - mini.width) // 2, y + (lado - mini.height) // 2))
        except OSError:
            desenho.rectangle([x + 5, y + 5, x + lado - 5, y + lado - 5], outline="red")
        desenho.text((x + 6, y + lado + 6), rotulo[-28:], fill="black")
    destino.parent.mkdir(parents=True, exist_ok=True)
    folha.save(destino, "JPEG", quality=80)
    return destino
