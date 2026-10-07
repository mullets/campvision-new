#!/bin/bash
# CAMP Vision 2 — atualizar com um comando.
#
#   ./atualizar.sh            puxa a versão nova, instala o que faltar, testa e reinicia
#   ./atualizar.sh --agora    não espera o lote em andamento (o checkpoint retoma)
#   ./atualizar.sh --voltar   volta para a versão anterior
#   ./atualizar.sh --versao X vai para um commit/tag específico
#   ./atualizar.sh --sem-log  não abre o log ao vivo no fim
#   ./atualizar.sh --auto     usado pela auto-atualização do vigia: não reinicia
#                             o serviço (o próprio vigia se reinicia)
#
# Se os testes falharem na versão nova, volta sozinho para a anterior e o
# serviço continua rodando o código que funcionava.

set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
NOME="campvision2"
ESTADO="$HOME/.campvision2"
PY="$REPO/.venv/bin/python"
ANTERIOR="$ESTADO/versao_anterior"
FIXADA="$ESTADO/versao_fixada"   # depois de --voltar/--versao, a auto não mexe
cd "$REPO"
mkdir -p "$ESTADO"

MODO="normal"; ALVO=""; SEM_LOG=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --agora) MODO="agora" ;;
    --voltar) MODO="voltar" ;;
    --auto) MODO="auto" ;;
    --sem-log) SEM_LOG=1 ;;
    --versao) ALVO="${2:-}"; shift ;;
    *) echo "Opção desconhecida: $1" >&2; exit 2 ;;
  esac
  shift
done

VERDE=$'\e[32m'; VERMELHO=$'\e[31m'; AMARELO=$'\e[33m'; NORMAL=$'\e[0m'
ok()   { echo "  ${VERDE}✓${NORMAL} $*"; }
erro() { echo "  ${VERMELHO}✗${NORMAL} $*"; }
info() { echo "  · $*"; }

versao() { "$PY" -c 'from nucleo.config import VERSAO_BUILD; print(VERSAO_BUILD)' 2>/dev/null || echo "?"; }
reiniciar() {
  [[ "$MODO" == "auto" ]] && return
  if systemctl list-unit-files "$NOME.service" >/dev/null 2>&1; then
    sudo -n systemctl restart "$NOME" 2>/dev/null || sudo systemctl restart "$NOME"
    sleep 4
    systemctl is-active -q "$NOME" && ok "serviço reiniciado e no ar" \
      || { erro "serviço não subiu — veja: journalctl -u $NOME -n 50"; return 1; }
  fi
}

ATUAL="$(git rev-parse HEAD)"
echo "CAMP Vision 2 — versão atual $(versao) ($(git rev-parse --short HEAD))"

# ---------------------------------------------------------------- voltar
if [[ "$MODO" == "voltar" ]]; then
  [[ -f "$ANTERIOR" ]] || { erro "não há versão anterior registrada"; exit 1; }
  git reset -q --hard "$(cat "$ANTERIOR")"
  "$REPO/.venv/bin/pip" install -q -r requirements.txt
  git rev-parse HEAD > "$FIXADA"
  ok "voltou para $(git rev-parse --short HEAD) ($(versao))"
  info "auto-atualização pausada nesta versão até rodar ./atualizar.sh de novo"
  reiniciar
  exit 0
fi

if [[ "$MODO" == "auto" && -f "$FIXADA" ]]; then
  ok "versão fixada à mão em $(cut -c1-7 "$FIXADA") — auto-atualização pausada"
  exit 3
fi
rm -f "$FIXADA"

# ---------------------------------------------------------------- esperar o lote
if [[ "$MODO" == "normal" && -f "$ESTADO/estado.json" ]]; then
  for VEZ in $(seq 1 360); do
    SITUACAO="$("$PY" -c 'import json,sys;print(json.load(open(sys.argv[1])).get("situacao",""))' "$ESTADO/estado.json" 2>/dev/null || true)"
    [[ "$SITUACAO" != "processando" && "$SITUACAO" != "consolidando" ]] && break
    [[ "$VEZ" == 1 ]] && info "lote em andamento — esperando terminar (use --agora para não esperar)"
    sleep 10
  done
