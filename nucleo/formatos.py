"""Formatos que chegam dos scanners: JPG, PNG, TIF, DNG e PDF.

Cada DOCUMENTO pode vir em várias versões (TIF de alta + JPG de preview + DNG da
câmera + "JPG LIMPO" + "fotos para internet"), com o mesmo nome-base ou não
(CAMP-FORMATOS: ver `agrupar`). O documento é lido UMA vez, a partir da versão
mais leve legível, e a leitura vale para todas.

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


# ------------------------------------------------------------- versões (CAMP-FORMATOS)
# A MESMA prancha chega em várias pastas de formato/versão do projeto (F001, 29/10):
# `TIFF/`, `DNG/`, `JPG/`, `JPG LIMPO/`, `fotos em alta/` × `fotos para internet/`,
# muitas vezes com NOMES diferentes. Tudo isso é UM documento (um só D) com várias
# versões; nada é descartado: cada versão vai para a pasta final e para o mapa_origem.
#
# Pasta de formato/versão: o nome normalizado (sem acento, minúsculas, sem o prefixo
# numérico "01 - ") só tem palavras de formato/versão e de ligação, e ao menos uma de
# formato/versão. Ex.: "TIFF", "02 - JPG", "JPG LIMPO", "limpo", "fotos em alta",
# "fotos para internet", "alta resolução", "baixa", "web", "originais", "tratadas",
# "matriz", "preview". "Fotografias", "Negativos", "Casa X" NÃO são (são material/projeto).
PALAVRAS_FORMATO = {"tif", "tiff", "jpg", "jpeg", "dng", "pdf", "png", "raw", "nef"}
PALAVRAS_VERSAO = {
    "preview", "previews", "alta", "baixa", "resolucao", "res", "hd", "matriz", "master",
    "limpo", "limpa", "limpos", "limpas", "internet", "web", "site", "original", "originais",
    "tratada", "tratadas", "tratado", "tratados",
}
_LIGACAO = {"fotos", "foto", "imagens", "imagem", "arquivos", "em", "para", "de", "da", "do", "e", "versao", "versoes"}
# Sufixo de NOME que marca versão ("prancha 01 limpo.jpg", "IMG_0001_web.jpg"): sai do radical.
SUFIXOS_VERSAO = {"limpo", "limpa", "alta", "baixa", "internet", "web", "site", "final", "hd", "res",
                  "tratada", "tratado", "original", "copia", "editada", "editado"}

# Etiqueta canônica da versão (pela pasta): decide matriz, papel e leitura.
_ETIQUETAS = (("limp", "limpo"), ("internet", "internet"), ("web", "internet"), ("site", "internet"),
              ("baixa", "baixa"), ("alta", "alta"), ("hd", "alta"), ("matriz", "matriz"), ("master", "matriz"),
              ("original", "original"), ("tratad", "tratada"), ("preview", "preview"))
PUBLICACAO = {"internet", "baixa"}       # papel "publicacao": versão reduzida para a web
# Matriz (a versão de arquivo do documento): TIFF > DNG > JPG LIMPO > JPG > outros; "alta" > "internet".
_ORDEM_MATRIZ_EXT = {".tif": 0, ".tiff": 0, ".dng": 1, ".nef": 1, ".jpg": 2, ".jpeg": 2}  # o resto: 9
_ORDEM_MATRIZ_ETIQUETA = {"matriz": 0, "limpo": 1, "": 2, "alta": 2, "original": 2, "tratada": 2,
                          "preview": 3, "baixa": 4, "internet": 4}
# Leitura (carimbo/prévia): a mais leve LEGÍVEL — JPG antes de TIFF (PREFERENCIA); entre
# JPGs, o LIMPO (sem sujeira do scanner) primeiro e a reduzida "para internet" por último.
_ORDEM_LEITURA_ETIQUETA = {"limpo": 0, "": 1, "alta": 1, "original": 1, "tratada": 1, "matriz": 1,
                           "preview": 2, "baixa": 3, "internet": 3}
# dHash da imagem BRUTA (antes de girar) nas 4 rotações: até 6 bits = mesma imagem.
LIMITE_DHASH = 6
TOLERANCIA_PROPORCAO = 0.06
_DE_IMAGEM = (".jpg", ".jpeg", ".png", ".tif", ".tiff", ".dng", ".nef")


def _tokens(nome: str) -> list[str]:
    from .estrutura import normalizar

    tokens = normalizar(nome).split()
    while tokens and tokens[0].isdigit():   # "01 - JPG" -> "jpg"
        tokens.pop(0)
    return tokens


def e_pasta_de_versao(nome: str) -> bool:
    """Pasta de formato/versão da mesma prancha (ver PALAVRAS_FORMATO/PALAVRAS_VERSAO)."""
    tokens = _tokens(nome)
    if not tokens or not any(t in PALAVRAS_FORMATO or t in PALAVRAS_VERSAO for t in tokens):
        return False
    return all(t in PALAVRAS_FORMATO or t in PALAVRAS_VERSAO or t in _LIGACAO for t in tokens)


def etiqueta(nome_pasta: str) -> str:
    """'JPG LIMPO' -> 'limpo'; 'fotos para internet' -> 'internet'; 'TIFF'/'JPG' -> ''."""
    for t in _tokens(nome_pasta):
        for raiz, canonica in _ETIQUETAS:
            if t.startswith(raiz):
                return canonica
    return ""


def etiqueta_do_caminho(caminho: Path, raiz: Path | None = None) -> str:
    """Etiqueta pelas pastas de versão do caminho (na entrada) ou pela pasta do arquivo (no acervo)."""
    if raiz is not None:
        try:
            partes = caminho.parent.relative_to(raiz).parts
        except ValueError:
            partes = ()
        for parte in reversed(partes):
            if e_pasta_de_versao(parte) and etiqueta(parte):
                return etiqueta(parte)
        return ""
    return etiqueta(caminho.parent.name)


def pasta_de_versao(caminho: Path, raiz: Path) -> str:
    """As pastas de formato/versão do caminho relativo à unidade ('' = nenhuma)."""
    try:
        partes = caminho.parent.relative_to(raiz).parts
    except ValueError:
        return ""
    return "/".join(p for p in partes if e_pasta_de_versao(p))


def radical(stem: str) -> str:
    """Nome sem sufixos de versão nem zeros à esquerda: 'Prancha_01 LIMPO' -> 'prancha 1'."""
    tokens = _tokens_nome(stem)
    while len(tokens) > 1 and tokens[-1] in SUFIXOS_VERSAO:
        tokens.pop()
    return " ".join(str(int(t)) if t.isdigit() else t for t in tokens)


def _tokens_nome(stem: str) -> list[str]:
    from .estrutura import normalizar

    return normalizar(stem).split()


def formato_da_versao(caminho: Path, etiqueta_: str) -> str:
    """'jpg limpo', 'jpg internet', 'tif', 'dng' (rótulo do contrato 2.8, campo `versoes[].formato`)."""
    base = origem_formato(caminho)
    return f"{base} {etiqueta_}".strip() if etiqueta_ and etiqueta_ != "matriz" else base


def _rank_matriz(caminho: Path, etiqueta_: str) -> tuple:
    ext = caminho.suffix.lower()
    return (_ORDEM_MATRIZ_EXT.get(ext, 9),
            _ORDEM_MATRIZ_ETIQUETA.get(etiqueta_, 2), str(caminho))


@dataclass
class Documento:
    """Um documento físico e todas as suas versões digitais."""

    base: str
    versoes: list[Path] = field(default_factory=list)
    # Etiqueta de cada versão ("limpo", "internet", ...); sem entrada = pela pasta do arquivo.
    etiquetas: dict = field(default_factory=dict)

    def etiqueta_de(self, p: Path) -> str:
        return self.etiquetas[p] if p in self.etiquetas else etiqueta_do_caminho(p)

    @property
    def para_ler(self) -> Path:
        def chave(p: Path):
            et = self.etiqueta_de(p)
            pasta_simples = p.parent.name.upper() == _pasta_formato(p.suffix)
            return (PREFERENCIA.index(p.suffix.lower()), _ORDEM_LEITURA_ETIQUETA.get(et, 1),
                    0 if pasta_simples else 1, str(p))
        return sorted(self.versoes, key=chave)[0]

    @property
    def por_matriz(self) -> list[Path]:
        """Versões da matriz para baixo (TIFF > DNG > JPG LIMPO > JPG > outros; alta > internet)."""
        return sorted(self.versoes, key=lambda p: _rank_matriz(p, self.etiqueta_de(p)))

    def papeis(self) -> dict:
        """{versão: {"formato", "papel", "subpasta"}}. `subpasta` é a pasta de formato no acervo:
        a melhor versão de cada extensão fica na pasta de sempre (TIF/, JPG/...); as outras em
        "JPG LIMPO", "JPG INTERNET"... (ou "JPG V2" sem etiqueta), com o MESMO código."""
        saida, usadas = {}, set()
        for i, p in enumerate(self.por_matriz):
            et = self.etiqueta_de(p)
            fmt = _pasta_formato(p.suffix)
            sub, ext = fmt, p.suffix.lower()
            if (sub, ext) in usadas:
                sub, n = (f"{fmt} {et.upper()}" if et else ""), 2
                while not sub or (sub, ext) in usadas:
                    sub, n = f"{fmt} V{n}", n + 1
            usadas.add((sub, p.suffix.lower()))
            papel = "matriz" if i == 0 else ("publicacao" if et in PUBLICACAO else "derivado")
            saida[p] = {"formato": formato_da_versao(p, et), "papel": papel, "subpasta": sub}
        return saida


def _pasta_formato(ext: str) -> str:
    from .estrutura import pasta_formato

    return pasta_formato(ext)


def agrupar(arquivos: list[Path], grupo=None, raiz: Path | None = None, assinatura=None) -> list[Documento]:
    """Junta as versões de cada documento, mantendo a ordem da pasta original.

    1. mesmo nome-base (como sempre: `scan 001.tif` + `scan 001.jpg`);
    2. com `raiz` (a unidade): entre pastas de formato/versão DIFERENTES, o mesmo radical
       do nome ('Prancha 01.tif' + 'prancha_01_limpo.jpg'), só quando é inequívoco;
    3. senão, a mesma IMAGEM: dHash da imagem bruta nas 4 rotações (≤ 6 bits) com
       proporção parecida, par mútuo e inequívoco; a ordem na pasta só desempata.
    Nunca junta dois arquivos da MESMA pasta de versão (são folhas diferentes).

    `grupo(arquivo)` separa universos: lote do Mac com várias séries tem `scan 001`
    em Negativos e `scan 001` em Slides — documentos diferentes, não versões.
    `assinatura(arquivo)` -> (hashes das 4 rotações, proporção) ou None (testes trocam).
    """
    por_base: dict[str, Documento] = {}
    for arquivo in sorted(arquivos, key=lambda p: (grupo(p) if grupo else "", p.stem.lower(), str(p))):
        chave = (f"{grupo(arquivo)}|" if grupo else "") + arquivo.stem.lower()
        por_base.setdefault(chave, Documento(arquivo.stem)).versoes.append(arquivo)
    documentos = list(por_base.values())
    if raiz is None:
        return documentos
    for doc in documentos:
        doc.etiquetas = {v: etiqueta_do_caminho(v, raiz) for v in doc.versoes}
    universo = (lambda d: grupo(d.versoes[0])) if grupo else (lambda d: "")
    _pastas: dict[int, frozenset] = {}

    def pastas(d: Documento) -> frozenset:
        if id(d) not in _pastas:
            _pastas[id(d)] = frozenset(pasta_de_versao(v, raiz) for v in d.versoes)
        return _pastas[id(d)]

    if len({p for d in documentos for p in pastas(d)}) < 2:
        return documentos   # sem pastas de versão: nada muda

    def juntar(a: Documento, b: Documento) -> None:
        a.versoes += b.versoes
        a.etiquetas.update(b.etiquetas)
        documentos.remove(b)
        _pastas.pop(id(a), None)

    # 2. radical do nome, entre pastas de versão diferentes
    por_radical: dict[tuple, list[Documento]] = {}
    for doc in documentos:
        r = radical(doc.base)
        if r:
            por_radical.setdefault((universo(doc), r), []).append(doc)
    for docs in por_radical.values():
        if len(docs) < 2:
            continue
        vistas: set[str] = set()
        disjuntas = True
        for d in docs:
            ps = pastas(d)
            if ps & vistas:
                disjuntas = False
                break
            vistas |= ps
        if disjuntas:
            for d in docs[1:]:
                juntar(docs[0], d)

    # 3. mesma imagem (dHash bruto nas 4 rotações). Para cada documento e cada OUTRA pasta de
    # versão, o candidato mais parecido dessa pasta (único; a ordem na pasta só desempata o que a
    # imagem já indicou). Só vale par MÚTUO, e a junção nunca põe duas folhas da mesma pasta juntas.
    assinatura = assinatura or assinatura_da_imagem
    todas = sorted({p for d in documentos for p in pastas(d)})
    ordem: dict[tuple, int] = {}
    for pasta in todas:
        nela = sorted((d for d in documentos if pasta in pastas(d)),
                      key=lambda d: min(str(v) for v in d.versoes if pasta_de_versao(v, raiz) == pasta))
        for i, d in enumerate(nela):
            ordem[(id(d), pasta)] = i
    cache: dict[int, object] = {}

    def assin(d: Documento):
        if id(d) not in cache:
            leve = Documento(d.base, d.versoes, d.etiquetas).para_ler
            cache[id(d)] = assinatura(leve) if leve.suffix.lower() in _DE_IMAGEM else None
        return cache[id(d)]

    distancias: dict[tuple, int] = {}

    def distancia(a: Documento, b: Documento) -> int:
        chave = (id(a), id(b)) if id(a) < id(b) else (id(b), id(a))
        if chave not in distancias:
            sa, sb = assin(a), assin(b)
            melhor = 99
            if sa and sb:
                (hashes_a, prop_a), (hashes_b, prop_b) = sa, sb
                for giro, h in hashes_a.items():
                    prop = prop_a if giro in (0, 180) else (1 / prop_a if prop_a else 0)
                    if abs(prop - prop_b) <= TOLERANCIA_PROPORCAO * max(prop, prop_b, 1e-6):
                        melhor = min(melhor, bin(h ^ hashes_b[0]).count("1"))
            distancias[chave] = melhor
        return distancias[chave]

    def melhor_em(a: Documento, pasta: str) -> Documento | None:
        pares = [(distancia(a, b), b) for b in documentos
                 if b is not a and pasta in pastas(b) and not (pastas(a) & pastas(b))
                 and universo(a) == universo(b)]
        pares = [(x, b) for x, b in pares if x <= LIMITE_DHASH]
        if not pares:
            return None
        minimo = min(x for x, _b in pares)
        empatados = [b for x, b in pares if x == minimo]
        if len(empatados) > 1:
            meus = {ordem.get((id(a), p)) for p in pastas(a)}
            empatados = [b for b in empatados if ordem.get((id(b), pasta)) in meus]
        return empatados[0] if len(empatados) == 1 else None

    sozinhos = [d for d in documentos if len(pastas(d)) == 1]
    arestas = []
    if sozinhos:   # todo documento já casado pelo nome em todas as pastas: nenhuma imagem é aberta
        escolha = {}
        for a in documentos:
            for pasta in todas:
                if pasta not in pastas(a):
                    escolha[(id(a), pasta)] = melhor_em(a, pasta)
        for a in documentos:
            for pasta in todas:
                b = escolha.get((id(a), pasta))
                if b is not None and id(a) < id(b) and any(escolha.get((id(b), p)) is a for p in pastas(a)):
                    arestas.append((distancia(a, b), a, b))
    # junta (union-find) da mais parecida para a menos, sem nunca juntar duas folhas da mesma pasta
    dono: dict[int, Documento] = {id(d): d for d in documentos}

    def raiz_de(d: Documento) -> Documento:
        while dono[id(d)] is not d:
            d = dono[id(d)]
        return d

    for _x, a, b in sorted(arestas, key=lambda t: (t[0], str(t[1].versoes[0]), str(t[2].versoes[0]))):
        ra, rb = raiz_de(a), raiz_de(b)
        if ra is rb or (pastas(ra) & pastas(rb)):
            continue
        base, outro = (ra, rb) if _rank_matriz(ra.por_matriz[0], ra.etiqueta_de(ra.por_matriz[0])) <= \
            _rank_matriz(rb.por_matriz[0], rb.etiqueta_de(rb.por_matriz[0])) else (rb, ra)
        juntar(base, outro)
        dono[id(outro)] = base
    return documentos


def _hashes_4(img: Image.Image) -> dict[int, int]:
    from .preparo import dhash

    base = img.convert("L")
    return {g: int(dhash(base.rotate(-g, expand=True) if g else base), 16) for g in (0, 90, 180, 270)}


def assinatura_da_imagem(caminho: Path):
    """(dHash das 4 rotações da imagem BRUTA, largura/altura) — None se não abre ou não tem informação
    (folha lisa dá dHash ~0 e casaria com tudo)."""
    try:
        img = _abrir_leve(caminho)
    except (OSError, ValueError, SyntaxError):
        return None
    if img is None:
        return None
    hashes = _hashes_4(img)
    bits = bin(hashes[0]).count("1")
    if bits < 4 or bits > 60:
        return None
    return hashes, img.width / max(1, img.height)


def _abrir_leve(caminho: Path) -> Image.Image | None:
    if caminho.suffix.lower() in (".dng", ".nef"):
        img = _dng(caminho)
    else:
        img = Image.open(caminho)
        img.draft("RGB", (512, 512))
        img.load()
    if img is None:
        return None
    try:
        from PIL import ImageOps

        img = ImageOps.exif_transpose(img) or img
    except (OSError, ValueError, KeyError):
        pass
    img = para_rgb(img) if img.mode not in ("RGB", "L") else img
    img.thumbnail((512, 512))
    return img


def giro_relativo(versao: Path, referencia: Path) -> int | None:
    """Quanto girar (graus, horário, como preparo.transformar) a `versao` para ficar como a
    `referencia`. None = não deu para saber (a versão não é girada à toa)."""
    a, b = assinatura_da_imagem(versao), assinatura_da_imagem(referencia)
    if not a or not b:
        return None
    (ha, _pa), (hb, _pb) = a, b
    dist = {g: bin(h ^ hb[0]).count("1") for g, h in ha.items()}
    melhor = min(dist.values())
    if melhor > LIMITE_DHASH:
        return None
    giros = [g for g, d in dist.items() if d == melhor]
    return 0 if 0 in giros else (giros[0] if len(giros) == 1 else None)


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
