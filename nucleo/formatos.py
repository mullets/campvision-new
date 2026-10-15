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

EXTENSOES = (".jpg", ".jpeg", ".png", ".tif", ".tiff", ".dng", ".nef", ".pdf", ".cdr", ".plt")
# Ordem de preferência para LER (a mais leve primeiro).
PREFERENCIA = (".jpg", ".jpeg", ".png", ".tif", ".tiff", ".dng", ".nef", ".pdf", ".cdr", ".plt")
# Formatos em que o exiftool não grava metadado: vão para o acervo como estão,
# o EXIF fica só nas outras versões e no catalogacao/ (procedimento, etapa 1).
SEM_EXIF = (".cdr", ".nef", ".plt")


def origem_formato(caminho: Path) -> str:
    """`origem_formato` do procedimento (etapa 1): de que formato a leitura saiu."""
    return {".jpeg": "jpg", ".tiff": "tif"}.get(caminho.suffix.lower(), caminho.suffix.lower().lstrip("."))
LADO_LEITURA = 3000  # bruta: daqui saem a leitura (2000) e o preview (3000)


@dataclass
class Documento:
    """Um documento físico e todas as suas versões digitais."""

    base: str
    versoes: list[Path] = field(default_factory=list)

    @property
    def para_ler(self) -> Path:
        return sorted(self.versoes, key=lambda p: (PREFERENCIA.index(p.suffix.lower()), str(p)))[0]


def agrupar(arquivos: list[Path], grupo=None) -> list[Documento]:
    """Junta as versões pelo nome-base, mantendo a ordem da pasta original.

    `grupo(arquivo)` separa universos: lote do Mac com várias séries tem `scan 001`
    em Negativos e `scan 001` em Slides — documentos diferentes, não versões.
    """
    por_base: dict[str, Documento] = {}
    for arquivo in sorted(arquivos, key=lambda p: (grupo(p) if grupo else "", p.stem.lower(), str(p))):
        chave = (f"{grupo(arquivo)}|" if grupo else "") + arquivo.stem.lower()
        por_base.setdefault(chave, Documento(arquivo.stem)).versoes.append(arquivo)
    return list(por_base.values())


def suportado(caminho: Path) -> bool:
    return caminho.suffix.lower() in EXTENSOES


def para_rgb(img: Image.Image) -> Image.Image:
    """RGB 8 bits em sRGB, sem estragar o que veio do scanner.

    - 16 bits (I;16, I, F, RGB de 48 bits): reescala para 8 bits — `convert("RGB")`
      direto CORTA e a prancha sai estourada ou preta;
    - CMYK/LAB/P/LA/RGBA: converte pelo caminho certo (transparência vira branco);
    - perfil ICC embutido (Adobe RGB, ProPhoto, do scanner): converte para sRGB.
    """
    from PIL import ImageCms

    icc = img.info.get("icc_profile")
    if img.mode in ("I;16", "I;16B", "I;16L", "I;16N", "I", "F"):
        inteira = img.convert("I") if img.mode != "F" else img
        maior = (inteira.getextrema() or (0, 255))[1] or 255
        escala = 255.0 / (65535.0 if img.mode.startswith("I;16") or maior > 255 else 255.0)
        img = inteira.point(lambda v: v * escala).convert("L")
    if img.mode in ("RGBA", "LA", "PA") or (img.mode == "P" and "transparency" in img.info):
        fundo = Image.new("RGB", img.size, "white")
        rgba = img.convert("RGBA")
        fundo.paste(rgba, mask=rgba.getchannel("A"))
        img = fundo
    if icc and img.mode in ("RGB", "CMYK", "L", "LAB"):
        try:
            origem = ImageCms.ImageCmsProfile(io.BytesIO(icc))
            img = ImageCms.profileToProfile(img, origem, ImageCms.createProfile("sRGB"), outputMode="RGB")
        except (ImageCms.PyCMSError, OSError, ValueError):
            pass
    return img if img.mode == "RGB" else img.convert("RGB")


def salvar_jpg(img: Image.Image, destino: Path, lado: int, qualidade: int = 95) -> Path:
    """JPG de qualidade de arquivo: LANCZOS, 4:4:4 (traço fino de prancha não borra), sRGB."""
    img = para_rgb(img)
    if max(img.size) > lado:
        img.thumbnail((lado, lado), Image.Resampling.LANCZOS)
    destino.parent.mkdir(parents=True, exist_ok=True)
    img.save(destino, "JPEG", quality=qualidade, subsampling=0, optimize=True)
    return destino


def _reduzir_e_salvar(img: Image.Image, destino: Path) -> Path:
    return salvar_jpg(img, destino, LADO_LEITURA, 92)


def _dng(origem: Path, maxima: bool = False) -> Image.Image | None:
    if maxima:  # revelado inteiro, 8 bits, balanço da câmera
        try:
            import rawpy  # type: ignore

            with rawpy.imread(str(origem)) as raw:
                return Image.fromarray(raw.postprocess(use_camera_wb=True, output_bps=8))
        except Exception:  # noqa: BLE001 - sem rawpy: cai na prévia embutida (avisado no log)
            _log.warning("%s: sem rawpy, o JPG sai da prévia embutida do RAW (qualidade menor).", origem.name)
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


