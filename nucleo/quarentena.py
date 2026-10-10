"""Quarentena da entrada (ticket 87, decisão do Rafa em 10/10).

O original em `100 - Scanners` não é mais apagado logo depois da cópia
conferida. Ele vai para `100 - Scanners/_conferidos/AAAA-MM-DD/<lote>/`, que fica
no mesmo volume (é renomear, não copiar), e só é apagado quando:

1. a RÉPLICA do ACERVOS_CAMP confirmou: a sentinela
   (`ACERVOS_CAMP/_campvision/sentinela.json`, regravada a cada rodada) vista na
   pasta da réplica tem `gravado_em` igual ou posterior ao momento da quarentena; e
2. passaram `quarentena_dias` (padrão 7). Com o disco acima de 85%, o que JÁ
   tem réplica confirmada pode sair antes dos N dias.

Sem `caminho_replica` configurado, nada é apagado. Nada em ACERVOS_CAMP é
apagado, nunca. Tudo vai para o livro: `quarentena` e `apagado_original`.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
from datetime import datetime, timedelta
from pathlib import Path

_log = logging.getLogger("cv2.quarentena")
PASTA = "_conferidos"
INDICE = "indice.jsonl"
SENTINELA = Path("_campvision") / "sentinela.json"
USO_ALTO = 0.85


def _agora() -> datetime:
    return datetime.now().astimezone()


def mover(origem: Path, raiz_entrada: Path, relativo: Path, extra: dict | None = None,
          agora: datetime | None = None) -> Path:
    """Move o original para a quarentena e anota no índice. Devolve o caminho novo."""
    agora = agora or _agora()
    destino = raiz_entrada / PASTA / agora.strftime("%Y-%m-%d") / relativo
    destino.parent.mkdir(parents=True, exist_ok=True)
    if destino.exists():
        destino = destino.with_name(f"{destino.stem}.{agora.strftime('%H%M%S')}{destino.suffix}")
    os.replace(origem, destino)
    linha = {"caminho": str(destino.relative_to(raiz_entrada)), "movido_em": agora.isoformat(timespec="seconds"),
             "tamanho": destino.stat().st_size, **(extra or {})}
    with (raiz_entrada / PASTA / INDICE).open("a", encoding="utf-8") as f:
        f.write(json.dumps(linha, ensure_ascii=False) + "\n")
    return destino


def gravar_sentinela(raiz_final: Path, versao: str, agora: datetime | None = None) -> None:
    caminho = raiz_final / SENTINELA
    caminho.parent.mkdir(parents=True, exist_ok=True)
    tmp = caminho.with_name(".sentinela.tmp")
    tmp.write_text(json.dumps({"gravado_em": (agora or _agora()).isoformat(timespec="seconds"),
                               "versao": versao}), encoding="utf-8")
    os.replace(tmp, caminho)


def replica_confirmada(caminho_replica: str | Path) -> datetime | None:
    """Data da sentinela na réplica (o momento até onde a réplica cobre tudo)."""
    if not caminho_replica:
        return None
    try:
        dados = json.loads((Path(caminho_replica) / SENTINELA).read_text(encoding="utf-8"))
        return datetime.fromisoformat(dados["gravado_em"])
    except (OSError, ValueError, KeyError, TypeError):
        return None


def _ler_indice(raiz_entrada: Path) -> list[dict]:
    caminho = raiz_entrada / PASTA / INDICE
    if not caminho.exists():
        return []
    linhas = []
    for bruta in caminho.read_text(encoding="utf-8").splitlines():
        try:
            linhas.append(json.loads(bruta))
        except ValueError:
            continue
    return linhas


def estado(raiz_entrada: Path, caminho_replica: str = "") -> dict:
    linhas = [l for l in _ler_indice(raiz_entrada) if (raiz_entrada / l["caminho"]).exists()]
    replica = replica_confirmada(caminho_replica)
    return {"arquivos": len(linhas), "gb": round(sum(l.get("tamanho", 0) for l in linhas) / 2**30, 2),
            "ultima_replica": replica.isoformat(timespec="seconds") if replica else None,
            "aviso": (f"quarentena sem réplica: {sum(l.get('tamanho', 0) for l in linhas) / 2**30:.1f} GB"
                      if linhas and not replica else "")}


def limpar(raiz_entrada: Path, caminho_replica: str, dias: int, livro=None,
           agora: datetime | None = None, uso_disco: float | None = None) -> int:
    """Apaga da quarentena só o que a réplica já cobre (e passou do prazo, ou o disco está cheio)."""
    replica = replica_confirmada(caminho_replica)
    if replica is None:
        return 0
    agora = agora or _agora()
    if uso_disco is None:
        try:
            u = shutil.disk_usage(raiz_entrada)
            uso_disco = u.used / u.total
        except OSError:
            uso_disco = 0.0
    linhas = _ler_indice(raiz_entrada)
    ficam, apagados = [], 0
    for l in linhas:
        caminho = raiz_entrada / l["caminho"]
        if not caminho.exists():
            continue
        movido = datetime.fromisoformat(l["movido_em"])
        coberto = replica >= movido
        vencido = agora - movido >= timedelta(days=dias)
        if coberto and (vencido or uso_disco >= USO_ALTO):
            try:
                caminho.unlink()
                apagados += 1
                if livro is not None:
                    livro.anotar("apagado_original", codigo_projeto=l.get("codigo_projeto", ""),
                                 codigo_documento=l.get("codigo_documento", ""), arquivo_origem=l["caminho"],
                                 tamanho=l.get("tamanho"), sha256=l.get("sha256", ""),
                                 detalhe=f"quarentena encerrada; réplica de {replica.isoformat(timespec='seconds')}")
                continue
            except OSError as erro:
                _log.warning("Não apaguei %s: %s", caminho, erro)
        ficam.append(l)
    if apagados:
        tmp = raiz_entrada / PASTA / f".{INDICE}.tmp"
        tmp.write_text("".join(json.dumps(l, ensure_ascii=False) + "\n" for l in ficam), encoding="utf-8")
        os.replace(tmp, raiz_entrada / PASTA / INDICE)
        for pasta in sorted((p for p in (raiz_entrada / PASTA).rglob("*") if p.is_dir()),
                            key=lambda p: len(p.parts), reverse=True):
            try:
                pasta.rmdir()
            except OSError:
                pass
    return apagados
