"""JPG de cada documento do acervo: se falta, gera a partir do master (TIF/DNG/PDF/...).

Vai para <projeto>/<série>/JPG/<código>.jpg (mesmo código do master), 6000 px no
lado maior, JPEG 92, sRGB, com o metadado CAMP copiado do master. Entra no
mapa_origem.json (para o pacote e o painel acharem) e no livro como `refeito`.
Nada é apagado nem sobrescrito: JPG que já existe fica como está.
"""

from __future__ import annotations

import hashlib
import json
import logging
import shutil
import subprocess
from pathlib import Path

from . import estrutura, formatos

_log = logging.getLogger("cv2.derivados")
LADO_JPG = 6000
QUALIDADE_JPG = 92
ORDEM_MASTER = (".tif", ".tiff", ".dng", ".nef", ".png", ".pdf", ".cdr", ".plt")


def _sha(caminho: Path) -> str:
    h = hashlib.sha256()
    with caminho.open("rb") as f:
        for bloco in iter(lambda: f.read(1 << 20), b""):
            h.update(bloco)
    return h.hexdigest()


def precisa_girar(prep: dict) -> bool:
    """Giro/espelho decidido COM CERTEZA e ainda não aplicado ao JPG."""
    return bool(prep) and not prep.get("incerta") and not prep.get("jpg_orientado") \
        and (int(prep.get("rotacao") or 0) % 360 != 0 or bool(prep.get("espelhada")))


def orientar_jpg(jpg: Path, prep: dict) -> bool:
    """Grava o JPG já girado/desespelhado (o master TIF/DNG/PDF nunca é tocado).

    Só com orientação certa; incerta fica para a folha de contatos. Mantém o
    metadado (exiftool copia de volta) e o perfil de cor.
    """
    from PIL import Image

    from .preparo import Orientacao, transformar

    if not precisa_girar(prep):
        return False
    o = Orientacao(rotacao=int(prep.get("rotacao") or 0) % 360, espelhada=bool(prep.get("espelhada")))
    temporario = jpg.with_name(f".{jpg.stem}.girando.jpg")
    with Image.open(jpg) as img:
        icc = img.info.get("icc_profile")
        novo = transformar(img.convert("RGB"), o)
        novo.save(temporario, "JPEG", quality=QUALIDADE_JPG, optimize=True, **({"icc_profile": icc} if icc else {}))
    if shutil.which("exiftool"):
        subprocess.run(["exiftool", "-q", "-overwrite_original", "-TagsFromFile", str(jpg), "-all:all",
                        "-Orientation=", str(temporario)], capture_output=True, timeout=120)
    temporario.replace(jpg)
    return True