fi

# ---------------------------------------------------------------- baixar
if [[ -n "$(git status --porcelain --untracked-files=no)" ]]; then
  erro "há alterações locais no código; não vou sobrescrever. Veja: git status"
  exit 1
fi
git fetch -q --tags origin
if [[ -n "$ALVO" ]]; then
  NOVO="$(git rev-parse "$ALVO^{commit}")"
else
  NOVO="$(git rev-parse '@{u}')"
fi
if [[ "$NOVO" == "$ATUAL" ]]; then
  ok "já está na versão mais nova"
  [[ "$MODO" == "auto" ]] && exit 3
  if [[ -t 1 && "$SEM_LOG" != "1" ]]; then
    echo "Log ao vivo — Ctrl+C para sair (o serviço continua rodando):"
    exec journalctl -u "$NOME" -f -n 20
  fi
  exit 0
fi
echo "$ATUAL" > "$ANTERIOR"
if [[ -n "$ALVO" ]]; then git reset -q --hard "$NOVO"; git rev-parse HEAD > "$FIXADA"; else git merge -q --ff-only "$NOVO"; fi
ok "código $(git rev-parse --short "$ATUAL") → $(git rev-parse --short HEAD)"

desfazer() {
  erro "$1 — voltando para $(git rev-parse --short "$ATUAL")"
  git reset -q --hard "$ATUAL"
  "$REPO/.venv/bin/pip" install -q -r requirements.txt || true
  reiniciar || true
  exit 1
}

# ---------------------------------------------------------------- dependências
[[ -x "$PY" ]] || python3 -m venv "$REPO/.venv"
"$REPO/.venv/bin/pip" install -q -r requirements.txt || desfazer "pip falhou"
ok "dependências Python em dia"
FALTAM=()
for par in exiftool:libimage-exiftool-perl pdftoppm:poppler-utils mount.cifs:cifs-utils; do
  command -v "${par%%:*}" >/dev/null || FALTAM+=("${par##*:}")
done
if [[ ${#FALTAM[@]} -gt 0 ]]; then
  if [[ "$MODO" == "auto" ]]; then
    desfazer "faltam pacotes do sistema (${FALTAM[*]}) — rode ./atualizar.sh à mão"
  fi
  info "instalando pacotes do sistema: ${FALTAM[*]}"
  sudo apt-get install -y -qq "${FALTAM[@]}" || desfazer "apt falhou"
fi

# ---------------------------------------------------------------- backup e testes
mkdir -p "$ESTADO/backups"
tar -czf "$ESTADO/backups/estado-$(date +%Y%m%d-%H%M%S).tgz" -C "$ESTADO" \
  --exclude=backups --exclude=leitura . 2>/dev/null || true
ls -1t "$ESTADO/backups"/estado-*.tgz 2>/dev/null | tail -n +11 | xargs -r rm -f
ok "backup do config e do estado em $ESTADO/backups"

if "$PY" -m unittest discover -s tests -t . >/tmp/campvision2-testes.log 2>&1; then
  ok "testes passaram ($(grep -Eo 'Ran [0-9]+' /tmp/campvision2-testes.log))"
else
  tail -20 /tmp/campvision2-testes.log
  desfazer "testes falharam na versão nova"
fi

# ---------------------------------------------------------------- reiniciar
reiniciar || desfazer "serviço não subiu na versão nova"
echo "Versão nova: $(versao) ($(git rev-parse --short HEAD))"
# Depois de atualizar à mão, já abre o log ao vivo (Ctrl+C sai; o serviço segue).
if [[ "$MODO" != "auto" && -t 1 && "$SEM_LOG" != "1" ]]; then
  echo
  echo "Log ao vivo — Ctrl+C para sair (o serviço continua rodando):"
  exec journalctl -u "$NOME" -f -n 20
fi
journalctl -u "$NOME" -n 5 --no-pager 2>/dev/null | sed 's/^/  /' || true
