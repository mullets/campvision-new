#!/bin/bash
# Instala o vigia do CAMP Vision 2 como serviço do macOS (LaunchAgent).
#
#   ./launchagent/instalar.sh            instala e sobe
#   ./launchagent/instalar.sh --remover  descarrega e apaga
#
# Roda no seu login, sem sudo. Sobe sozinho ao ligar o Mac e volta se cair.

set -euo pipefail

ROTULO="com.camp.campvision2.vigia"
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DESTINO="$HOME/Library/LaunchAgents/$ROTULO.plist"
MODELO="$REPO/launchagent/$ROTULO.plist"

if [[ "${1:-}" == "--remover" ]]; then
  launchctl bootout "gui/$(id -u)/$ROTULO" 2>/dev/null || true
  rm -f "$DESTINO"
  echo "Vigia removido."
  exit 0
fi

# Python do ambiente virtual, se existir; senão o do sistema.
if [[ -x "$REPO/.venv/bin/python" ]]; then
  PYTHON="$REPO/.venv/bin/python"
else
  PYTHON="$(command -v python3)"
  echo "AVISO: usando $PYTHON (nenhum .venv encontrado em $REPO)."
fi

if [[ -z "${ANTHROPIC_API_KEY:-}" ]]; then
  echo "ERRO: exporte ANTHROPIC_API_KEY antes de instalar." >&2
  echo "      export ANTHROPIC_API_KEY='sk-ant-...'" >&2
  exit 1
fi

if ! "$PYTHON" "$REPO/vigia.py" --status >/dev/null 2>&1; then
  echo "AVISO: 'vigia.py --status' não rodou limpo."
  echo "       Defina a pasta primeiro: python vigia.py --pasta /Volumes/acervos"
fi

mkdir -p "$HOME/Library/LaunchAgents" "$HOME/.campvision2"

sed -e "s|__PYTHON__|$PYTHON|g" \
    -e "s|__REPO__|$REPO|g" \
    -e "s|__CASA__|$HOME|g" \
    -e "s|__CHAVE__|$ANTHROPIC_API_KEY|g" \
    "$MODELO" > "$DESTINO"
chmod 600 "$DESTINO"   # o arquivo contém a chave da API

launchctl bootout "gui/$(id -u)/$ROTULO" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$DESTINO"
launchctl enable "gui/$(id -u)/$ROTULO"

echo "Vigia instalado e no ar."
echo
echo "  ver se está rodando:  launchctl list | grep campvision2"
echo "  acompanhar:           tail -f ~/.campvision2/vigia.log"
echo "  parar por ora:        launchctl bootout gui/$(id -u)/$ROTULO"
echo "  desinstalar:          ./launchagent/instalar.sh --remover"
echo
echo "Para acompanhar com o painel ao vivo, pare o serviço e rode 'python vigia.py'."
