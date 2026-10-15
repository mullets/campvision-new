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
#   ./atualizar.sh --boot     chamado pelo systemd (ExecStartPre) a cada subida do serviço:
#                             se a versão nova não confirmou "subi bem" em CAMPVISION_BOOT_MAX
#                             subidas (padrão 3), volta sozinho para a anterior
#
# O serviço só é reiniciado com a situação "vigiando" (ocioso). Em qualquer outra
# (copiando, preparando, gravando metadados, processando...) a atualização espera.
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
    --boot) MODO="boot" ;;
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
PENDENTE="$ESTADO/subida_pendente"   # novo=SHA / anterior=SHA / tentativas=N (some quando o vigia confirma)

# ---------------------------------------------------------------- boot (rollback automático)
if [[ "$MODO" == "boot" ]]; then
  [[ -f "$PENDENTE" ]] || exit 0
  NOVO_P="$(sed -n 's/^novo=//p' "$PENDENTE")"; ANT_P="$(sed -n 's/^anterior=//p' "$PENDENTE")"
  TENT="$(sed -n 's/^tentativas=//p' "$PENDENTE")"; TENT="${TENT:-0}"
  if [[ "$NOVO_P" != "$ATUAL" ]]; then rm -f "$PENDENTE"; exit 0; fi   # mudou à mão: nada a vigiar
  MAXIMO="${CAMPVISION_BOOT_MAX:-3}"
  if (( TENT >= MAXIMO )); then
    echo "CAMP Vision 2: a versão ${ATUAL:0:7} subiu $TENT vez(es) sem confirmar 'subi bem' — voltando para ${ANT_P:0:7}" >&2
    git reset -q --hard "$ANT_P" && "$REPO/.venv/bin/pip" install -q -r requirements.txt || true
    git rev-parse HEAD > "$FIXADA"
    rm -f "$PENDENTE"
    echo "$(date -Is) rollback automático de ${ATUAL:0:7} para ${ANT_P:0:7} (sem 'subi bem' em $TENT subidas)" >> "$ESTADO/rollbacks.log"
    exit 0
  fi
  printf 'novo=%s\nanterior=%s\ntentativas=%s\n' "$NOVO_P" "$ANT_P" "$((TENT + 1))" > "$PENDENTE"
  exit 0
fi

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
# Só reinicia com o vigia "vigiando" (ocioso). Situação vazia = serviço parado ou estado velho
# (mais de 3 min sem gravar): nada a esperar.
situacao_do_vigia() {
  "$PY" - "$ESTADO/estado.json" <<'PYEOF' 2>/dev/null || true
import json, sys, time, os
try:
    caminho = sys.argv[1]
    if time.time() - os.path.getmtime(caminho) > 180:
        raise SystemExit
    print(json.load(open(caminho)).get("situacao", ""))
except Exception:
    pass
PYEOF
}
if [[ "$MODO" == "normal" && -f "$ESTADO/estado.json" ]]; then
  for VEZ in $(seq 1 360); do
    SITUACAO="$(situacao_do_vigia)"
    [[ -z "$SITUACAO" || "$SITUACAO" == "vigiando" ]] && break
    [[ "$VEZ" == 1 ]] && info "vigia está '$SITUACAO' — esperando ficar 'vigiando' (use --agora para não esperar)"
    sleep 10
  done
  SITUACAO="$(situacao_do_vigia)"
  if [[ -n "$SITUACAO" && "$SITUACAO" != "vigiando" ]]; then
    erro "o vigia continua '$SITUACAO' depois de 1 h — não vou reiniciar no meio de um lote (use --agora)"
    exit 1
  fi
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
rm -f "$ESTADO/subiu_bem"
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
# RAW (DNG/NEF) revelado inteiro para o JPG do acervo; sem ele, sai da prévia embutida (pior).
"$REPO/.venv/bin/pip" install -q "rawpy>=0.19" 2>/dev/null || info "rawpy não instalou: JPG de DNG sai da prévia embutida"
FALTAM=()
for par in exiftool:libimage-exiftool-perl pdftoppm:poppler-utils mount.cifs:cifs-utils tesseract:tesseract-ocr jpegtran:libjpeg-turbo-progs; do
  command -v "${par%%:*}" >/dev/null || FALTAM+=("${par##*:}")
done
# O português do tesseract é pacote à parte: sem ele a orientação fica sempre incerta.
if ! { command -v tesseract >/dev/null && tesseract --list-langs 2>/dev/null | grep -qx por; }; then
  FALTAM+=(tesseract-ocr-por)
fi
if [[ ${#FALTAM[@]} -gt 0 ]]; then
  if [[ "$MODO" == "auto" ]] && ! sudo -n true 2>/dev/null; then
    desfazer "faltam pacotes do sistema (${FALTAM[*]}) — rode ./atualizar.sh à mão"
  fi
  info "instalando pacotes do sistema: ${FALTAM[*]}"
  sudo apt-get install -y -qq "${FALTAM[@]}" || desfazer "apt falhou"
fi

# ---------------------------------------------------------------- backup e testes
mkdir -p "$ESTADO/backups"
chmod 700 "$ESTADO/backups"
# Sem o arquivo `ambiente` (chaves da Anthropic/painel/GitHub: nunca vão para backup) e com 600.
BACKUP="$ESTADO/backups/estado-$(date +%Y%m%d-%H%M%S).tgz"
( umask 077; tar -czf "$BACKUP" -C "$ESTADO" \
  --exclude=backups --exclude=leitura --exclude=ambiente --exclude=vigia.lock . 2>/dev/null ) || true
[[ -f "$BACKUP" ]] && chmod 600 "$BACKUP"
ls -1t "$ESTADO/backups"/estado-*.tgz 2>/dev/null | tail -n +11 | xargs -r rm -f
ok "backup do config e do estado em $ESTADO/backups"

if "$PY" -m unittest discover -s tests -t . >/tmp/campvision2-testes.log 2>&1; then
  ok "testes passaram ($(grep -Eo 'Ran [0-9]+' /tmp/campvision2-testes.log))"
else
  tail -20 /tmp/campvision2-testes.log
  desfazer "testes falharam na versão nova"
fi

# A versão nova só vale depois que o vigia completar a 1ª rodada e confirmar (subiu_bem);
# a cada subida do serviço o --boot conta, e sem confirmação ele volta para a anterior.
printf 'novo=%s\nanterior=%s\ntentativas=0\n' "$(git rev-parse HEAD)" "$ATUAL" > "$PENDENTE"

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
