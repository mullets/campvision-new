#!/bin/bash
# CAMP Vision 2 — instalação completa no Ubuntu Server (máquina .40).
#
#   cd ~/campvision-new && sudo ./instalar.sh
#
# Faz tudo, e pode ser rodado de novo sem estragar nada (só pergunta o que falta):
#   1. pacotes do sistema (python, exiftool, poppler, cifs)
#   2. notebook não suspende com a tampa fechada
#   3. QNAP montado para sempre em /mnt/camp/backup
#   4. ambiente Python (.venv) e dependências
#   5. chaves (Anthropic, painel, GitHub) em arquivos 600
#   6. config: entrada, acervo, painel
#   7. serviço do sistema: sobe no boot, volta se cair
#   8. tela do notebook mostrando o andamento ao vivo
#   9. conferência final
#
# Variáveis para rodar sem perguntas: QNAP_IP, QNAP_COMPARTILHAMENTO,
# QNAP_USUARIO, QNAP_SENHA, ANTHROPIC_API_KEY, CAMP_PAINEL_URL,
# CAMP_PAINEL_TOKEN, GITHUB_TOKEN.

set -euo pipefail

VERDE=$'\e[32m'; AMARELO=$'\e[33m'; VERMELHO=$'\e[31m'; NEGRITO=$'\e[1m'; NORMAL=$'\e[0m'
passo() { echo; echo "${NEGRITO}== $* ==${NORMAL}"; }
ok()    { echo "  ${VERDE}✓${NORMAL} $*"; }
aviso() { echo "  ${AMARELO}!${NORMAL} $*"; }
erro()  { echo "  ${VERMELHO}✗${NORMAL} $*"; }

if [[ $EUID -ne 0 ]]; then
  echo "Rode com sudo:  sudo ./instalar.sh" >&2
  exit 1
fi

USUARIO="${SUDO_USER:-campvision}"
if [[ "$USUARIO" == "root" ]]; then
  echo "Rode a partir do usuário do serviço (ex.: campvision) com sudo, não como root direto." >&2
  exit 1
fi
CASA="$(getent passwd "$USUARIO" | cut -d: -f6)"
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ESTADO="$CASA/.campvision2"
AMBIENTE="$ESTADO/ambiente"
NOME="campvision2"
MONTAGEM="/mnt/camp/backup"
CRED="/etc/camp-qnap.cred"
como_usuario() { sudo -u "$USUARIO" -H "$@"; }

perguntar() {  # perguntar VAR "texto" [padrão] [secreto]
  local var="$1" texto="$2" padrao="${3:-}" secreto="${4:-}"
  local atual="${!var:-}"
  [[ -n "$atual" ]] && return
  local resposta=""
  if [[ -n "$secreto" ]]; then
    read -r -s -p "  $texto: " resposta; echo
  else
    read -r -p "  $texto${padrao:+ [$padrao]}: " resposta
  fi
  printf -v "$var" '%s' "${resposta:-$padrao}"
}

ler_ambiente() {  # valor de uma chave no arquivo de ambiente
  [[ -f "$AMBIENTE" ]] && grep -E "^$1=" "$AMBIENTE" | tail -1 | cut -d= -f2- || true
}

grava_ambiente() {  # grava_ambiente CHAVE valor
  touch "$AMBIENTE"
  grep -v -E "^$1=" "$AMBIENTE" > "$AMBIENTE.tmp" || true
  [[ -n "$2" ]] && printf '%s=%s\n' "$1" "$2" >> "$AMBIENTE.tmp"
  mv "$AMBIENTE.tmp" "$AMBIENTE"
  chown "$USUARIO:" "$AMBIENTE"; chmod 600 "$AMBIENTE"
}

echo "${NEGRITO}CAMP Vision 2 — instalação${NORMAL}  (usuário do serviço: $USUARIO, repo: $REPO)"

# ------------------------------------------------------------------ 1
passo "1/9 Pacotes do sistema"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq python3 python3-venv python3-pip git libimage-exiftool-perl \
  poppler-utils cifs-utils smbclient iputils-ping >/dev/null
ok "python3 $(python3 -c 'import sys;print(".".join(map(str,sys.version_info[:2])))'), exiftool $(exiftool -ver), pdftoppm, cifs"
python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' \
  || { erro "Python precisa ser 3.10 ou mais novo"; exit 1; }

# ------------------------------------------------------------------ 2
passo "2/9 Tampa do notebook e suspensão"
mkdir -p /etc/systemd/logind.conf.d
cat > /etc/systemd/logind.conf.d/camp-tampa.conf <<'EOF'
[Login]
HandleLidSwitch=ignore
HandleLidSwitchExternalPower=ignore
HandleLidSwitchDocked=ignore
IdleAction=ignore
EOF
systemctl mask -q sleep.target suspend.target hibernate.target hybrid-sleep.target
systemctl kill -s HUP systemd-logind 2>/dev/null || true
ok "fechar a tampa não suspende; suspensão desligada"

