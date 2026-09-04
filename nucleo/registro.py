"""Log com o nome do arquivo em toda linha.

Herdado do app antigo porque provou valor: sem isso, com várias threads
processando pranchas diferentes, o log vira uma sopa impossível de diagnosticar.
O filtro é attachado aos HANDLERS (não ao logger raiz), senão registros vindos
de loggers filhos como "cv2.visao" passam sem o prefixo.
"""

from __future__ import annotations

import contextvars
import logging
from contextlib import contextmanager
from pathlib import Path

_arquivo_atual: contextvars.ContextVar[str] = contextvars.ContextVar("arquivo", default="")


class FiltroArquivo(logging.Filter):
    def filter(self, registro: logging.LogRecord) -> bool:
        nome = _arquivo_atual.get()
        if nome and not str(registro.msg).startswith("["):
            registro.msg = f"[{nome}] {registro.msg}"
        return True


@contextmanager
def arquivo_em_processamento(nome: str):
    token = _arquivo_atual.set(nome)
    try:
        yield
    finally:
        _arquivo_atual.reset(token)


def configurar(caminho_log: Path | None = None, nivel: int = logging.INFO) -> logging.Logger:
    logger = logging.getLogger("cv2")
    logger.setLevel(nivel)
    logger.handlers.clear()
    formato = logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s", "%H:%M:%S")

    console = logging.StreamHandler()
    console.setFormatter(formato)
    console.addFilter(FiltroArquivo())
    logger.addHandler(console)

    if caminho_log:
        caminho_log.parent.mkdir(parents=True, exist_ok=True)
        arquivo = logging.FileHandler(caminho_log, encoding="utf-8")
        arquivo.setFormatter(formato)
        arquivo.addFilter(FiltroArquivo())
        logger.addHandler(arquivo)

    logger.propagate = False
    return logger
