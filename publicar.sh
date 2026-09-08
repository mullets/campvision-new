#!/bin/bash
# Publica o CAMP Vision 2 no GitHub.
#
#   ./publicar.sh SEU-USUARIO            usa SSH (git@github.com:...)
#   ./publicar.sh SEU-USUARIO --https    usa HTTPS
#
# Confere o essencial antes de subir: que nenhum segredo está sendo versionado,
# que os testes passam e que a árvore está limpa. Se o repositório ainda não
# existe no GitHub, cria com o `gh` quando ele estiver instalado; senão, diz o
# que fazer na mão.

set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
NOME="campvision2"
cd "$REPO"

USUARIO="${1:-}"
if [[ -z "$USUARIO" ]]; then
  echo "Uso: ./publicar.sh SEU-USUARIO [--https]" >&2
  exit 1
fi

if [[ "${2:-}" == "--https" ]]; then
  URL="https://github.com/$USUARIO/$NOME.git"
else
  URL="git@github.com:$USUARIO/$NOME.git"
fi

echo "== 1. Nenhum segredo versionado =="
VAZANDO=0
# Procura CHAVE DE VERDADE, não placeholder: exige 20+ caracteres de chave
# depois do prefixo. Assim 'sk-ant-...' na documentação não trava a publicação.
for PADRAO in 'sk-ant-[A-Za-z0-9_-]{20,}' 'CAMPVISION_SMTP_SENHA=[^"$ ]{6,}'; do
  if git grep -qIE "$PADRAO" -- . ':!publicar.sh' 2>/dev/null; then
    echo "  PERIGO: parece haver um segredo real em arquivo versionado:" >&2
    git grep -nIE "$PADRAO" -- . ':!publicar.sh' >&2 || true
    VAZANDO=1
  fi
done
if git ls-files --error-unmatch config.json >/dev/null 2>&1; then  # o real, não o exemplo
  echo "  PERIGO: config.json está versionado e pode conter a chave." >&2
  VAZANDO=1
fi
if [[ "$VAZANDO" == "1" ]]; then
  echo
  echo "Nada foi enviado. Remova o segredo, rode 'git rm --cached ARQUIVO' e tente de novo." >&2
  exit 1
fi
echo "  ok"

echo "== 2. Testes =="
if [[ -x "$REPO/.venv/bin/python" ]]; then
  PYTHON="$REPO/.venv/bin/python"
else
  PYTHON="$(command -v python3)"
fi
if ! "$PYTHON" -m unittest discover -s tests -t . >/tmp/campvision2-testes.txt 2>&1; then
  tail -20 /tmp/campvision2-testes.txt >&2
  echo "  Testes falhando. Não vou subir código quebrado." >&2
  exit 1
fi
tail -1 /tmp/campvision2-testes.txt
echo "  ok"

echo "== 3. Commit do que estiver pendente =="
if [[ -n "$(git status --porcelain)" ]]; then
  git add -A
  git commit -qm "${3:-Ajustes locais}"
  echo "  commitado"
else
  echo "  árvore já limpa"
fi

echo "== 4. Remoto =="
if git remote get-url origin >/dev/null 2>&1; then
  echo "  origin já existe: $(git remote get-url origin)"
elif command -v gh >/dev/null 2>&1; then
  echo "  criando o repositório no GitHub com o gh..."
  gh repo create "$USUARIO/$NOME" --private --source=. --remote=origin
else
  # Sem o gh não dá para criar o repositório daqui. Definir o remoto e tentar
  # o push resultaria num erro de SSH sem explicação, então paramos aqui com a
  # instrução exata.
  git remote add origin "$URL"
  echo "  origin definido como $URL"
  echo
  echo "Falta criar o repositório no GitHub. Duas opções:"
  echo
  echo "  a) instale o gh e rode este script de novo:"
  echo "       brew install gh && gh auth login"
  echo "       ./publicar.sh $USUARIO"
  echo
  echo "  b) crie na mão em https://github.com/new"
  echo "       nome: $NOME   |   vazio, SEM README nem .gitignore"
  echo "       depois rode: ./publicar.sh $USUARIO"
  exit 0
fi

echo "== 5. Enviando =="
if ! git push -u origin main; then
  echo
  echo "O push falhou. Causas comuns:" >&2
  echo "  - o repositório ainda não existe: crie em https://github.com/new" >&2
  echo "  - sem chave SSH configurada: use ./publicar.sh $USUARIO --https" >&2
  echo "  - repositório criado com README: rode 'git pull --rebase origin main'" >&2
  exit 1
fi

echo
echo "Pronto: $(git remote get-url origin)"
echo
echo "Na máquina dedicada:"
echo "  git clone $URL"
echo "  cd $NOME && python3 -m venv .venv && source .venv/bin/activate"
echo "  pip install -r requirements.txt"
echo "  export ANTHROPIC_API_KEY=\"sk-ant-...\""
echo "  python vigia.py --criar-config"
echo "  python vigia.py --pasta \"smb://servidor/share/pasta\""
echo "  ./launchagent/instalar.sh"
echo
echo "A auto-atualização do vigia já funciona a partir deste remoto."
