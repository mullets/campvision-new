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
if git ls-files --error-unmatch config.json >/dev/null 2>&1; then
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
else
  if command -v gh >/dev/null 2>&1; then
    echo "  criando o repositório no GitHub com o gh..."
    gh repo create "$USUARIO/$NOME" --private --source=. --remote=origin
  else
    git remote add origin "$URL"
    echo "  origin definido como $URL"
    echo "  ATENÇÃO: crie o repositório vazio em https://github.com/new (nome: $NOME)"
    echo "           antes de continuar, ou instale o gh: brew install gh"
  fi
fi

echo "== 5. Enviando =="
git push -u origin main
echo
echo "Pronto: $(git remote get-url origin)"
echo "A auto-atualização do vigia já funciona a partir deste remoto."