def orientar_jpgs_do_projeto(pasta: Path, raiz_final: Path, livro=None) -> list[str]:
    """Aplica a orientação do preparo.json aos JPG do projeto que ainda não foram girados."""
    caminho = pasta / "catalogacao" / "preparo.json"
    try:
        preparos = json.loads(caminho.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    girados = []
    for jpg in sorted(pasta.rglob("*.jp*g")):
        if "catalogacao" in jpg.relative_to(pasta).parts:
            continue
        prep = preparos.get(jpg.stem)
        if prep and orientar_jpg(jpg, prep):
            prep["jpg_orientado"] = True
            girados.append(jpg.stem)
            if livro is not None:
                rel = str(jpg.relative_to(raiz_final)) if jpg.is_relative_to(raiz_final) else str(jpg)
                livro.anotar("refeito", codigo_projeto=estrutura.codigo_da_pasta(pasta), codigo_documento=jpg.stem,
                             arquivo_destino=rel, detalhe=f"JPG girado {prep.get('rotacao', 0)}°"
                             + (" e desespelhado" if prep.get("espelhada") else ""))
    if girados:
        caminho.write_text(json.dumps(preparos, ensure_ascii=False, indent=1), encoding="utf-8")
    return girados


def gerar_jpgs(pasta: Path, raiz_final: Path, pasta_estado: Path, livro=None) -> list[str]:
    """Gera o JPG que falta de cada documento do projeto. Devolve os códigos gerados."""
    por_codigo: dict[str, list[Path]] = {}
    for arquivo in pasta.rglob("*"):
        if arquivo.is_file() and "catalogacao" not in arquivo.relative_to(pasta).parts \
                and estrutura.CODIGO_DOCUMENTO.match(arquivo.stem) and formatos.suportado(arquivo):
            por_codigo.setdefault(arquivo.stem, []).append(arquivo)
    mapa_caminho = pasta / "catalogacao" / "mapa_origem.json"
    try:
        mapa = json.loads(mapa_caminho.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        mapa = {}
    caminho_prep = pasta / "catalogacao" / "preparo.json"
    try:
        preparos = json.loads(caminho_prep.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        preparos = {}
    gerados: list[str] = []
    for codigo, versoes in sorted(por_codigo.items()):
        if any(v.suffix.lower() in (".jpg", ".jpeg") for v in versoes):
            continue
        master = sorted(versoes, key=lambda v: (ORDEM_MASTER.index(v.suffix.lower())
                                                if v.suffix.lower() in ORDEM_MASTER else 99, str(v)))[0]
        serie = estrutura.CODIGO_DOCUMENTO.match(codigo).group(4).upper()
        destino = estrutura.destino_documento(pasta, serie, codigo, ".jpg")
        if destino.exists():
            continue
        temporario = pasta_estado / "derivados" / f"{codigo}.jpg"
        temporario.parent.mkdir(parents=True, exist_ok=True)
        img = formatos.abrir(master, temporario)
        if img is None:
            _log.warning("%s: não abri %s para gerar o JPG.", codigo, master.name)
            continue
        img = img.convert("RGB")
        prep = preparos.get(codigo, {})
        if precisa_girar(prep):  # o JPG novo já nasce na orientação certa
            from .preparo import Orientacao, transformar

            img = transformar(img, Orientacao(rotacao=int(prep.get("rotacao") or 0) % 360,
                                              espelhada=bool(prep.get("espelhada"))))
            prep["jpg_orientado"] = True
        img.thumbnail((LADO_JPG, LADO_JPG))
        img.save(temporario, "JPEG", quality=QUALIDADE_JPG, optimize=True)
        img.close()
        if shutil.which("exiftool"):
            subprocess.run(["exiftool", "-q", "-overwrite_original", "-TagsFromFile", str(master),
                            "-XMP:all", "-IPTC:all", "-EXIF:Artist", "-EXIF:Copyright",
                            "-EXIF:ImageDescription", str(temporario)], capture_output=True, timeout=120)
        destino.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(temporario), destino)
        rel_master = str(master.relative_to(raiz_final)) if master.is_relative_to(raiz_final) else str(master)
        rel_destino = str(destino.relative_to(raiz_final)) if destino.is_relative_to(raiz_final) else str(destino)
        anterior = next((v for v in mapa.values() if v.get("destino") == rel_master), {})
        mapa[_sha(destino)] = {"destino": rel_destino, "origem": anterior.get("origem", rel_master),
                               "nome_original": anterior.get("nome_original", master.name),
                               "lote_id": "jpg-gerado", "origem_formato": "jpg",
                               "gerado_de": rel_master}
        if livro is not None:
            livro.anotar("refeito", codigo_projeto=estrutura.codigo_da_pasta(pasta), codigo_documento=codigo,
                         arquivo_origem=rel_master, arquivo_destino=rel_destino,
                         nome_original=master.name, detalhe=f"JPG gerado de {master.suffix.lstrip('.').upper()}")
        gerados.append(codigo)
    if gerados and preparos:
        caminho_prep.write_text(json.dumps(preparos, ensure_ascii=False, indent=1), encoding="utf-8")
    if gerados:
        mapa_caminho.parent.mkdir(parents=True, exist_ok=True)
        mapa_caminho.write_text(json.dumps(mapa, ensure_ascii=False, indent=1), encoding="utf-8")
    return gerados
