#!/bin/bash
# Tela do notebook e atalhos de terminal. Roda sozinho pelo instalar.sh, ou:
#   sudo ./scripts/tela_e_atalhos.sh
#
# Ao ligar o notebook:
#   tty1 (a tela que aparece)  → log ao vivo do CV2 (journalctl -f)
#   tty2 (Ctrl+Alt+F2)         → painel do vigia (fila, projeto, contadores)
# Atalhos no terminal (SSH ou tela): cvlog, cvtela, cvlotes, cvhist, cvatualizar
set -euo pipefail
[[ $EUID -eq 0 ]] || { echo "Rode com sudo." >&2; exit 1; }
USUARIO="${SUDO_USER:-camp}"
CASA="$(getent passwd "$USUARIO" | cut -d: -f6)"
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PY="$REPO/.venv/bin/python"

usermod -aG systemd-journal,adm "$USUARIO"   # ler o log sem sudo

for TTY in tty1 tty2; do
  mkdir -p "/etc/systemd/system/getty@$TTY.service.d"
  cat > "/etc/systemd/system/getty@$TTY.service.d/camp-autologin.conf" <<EOT
[Service]
ExecStart=
ExecStart=-/sbin/agetty --autologin $USUARIO --noclear %I \$TERM
EOT
done

PERFIL="$CASA/.bash_profile"
touch "$PERFIL"
# Tira o bloco antigo (só monitor no tty1) e põe o novo.
sed -i -E '/# CAMP Vision 2 — tela/,/^(fi|# fim tela CV2)$/d' "$PERFIL"
cat >> "$PERFIL" <<EOT
# CAMP Vision 2 — tela
case "\$(tty)" in
  /dev/tty1)
    setterm --blank 0 --powerdown 0 2>/dev/null
    trap '' INT TSTP QUIT
    while true; do clear; echo "CAMP Vision 2 — log ao vivo   (painel: Ctrl+Alt+F2)"; journalctl -u campvision2 -f -n 40 --no-hostname; sleep 2; done ;;
  /dev/tty2)
    setterm --blank 0 --powerdown 0 2>/dev/null
    trap '' INT TSTP QUIT
    while true; do "$PY" "$REPO/vigia.py" --monitor; sleep 2; done ;;
esac
# fim tela CV2
EOT
grep -q '\.bashrc' "$PERFIL" || echo '[ -f "$HOME/.bashrc" ] && . "$HOME/.bashrc"' >> "$PERFIL"

BASHRC="$CASA/.bashrc"
touch "$BASHRC"
sed -i '/# CAMP Vision 2 — atalhos/,/# fim atalhos CV2/d' "$BASHRC"
cat >> "$BASHRC" <<EOT
# CAMP Vision 2 — atalhos
alias cvlog='journalctl -u campvision2 -f -n 40'
alias cvtela='$PY $REPO/vigia.py --monitor'
alias cvlotes='$PY $REPO/vigia.py --lotes'
alias cvhist='$PY $REPO/vigia.py --historico'
alias cvatualizar='cd $REPO && ./atualizar.sh'
alias cvstatus='systemctl status campvision2 --no-pager | head -5'
# fim atalhos CV2
EOT
chown "$USUARIO:" "$PERFIL" "$BASHRC"
systemctl daemon-reload
systemctl restart getty@tty1.service getty@tty2.service
echo "Pronto. Tela: log no tty1, painel no tty2 (Ctrl+Alt+F2)."
echo "Atalhos (abra um terminal novo): cvlog  cvtela  cvlotes  cvhist TERMO  cvatualizar  cvstatus"