def _pdf(origem: Path, temporario: Path, dpi: int = 150) -> Image.Image | None:
    if shutil.which("pdftoppm"):
        prefixo = temporario.with_suffix("")
        try:
            subprocess.run(["pdftoppm", "-f", "1", "-l", "1", "-r", str(dpi), "-png",
                            "-singlefile", str(origem), str(prefixo)],
                           capture_output=True, timeout=180, check=True)
            gerado = prefixo.with_suffix(".png")
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
            img = pagina.render(scale=dpi / 72).to_pil()
            img.load()
            pagina.close()
            return img
        finally:
            pdf.close()
    except Exception:  # noqa: BLE001
        return None


def _cdr(origem: Path, temporario: Path) -> Image.Image | None:
    """CorelDRAW → PDF (LibreOffice headless) → JPG pela mesma rota do PDF."""
    soffice = shutil.which("soffice") or shutil.which("libreoffice")
    if not soffice:
        _log.warning("%s: CDR precisa do LibreOffice (soffice) para ler.", origem.name)
        return None
    pasta = temporario.parent / f".cdr-{temporario.stem}"
    pasta.mkdir(parents=True, exist_ok=True)
    try:
        subprocess.run([soffice, "--headless", "--convert-to", "pdf", "--outdir", str(pasta), str(origem)],
                       capture_output=True, timeout=300)
        pdf = pasta / f"{origem.stem}.pdf"
        if not pdf.exists():
            return None
        img = _pdf(pdf, temporario)
        # Às vezes o bitmap embutido do CDR some na conversão e a página sai em
        # branco; o texto vem completo pelo pdftotext (CV-20). Nesse caso a
        # imagem de leitura vira o texto escrito numa folha, para não perder nada.
        if (img is None or _quase_branca(img)) and shutil.which("pdftotext"):
            texto = subprocess.run(["pdftotext", "-layout", str(pdf), "-"], capture_output=True,
                                   timeout=120).stdout.decode("utf-8", "ignore").strip()
            if texto:
                temporario.with_suffix(".txt").write_text(texto, encoding="utf-8")
                return _texto_em_folha(texto)
        return img
    except (OSError, subprocess.SubprocessError):
        return None
    finally:
        shutil.rmtree(pasta, ignore_errors=True)


def _quase_branca(img: Image.Image) -> bool:
    menor = img.convert("L").resize((200, 200)).getextrema()[0]
    return menor > 235


def _texto_em_folha(texto: str) -> Image.Image:
    from PIL import ImageDraw, ImageFont

    linhas = texto.splitlines()[:180]
    try:
        fonte = ImageFont.truetype("DejaVuSansMono.ttf", 22)
    except OSError:
        fonte = ImageFont.load_default()
    largura = max(800, min(3000, 14 * max((len(l) for l in linhas), default=40) + 80))
    img = Image.new("RGB", (largura, 40 + 28 * len(linhas)), "white")
    d = ImageDraw.Draw(img)
    for i, linha in enumerate(linhas):
        d.text((40, 20 + 28 * i), linha, fill="black", font=fonte)
    return img


def imagem_de_leitura(origem: Path, destino: Path) -> Path | None:
    """Gera o JPG de leitura em `destino`. None se não deu para abrir."""
    if destino.exists() and destino.stat().st_mtime >= origem.stat().st_mtime:
        return destino
    img = abrir(origem, destino)
    return _reduzir_e_salvar(img, destino) if img is not None else None


def abrir(origem: Path, destino: Path, maxima: bool = False) -> Image.Image | None:
    """Abre qualquer formato suportado como imagem (o temporário do PDF/CDR fica ao lado de `destino`).

    A tag de orientação (EXIF/TIFF) é APLICADA: a imagem sai como o scanner/câmera
    mostra. `maxima=True` (JPG do acervo): RAW revelado inteiro, PDF a 400 dpi — a
    prévia embutida do DNG e o PDF a 150 dpi servem para LER, não para arquivar."""
    ext = origem.suffix.lower()
    destino.parent.mkdir(parents=True, exist_ok=True)  # o pdftoppm grava direto aqui
    img: Image.Image | None = None
    try:
        if ext in (".dng", ".nef"):
            img = _dng(origem, maxima)
        elif ext == ".cdr":
            img = _cdr(origem, destino)
        elif ext == ".plt":
            from .hpgl import renderizar

            img = renderizar(origem)
        elif ext == ".pdf":
            img = _pdf(origem, destino, 400 if maxima else 150)
        else:
            img = Image.open(origem)
            img.load()
    except OSError as erro:
        _log.warning("Não abri %s: %s", origem.name, erro)
        return None
    if img is None:
        _log.warning("Sem imagem de leitura para %s (%s).", origem.name, ext)
        return None
    try:
        from PIL import ImageOps

        img = ImageOps.exif_transpose(img) or img
    except (OSError, ValueError, KeyError):
        pass
    return img


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
