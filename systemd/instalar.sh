#!/bin/bash
# Instala o vigia do CAMP Vision 2 como servico do usuario (systemd --user).
#
#   ./systemd/instalar.sh            instala e sobe
#   ./systemd/instalar.sh --remover  para e apaga
#
# Servico de usuario, sem sudo. Para rodar sem ninguem logado, habilite o
# lingering uma vez:  sudo loginctl enable-linger $USER

set -euo pipefail

NOME="campvision2"
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DESTINO="$HOME/.config/systemd/user/$NOME.service"
ESTADO="$HOME/.campvision2"

if [[ "${1:-}" == "--remover" ]]; then
  systemctl --user disable --now "$NOME.service" 2>/dev/null || true
  rm -f "$DESTINO"
  systemctl --user daemon-reload
  echo "Vigia removido."
  exit 0
fi

if [[ -x "$REPO/.venv/bin/python" ]]; then
  PYTHON="$REPO/.venv/bin/python"
else
  PYTHON="$(command -v python3)"
  echo "AVISO: usando $PYTHON (nenhum .venv em $REPO)."
fi

mkdir -p "$HOME/.config/systemd/user" "$ESTADO"

# A chave fica num arquivo so-do-dono, nao no unit file.
if [[ ! -f "$ESTADO/ambiente" ]]; then
  if [[ -z "${ANTHROPIC_API_KEY:-}" ]]; then
    echo "ERRO: exporte ANTHROPIC_API_KEY antes de instalar." >&2
    echo "      export ANTHROPIC_API_KEY='sk-ant-...'" >&2
    exit 1
  fi
  printf 'ANTHROPIC_API_KEY=%s\n' "$ANTHROPIC_API_KEY" > "$ESTADO/ambiente"
  # A auto-atualizacao precisa da chave SSH: servico nao tem ssh-agent.
  for CANDIDATA in "$HOME/.ssh/id_ed25519" "$HOME/.ssh/id_rsa"; do
    if [[ -f "$CANDIDATA" ]]; then
      printf 'GIT_SSH_COMMAND=ssh -i %s -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new\n' \
        "$CANDIDATA" >> "$ESTADO/ambiente"
      echo "Auto-atualizacao usara a chave $CANDIDATA"
      ssh-keygen -y -P "" -f "$CANDIDATA" >/dev/null 2>&1 || {
        echo "AVISO: essa chave tem senha; um servico nao tem quem a digite."
        echo "       Gere uma chave sem senha ou use remoto HTTPS com token."
      }
      break
    fi
  done
  chmod 600 "$ESTADO/ambiente"
  echo "Chave gravada em $ESTADO/ambiente (permissao 600)."
fi

sed -e "s|__PYTHON__|$PYTHON|g" -e "s|__REPO__|$REPO|g" -e "s|__CASA__|$HOME|g" \
  "$REPO/systemd/$NOME.service" > "$DESTINO"

systemctl --user daemon-reload
systemctl --user enable --now "$NOME.service"

echo
echo "Vigia instalado e no ar."
echo
echo "  estado:        systemctl --user status $NOME"
echo "  acompanhar:    journalctl --user -u $NOME -f"
echo "  parar:         systemctl --user stop $NOME"
echo "  desinstalar:   ./systemd/instalar.sh --remover"
echo
echo "Para rodar sem ninguem logado (maquina dedicada):"
echo "  sudo loginctl enable-linger $USER"
