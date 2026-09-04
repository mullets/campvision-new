"""Auto-atualização pelo GitHub.

Regra de ouro: **nunca no meio de um lote**. O vigia só chama isto entre
projetos, com a fila parada. Se veio código novo, o processo se reinicia
sozinho (`os.execv`) para carregar o código atualizado — o LaunchAgent não
precisa saber de nada.

Só faz `pull --ff-only`: se você tiver mexido em algo local e isso conflitar,
a atualização é abortada com aviso, sem sobrescrever seu trabalho.
"""

from __future__ import annotations

import logging
import os
import subprocess
import sys
from pathlib import Path

_log = logging.getLogger("cv2.atualizador")

TEMPO_LIMITE = 90


def _git(repo: Path, *argumentos: str) -> tuple[int, str]:
    try:
        proc = subprocess.run(
            ["git", "-C", str(repo), *argumentos],
            capture_output=True, text=True, timeout=TEMPO_LIMITE,
        )
    except (OSError, subprocess.TimeoutExpired) as erro:
        return 1, str(erro)
    return proc.returncode, (proc.stdout + proc.stderr).strip()


def e_repositorio(repo: Path) -> bool:
    return (repo / ".git").exists()


def ha_mudancas_locais(repo: Path) -> bool:
    codigo, saida = _git(repo, "status", "--porcelain")
    return codigo == 0 and bool(saida.strip())


def verificar(repo: Path) -> tuple[bool, str]:
    """Devolve (tem_novidade, mensagem). Não altera nada."""
    if not e_repositorio(repo):
        return False, "não é um repositório git"
    codigo, saida = _git(repo, "fetch", "--quiet")
    if codigo != 0:
        return False, f"fetch falhou: {saida[:160]}"
    codigo, local = _git(repo, "rev-parse", "HEAD")
    _, remoto = _git(repo, "rev-parse", "@{u}")
    if codigo != 0 or not remoto or remoto.startswith("fatal"):
        return False, "sem branch remota configurada"
    if local == remoto:
        return False, "já está atualizado"
    return True, f"{local[:7]} -> {remoto[:7]}"


def atualizar(repo: Path) -> tuple[bool, str]:
    """Faz o pull. Devolve (atualizou, mensagem)."""
    tem, mensagem = verificar(repo)
    if not tem:
        return False, mensagem
    if ha_mudancas_locais(repo):
        return False, "há alterações locais não commitadas — atualização pulada"
    codigo, saida = _git(repo, "pull", "--ff-only", "--quiet")
    if codigo != 0:
        return False, f"pull falhou: {saida[:160]}"
    _log.info("Código atualizado: %s", mensagem)
    return True, mensagem


def reiniciar_processo() -> None:  # pragma: no cover - troca o processo
    """Substitui o processo atual pelo mesmo comando, já com o código novo."""
    _log.info("Reiniciando para carregar o código atualizado…")
    sys.stdout.flush()
    sys.stderr.flush()
    os.execv(sys.executable, [sys.executable, *sys.argv])
