"""Diário de bordo do vigia.

Uma linha JSON por projeto processado. É o que alimenta o relatório diário e
o que permite responder "o que rodou ontem?" sem depender de log de texto.
Arquivo append-only: nunca é reescrito, só cresce, e sobrevive a reinício.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import threading
from dataclasses import asdict, dataclass, field
from datetime import date, datetime
from pathlib import Path

_log = logging.getLogger("cv2.eventos")
_trava = threading.Lock()


@dataclass
class Evento:
    quando: str = field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))
    projeto: str = ""
    pasta: str = ""
    pranchas: int = 0
    com_carimbo: int = 0
    erros: int = 0
    campos_a_revisar: int = 0
    custo_usd: float = 0.0
    duracao_segundos: int = 0
    falha: str = ""

    @property
    def data(self) -> date:
        try:
            return datetime.fromisoformat(self.quando).date()
        except ValueError:
            return date.min


def registrar(caminho: Path, evento: Evento) -> None:
    caminho.parent.mkdir(parents=True, exist_ok=True)
    with _trava, caminho.open("a", encoding="utf-8") as f:
        f.write(json.dumps(asdict(evento), ensure_ascii=False) + "\n")


# Contrato 2.6 (02/11/2026, Rafael: "esses relatórios do campvision, consigo acessar no meu painel? / seria bom saber tudo que ele fez
# resumidamente e o valor gasto / no painel de um fundo, o quanto já gastamos com ele"): cópia do diário no acervo, que o painel lê.
ESPELHO = Path("_campvision") / "eventos.jsonl"


def espelhar(local: Path, raiz_final) -> bool:
    """Copia o diário inteiro para ACERVOS_CAMP/_campvision/eventos.jsonl (troca atômica: o painel nunca lê pela metade).
    QNAP fora ou sem escrita: False, sem erro (o diário local continua sendo a fonte; a próxima cópia leva tudo)."""
    if not raiz_final or not local.exists():
        return False
    raiz = Path(raiz_final)
    if not raiz.is_dir():
        return False
    destino = raiz / ESPELHO
    tmp = destino.with_name(destino.name + ".tmp")
    try:
        destino.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(local, tmp)
        os.replace(tmp, destino)
        return True
    except OSError as erro:
        _log.warning("Diário de eventos não copiado para o acervo: %s", erro)
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        return False


def ler(caminho: Path, do_dia: date | None = None) -> list[Evento]:
    """Lê os eventos, opcionalmente filtrando por dia. Linha corrompida é pulada."""
    if not caminho.exists():
        return []
    eventos: list[Evento] = []
    for linha in caminho.read_text(encoding="utf-8").splitlines():
        linha = linha.strip()
        if not linha:
            continue
        try:
            dados = json.loads(linha)
        except json.JSONDecodeError:
            _log.warning("Linha inválida em %s, ignorada.", caminho.name)
            continue
        evento = Evento(**{k: v for k, v in dados.items() if k in Evento.__annotations__})
        if do_dia is None or evento.data == do_dia:
            eventos.append(evento)
    return eventos
