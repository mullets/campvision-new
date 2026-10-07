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


def _explicar_falha_de_fetch(saida: str) -> str:
    """Traduz o erro do git para algo acionável.

    O caso que mais morde: rodando como serviço, o processo não herda o
    ssh-agent do seu login, então a chave SSH não está disponível e o fetch
    falha calado a cada hora.
    """
    baixo = saida.lower()
    if "permission denied" in baixo or "publickey" in baixo:
        return (
            "fetch sem acesso SSH — como serviço o processo não herda o "
            "ssh-agent do login. Use remoto HTTPS com token, ou uma chave sem "
            "senha em ~/.ssh e GIT_SSH_COMMAND no plist"
        )
    if "could not resolve host" in baixo or "network is unreachable" in baixo:
        return "fetch sem rede — tentarei de novo no próximo ciclo"
    if "cannot run ssh" in baixo:
        return "fetch falhou: cliente ssh não encontrado no PATH do serviço"
    return f"fetch falhou: {saida[:160]}"


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
        return False, _explicar_falha_de_fetch(saida)
    codigo, local = _git(repo, "rev-parse", "HEAD")
    _, remoto = _git(repo, "rev-parse", "@{u}")
    if codigo != 0 or not remoto or remoto.startswith("fatal"):
        return False, "sem branch remota configurada"
    if local == remoto:
        return False, "já está atualizado"
    return True, f"{local[:7]} -> {remoto[:7]}"


def atualizar(repo: Path) -> tuple[bool, str]:
    """Atualiza. Devolve (atualizou, mensagem).

    Com `atualizar.sh` no repositório, usa ele (`--auto`): o mesmo caminho da
    atualização manual — dependências, testes e volta automática se falhar.
    """
    script = repo / "atualizar.sh"
    if script.exists() and os.name != "nt":
        try:
            proc = subprocess.run(["bash", str(script), "--auto"], cwd=repo,
                                  capture_output=True, text=True, timeout=1800)
        except (OSError, subprocess.TimeoutExpired) as erro:
            return False, f"atualizar.sh falhou: {erro}"
        saida = (proc.stdout + proc.stderr).strip().splitlines()
        ultima = saida[-1] if saida else ""
        if proc.returncode == 0:
            _log.info("Código atualizado: %s", ultima)
            return True, ultima
        if proc.returncode == 3:
            return False, "já está atualizado"
        return False, "atualização falhou: " + " | ".join(saida[-3:])[:200]
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
