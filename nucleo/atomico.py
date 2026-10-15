"""Gravação atômica: escreve num temporário ao lado e troca por rename.

Quem lê (o painel, o watcher do QNAP, outra thread) nunca pega o arquivo pela
metade, e uma queda no meio da escrita deixa o arquivo ANTIGO intacto.
"""

from __future__ import annotations

import json
import os
from pathlib import Path


def gravar_texto(caminho: Path, texto: str) -> None:
    caminho = Path(caminho)
    caminho.parent.mkdir(parents=True, exist_ok=True)
    temporario = caminho.with_name(f".{caminho.name}.tmp")
    try:
        with open(temporario, "w", encoding="utf-8") as f:
            f.write(texto)
            f.flush()
            os.fsync(f.fileno())
        os.replace(temporario, caminho)
    except BaseException:
        temporario.unlink(missing_ok=True)
        raise


def gravar_json(caminho: Path, dados, indent: int = 2) -> None:
    gravar_texto(caminho, json.dumps(dados, ensure_ascii=False, indent=indent))