# ------------------------------------------------------------------ 3
passo "3/9 QNAP"
perguntar QNAP_IP "IP do QNAP" "192.168.15.30"
perguntar QNAP_COMPARTILHAMENTO "Pasta compartilhada do QNAP" "Backup Servidor CAMP"
if [[ ! -f "$CRED" ]]; then
  perguntar QNAP_USUARIO "Usuário do QNAP"
  perguntar QNAP_SENHA "Senha do QNAP" "" secreto
  printf 'username=%s\npassword=%s\n' "$QNAP_USUARIO" "$QNAP_SENHA" > "$CRED"
  chmod 600 "$CRED"
fi
ok "credenciais em $CRED (só root lê)"
UID_S="$(id -u "$USUARIO")"; GID_S="$(id -g "$USUARIO")"
ORIGEM="//$QNAP_IP/${QNAP_COMPARTILHAMENTO// /\\040}"
OPCOES="credentials=$CRED,uid=$UID_S,gid=$GID_S,file_mode=0664,dir_mode=0775,iocharset=utf8,vers=3.0,nofail,_netdev,x-systemd.automount,x-systemd.mount-timeout=30"
mkdir -p "$MONTAGEM"
sed -i "\#[[:space:]]$MONTAGEM[[:space:]]#d" /etc/fstab
echo "$ORIGEM $MONTAGEM cifs $OPCOES 0 0" >> /etc/fstab
systemctl daemon-reload
systemctl restart remote-fs.target 2>/dev/null || true
mount "$MONTAGEM" 2>/dev/null || true
ENTRADA="$MONTAGEM/Arquivos/100 - Scanners"
ACERVO="$MONTAGEM/Fundos e Escritorios/ACERVOS_CAMP"
if ls "$MONTAGEM" >/dev/null 2>&1 && [[ -d "$ENTRADA" && -d "$ACERVO" ]]; then
  ok "montado em $MONTAGEM"
  ok "entrada: $ENTRADA"
  ok "acervo:  $ACERVO"
  como_usuario touch "$ACERVO/.camp-teste-escrita" && rm -f "$ACERVO/.camp-teste-escrita" \
    && ok "escrita no acervo OK" || erro "sem permissão de escrita em ACERVOS_CAMP (usuário do QNAP)"
else
  erro "QNAP não montou ou as pastas não existem. Diagnóstico:"
  ping -c 1 -W 2 "$QNAP_IP" >/dev/null && ok "ping $QNAP_IP" || erro "sem ping em $QNAP_IP"
  timeout 3 bash -c "</dev/tcp/$QNAP_IP/445" 2>/dev/null && ok "porta 445 (SMB) aberta" || erro "porta 445 fechada"
  echo "  Compartilhamentos que o QNAP oferece:"
  smbclient -L "//$QNAP_IP" -A <(printf 'username=%s\npassword=%s\n' \
    "$(sed -n 's/^username=//p' "$CRED")" "$(sed -n 's/^password=//p' "$CRED")") 2>&1 | sed 's/^/    /' | head -20
  echo "  Corrija e rode de novo. Para trocar a senha: sudo rm $CRED"
  exit 1
fi

# ------------------------------------------------------------------ 4
passo "4/9 Ambiente Python"
como_usuario python3 -m venv "$REPO/.venv"
como_usuario "$REPO/.venv/bin/pip" install -q --upgrade pip
como_usuario "$REPO/.venv/bin/pip" install -q -r "$REPO/requirements.txt"
ok "dependências em $REPO/.venv"

# ------------------------------------------------------------------ 5
passo "5/9 Chaves"
como_usuario mkdir -p "$ESTADO"
ANTHROPIC_API_KEY="${ANTHROPIC_API_KEY:-$(ler_ambiente ANTHROPIC_API_KEY)}"
perguntar ANTHROPIC_API_KEY "Chave da Anthropic (sk-ant-...)" "" secreto
grava_ambiente ANTHROPIC_API_KEY "$ANTHROPIC_API_KEY"
CAMP_PAINEL_TOKEN="${CAMP_PAINEL_TOKEN:-$(ler_ambiente CAMP_PAINEL_TOKEN)}"
if [[ -z "$CAMP_PAINEL_TOKEN" ]]; then
  read -r -s -p "  Token do painel (X-Camp-Token; Enter se não houver): " CAMP_PAINEL_TOKEN; echo
fi
grava_ambiente CAMP_PAINEL_TOKEN "$CAMP_PAINEL_TOKEN"
ok "chaves em $AMBIENTE (600)"
if [[ ! -f "$CASA/.git-credentials" ]]; then
  if [[ -z "${GITHUB_TOKEN:-}" ]]; then
    read -r -s -p "  Token do GitHub para a auto-atualização (Enter para pular): " GITHUB_TOKEN; echo
  fi
  if [[ -n "${GITHUB_TOKEN:-}" ]]; then
    printf 'https://mullets:%s@github.com\n' "$GITHUB_TOKEN" > "$CASA/.git-credentials"
    chown "$USUARIO:" "$CASA/.git-credentials"; chmod 600 "$CASA/.git-credentials"
    como_usuario git config --global credential.helper store
    ok "auto-atualização com token do GitHub"
  else
    aviso "sem token do GitHub: a auto-atualização só funciona se o repositório for público"
  fi
