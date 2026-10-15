"""Leva documentos de um projeto para outro: da pasta provisória (P9xxx) para o
projeto decidido pelo carimbo, ou de um projeto errado para o certo
(`--reidentificar`).

Move (mesmo volume, não copia) e renumera o D no projeto de destino; leva junto
mapa_origem, preparo, checkpoint de leitura, cache, preview e o JSON dos lotes.
Arquivo idêntico (mesmo SHA-256) que o destino já tem não é duplicado: o
documento passa a ser o que já está lá. Nada é apagado do acervo: a cópia
provisória redundante só sai porque o destino já tem os mesmos bytes.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import shutil
from pathlib import Path

from . import estrutura
from .atomico import gravar_texto
from .lote import NOME_CHECKPOINT

_log = logging.getLogger("cv2.transferir")


def _ler(caminho: Path, padrao):
    try:
        return json.loads(caminho.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return padrao


def _gravar(caminho: Path, dados) -> None:
    caminho.parent.mkdir(parents=True, exist_ok=True)
    tmp = caminho.with_name(f".{caminho.name}.tmp")
    tmp.write_text(json.dumps(dados, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, caminho)


def _sha256(caminho: Path) -> str:
    h = hashlib.sha256()
    with open(caminho, "rb") as f:
        for bloco in iter(lambda: f.read(1 << 20), b""):
            h.update(bloco)
    return h.hexdigest()


def _mesmo_destino(item, raiz_final: Path, alvo: Path) -> bool:
    """O mapa do destino diz que ESTE conteúdo (mesmo SHA de origem) foi copiado para `alvo`."""
    if not isinstance(item, dict) or not item.get("destino"):
        return False
    try:
        return (raiz_final / item["destino"]).resolve() == alvo.resolve()
    except OSError:
        return False


def _trocar(texto: str, trocas: list[tuple[str, str]]) -> str:
    for antigo, novo in trocas:
        texto = texto.replace(antigo, novo)
    return texto


def transferir(raiz_final: Path, pasta_estado: Path, origem: Path, destino: Path, documentos: list[str],
               codigo_destino: str, livro=None, motivo: str = "") -> tuple[dict[str, str], dict[Path, Path]]:
    """Leva `documentos` (códigos em `origem`) para `destino`. Devolve ({doc antigo: novo}, {arquivo antigo: novo})."""
    codigo_origem = estrutura.codigo_da_pasta(origem)
    cat_o, cat_d = origem / "catalogacao", destino / "catalogacao"
    mapa_o = _ler(cat_o / "mapa_origem.json", {}) or {}
    mapa_d = _ler(cat_d / "mapa_origem.json", {}) or {}
    docs = set(documentos)

    def rel(p: Path) -> str:
        try:
            return str(p.relative_to(raiz_final))
        except ValueError:
            return str(p)

    # 1. Código novo de cada documento (ou o que o destino já tem, se os bytes são os mesmos).
    existente_por_sha = {sha: Path(item["destino"]).stem for sha, item in mapa_d.items()
                        if (raiz_final / item["destino"]).exists()}
    proximo = estrutura.proximo_documento(destino)
    trocas: dict[str, str] = {}
    for doc in sorted(docs):
        shas = [sha for sha, item in mapa_o.items() if Path(item["destino"]).stem == doc]
        ja = next((existente_por_sha[s] for s in shas if s in existente_por_sha), None)
        if ja:
            trocas[doc] = ja
            continue
        achado = estrutura.CODIGO_DOCUMENTO.match(doc)
        if not achado:
            continue
        _f, _p, ano, serie, _n = achado.groups()
        trocas[doc] = estrutura.codigo_documento(codigo_destino, ano, serie.upper(), proximo)
        proximo += 1

    # 2. Arquivos.
    movidos: dict[Path, Path] = {}
    for arquivo in sorted(origem.rglob("*")):
        if not arquivo.is_file() or "catalogacao" in arquivo.relative_to(origem).parts:
            continue
        novo = trocas.get(arquivo.stem)
        if not novo:
            continue
        alvo = estrutura.destino_documento(destino, novo.split("-")[3], novo, arquivo.suffix)
        if alvo.exists():
            # Só apaga a cópia provisória se o SHA-256 PROVA que o destino tem os mesmos bytes
            # (ou o mapa do destino registra este mesmo conteúdo ali): tamanho igual não basta.
            soma = _sha256(arquivo)
            if soma == _sha256(alvo) or _mesmo_destino(mapa_d.get(soma), raiz_final, alvo):
                arquivo.unlink()
                movidos[arquivo] = alvo
                continue
            raise FileExistsError(f"{alvo.name} já existe com outro conteúdo — transferência abortada")
        alvo.parent.mkdir(parents=True, exist_ok=True)
        os.replace(arquivo, alvo)
        movidos[arquivo] = alvo
    trocas_texto = [(rel(a), rel(b)) for a, b in movidos.items()]
    trocas_texto += sorted(trocas.items(), key=lambda t: -len(t[0]))

    # 3. mapa_origem e preparo.
    for sha in [s for s, item in mapa_o.items() if Path(item["destino"]).stem in docs]:
        item = mapa_o.pop(sha)
        item["destino"] = _trocar(item["destino"], trocas_texto)
        mapa_d.setdefault(sha, item)
    _gravar(cat_o / "mapa_origem.json", mapa_o)
    _gravar(cat_d / "mapa_origem.json", mapa_d)
    prep_o = _ler(cat_o / "preparo.json", {}) or {}
    prep_d = _ler(cat_d / "preparo.json", {}) or {}
    prev_o = raiz_final / "_campvision" / "preview" / codigo_origem
    prev_d = raiz_final / "_campvision" / "preview" / codigo_destino
    for doc in sorted(docs & set(prep_o)):
        dados = prep_o.pop(doc)
        novo = trocas.get(doc, doc)
        if (prev_o / f"{doc}.jpg").exists():
            prev_d.mkdir(parents=True, exist_ok=True)
            os.replace(prev_o / f"{doc}.jpg", prev_d / f"{novo}.jpg")
            dados["preview"] = rel(prev_d / f"{novo}.jpg")
        prep_d.setdefault(novo, dados)
    _gravar(cat_o / "preparo.json", prep_o)
    _gravar(cat_d / "preparo.json", prep_d)

    # 4. Leituras já feitas (checkpoint) e cache: o destino não paga a leitura de novo.
    ck_o, ck_d = origem / NOME_CHECKPOINT, destino / NOME_CHECKPOINT
    if ck_o.exists():
        ficam, vao = [], []
        for linha in ck_o.read_text(encoding="utf-8").splitlines():
            try:
                stem = Path(json.loads(linha).get("arquivo", "")).stem
            except ValueError:
                continue
            (vao if stem in docs else ficam).append(_trocar(linha, trocas_texto) if stem in docs else linha)
        gravar_texto(ck_o, "".join(f"{l}\n" for l in ficam))
        if vao:
            with ck_d.open("a", encoding="utf-8") as f:
                f.writelines(f"{l}\n" for l in vao)
    cache_o = pasta_estado / "leitura" / codigo_origem
    cache_d = pasta_estado / "leitura" / codigo_destino
    for doc in docs:
        if (cache_o / f"{doc}.jpg").exists():
            cache_d.mkdir(parents=True, exist_ok=True)
            os.replace(cache_o / f"{doc}.jpg", cache_d / f"{trocas.get(doc, doc)}.jpg")

    # 5. JSON dos lotes (rastreio: de que pasta veio cada arquivo).
    for lote in (cat_o / "lotes").glob("*.json") if (cat_o / "lotes").is_dir() else []:
        alvo = cat_d / "lotes" / lote.name
        alvo.parent.mkdir(parents=True, exist_ok=True)
        dados = _ler(lote, {}) or {}
        dados["arquivos"] = [a for a in dados.get("arquivos", []) if a.get("codigo") in docs]
        texto = _trocar(json.dumps(dados, ensure_ascii=False, indent=1), trocas_texto)
        gravar_texto(alvo, texto)

    if livro is not None:
        for antigo, atual in movidos.items():
            livro.anotar("refeito", codigo_projeto=codigo_destino, codigo_documento=atual.stem,
                         arquivo_origem=rel(antigo), arquivo_destino=rel(atual), nome_original=antigo.name,
                         detalhe=motivo or f"transferido de {codigo_origem}")
    _log.info("%d documento(s) de %s → %s (%s).", len(trocas), codigo_origem, codigo_destino, motivo)
    return trocas, movidos


def limpar_provisoria(pasta: Path, guardar_em: Path | None = None) -> None:
    """Pasta provisória vazia de documentos: guarda a catalogação dela (rastreio) e sai."""
    sobrou = [a for a in pasta.rglob("*") if a.is_file() and "catalogacao" not in a.relative_to(pasta).parts
              and a.name not in (estrutura.MARCA_UNIDADE, NOME_CHECKPOINT, "status.json", "README.md")]
    if sobrou:
        _log.warning("Pasta provisória %s ainda tem %d arquivo(s): fica.", pasta.name, len(sobrou))
        return
    if guardar_em is not None and (pasta / "catalogacao").is_dir():
        guardar_em.mkdir(parents=True, exist_ok=True)
        for arquivo in (pasta / "catalogacao").iterdir():
            if arquivo.is_file():
                shutil.copy2(arquivo, guardar_em / arquivo.name)
    shutil.rmtree(pasta, ignore_errors=True)
