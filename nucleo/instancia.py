"""Lock de instância: um serviço por máquina, e conserto não roda em cima de lote.

`~/.campvision2/vigia.lock` com flock:
- o serviço (vigia.py sem comando) pega o lock EXCLUSIVO e o segura até sair;
- comando de conserto (reclassificar, reidentificar --sim, organizar-formatos,
  consertar-acervo --sim) recusa se o serviço está PROCESSANDO um lote, e enquanto
  roda segura o lock COMPARTILHADO para o serviço não subir por cima dele.

flock morre junto com o processo: não sobra lock velho depois de queda.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

try:  # Windows não tem fcntl; o serviço só roda em Linux/macOS
    import fcntl
except ImportError:  # pragma: no cover
    fcntl = None

NOME_LOCK = "vigia.lock"
# situações em que mexer no acervo é perigoso (o vigia está com um lote nas mãos)
OCUPADO = ("processando", "copiando", "preparando", "gravando", "consolidando", "relendo", "montando")


def _abrir(pasta_estado: Path):
    pasta_estado.mkdir(parents=True, exist_ok=True)
    return open(pasta_estado / NOME_LOCK, "a+")


def travar_servico(pasta_estado: Path):
    """Lock exclusivo do serviço. Devolve o arquivo (guarde até o fim) ou None se já há outro."""
    if fcntl is None:
        return open(Path(pasta_estado) / NOME_LOCK, "a+") if Path(pasta_estado).is_dir() else None
    arquivo = _abrir(Path(pasta_estado))
    try:
        fcntl.flock(arquivo, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        arquivo.close()
        return None
    arquivo.seek(0)
    arquivo.truncate()
    arquivo.write(f"{__import__('os').getpid()}\n")
    arquivo.flush()
    return arquivo


def servico_processando(pasta_estado: Path, idade_maxima: float = 180.0) -> str:
    """Situação do serviço se ele está no meio de um lote (estado.json recente), senão ''."""
    try:
        dados = json.loads((Path(pasta_estado) / "estado.json").read_text(encoding="utf-8"))
        mtime = (Path(pasta_estado) / "estado.json").stat().st_mtime
    except (OSError, ValueError):
        return ""
    if time.time() - mtime > idade_maxima:
        return ""  # estado velho: o serviço não está de pé
    situacao = str(dados.get("situacao") or "")
    return situacao if situacao.lower().startswith(OCUPADO) else ""


def travar_conserto(pasta_estado: Path):
    """Para comandos de conserto. Devolve (arquivo_do_lock | None, mensagem_de_recusa).

    Mensagem vazia = pode rodar (e `arquivo_do_lock` precisa ficar aberto até o fim).
    """
    pasta_estado = Path(pasta_estado)
    ocupado = servico_processando(pasta_estado)
    if ocupado:
        return None, (f"o serviço está processando um lote ({ocupado}). Espere terminar, ou pare o "
                      "serviço (sudo systemctl stop campvision2) antes de mexer no acervo.")
    if fcntl is None:
        return None, ""
    arquivo = _abrir(pasta_estado)
    try:
        fcntl.flock(arquivo, fcntl.LOCK_SH | fcntl.LOCK_NB)
    except OSError:
        # o serviço está de pé e ocioso (segura o lock exclusivo): pode consertar, mas avisa
        arquivo.close()
        return None, ""
    return arquivo, ""