fi
como_usuario git config --global --add safe.directory "$REPO" 2>/dev/null || true

# ------------------------------------------------------------------ 6
passo "6/9 Configuração"
perguntar CAMP_PAINEL_URL "Endereço do painel" "http://192.168.15.60:8000"
PY="$REPO/.venv/bin/python"
como_usuario "$PY" "$REPO/vigia.py" --criar-config >/dev/null
como_usuario "$PY" "$REPO/vigia.py" --entrada "$ENTRADA" >/dev/null
como_usuario "$PY" "$REPO/vigia.py" --acervo "$ACERVO" >/dev/null
como_usuario "$PY" "$REPO/vigia.py" --painel "$CAMP_PAINEL_URL" >/dev/null
como_usuario "$PY" - "$ESTADO/config.json" "$ACERVO" <<'EOF'
import json, sys
caminho, acervo = sys.argv[1], sys.argv[2]
cfg = json.load(open(caminho, encoding="utf-8"))
cfg["pasta_vigiada"] = cfg.get("pasta_vigiada") or acervo
cfg["identidade_site"] = cfg.get("identidade_site") or "https://camp.arq.br"
cfg["formatos_saida"] = ["csv"]
json.dump(cfg, open(caminho, "w", encoding="utf-8"), indent=2, ensure_ascii=False)
EOF
ok "config em $ESTADO/config.json"

# ------------------------------------------------------------------ 7
passo "7/9 Serviço"
# Versão antiga instalava como serviço de usuário: tira para não rodar dois.
como_usuario XDG_RUNTIME_DIR="/run/user/$UID_S" systemctl --user disable --now "$NOME.service" 2>/dev/null || true
rm -f "$CASA/.config/systemd/user/$NOME.service"
sed -e "s|__REPO__|$REPO|g" -e "s|__CASA__|$CASA|g" -e "s|__USUARIO__|$USUARIO|g" \
  "$REPO/systemd/$NOME-sistema.service" > "/etc/systemd/system/$NOME.service"
cat > "/etc/sudoers.d/$NOME" <<EOF
$USUARIO ALL=(root) NOPASSWD: /usr/bin/systemctl restart $NOME, /usr/bin/systemctl stop $NOME, /usr/bin/systemctl start $NOME, /bin/systemctl restart $NOME, /bin/systemctl stop $NOME, /bin/systemctl start $NOME
EOF
chmod 440 "/etc/sudoers.d/$NOME"
visudo -cq || { erro "sudoers inválido"; rm -f "/etc/sudoers.d/$NOME"; exit 1; }
systemctl daemon-reload
systemctl enable -q "$NOME.service"
systemctl restart "$NOME.service"
ok "serviço $NOME ligado (sobe no boot, reinicia se cair)"

# ------------------------------------------------------------------ 8
passo "8/9 Tela do notebook e atalhos"
SUDO_USER="$USUARIO" bash "$REPO/scripts/tela_e_atalhos.sh" | sed 's/^/  /'
ok "tty1: log ao vivo · tty2 (Ctrl+Alt+F2): painel · atalhos cvlog/cvtela/cvlotes/cvatualizar"

# ------------------------------------------------------------------ 9
passo "9/9 Conferência"
sleep 5
systemctl is-active -q "$NOME" && ok "serviço no ar" || erro "serviço não subiu: journalctl -u $NOME -n 50"
[[ -f "$ESTADO/estado.json" ]] && ok "vigia gravando estado" || aviso "estado.json ainda não apareceu (normal nos primeiros segundos)"
PAINEL_IP="$(echo "$CAMP_PAINEL_URL" | sed -E 's#https?://([^:/]+).*#\1#')"
if curl -fsS -m 5 -o /dev/null "$CAMP_PAINEL_URL/api/estacoes/contexto" -H "X-Camp-Token: $CAMP_PAINEL_TOKEN" 2>/dev/null; then
  ok "painel responde em $CAMP_PAINEL_URL"
else
  aviso "painel não respondeu em $CAMP_PAINEL_URL (projeto novo espera o painel para ganhar número)"
fi
IP_ATUAL="$(hostname -I | awk '{print $1}')"
[[ "$IP_ATUAL" == "192.168.15.40" ]] && ok "IP fixo 192.168.15.40" || aviso "IP atual é $IP_ATUAL — o combinado é 192.168.15.40 (passo 0)"

echo
echo "${NEGRITO}Pronto.${NORMAL}"
echo "  atalhos (terminal novo): cvlog  cvtela  cvlotes  cvhist TERMO  cvatualizar  cvstatus"
echo "  lotes:           $PY $REPO/vigia.py --lotes"
echo "  histórico:       $PY $REPO/vigia.py --historico NOME-OU-CODIGO"
echo "  log:             journalctl -u $NOME -f"
echo "  atualizar:       cd $REPO && ./atualizar.sh"
