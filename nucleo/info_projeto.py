"""Leitura do `info_projeto.json` que já existe no acervo.

Esse arquivo veio de outro fluxo e o esquema dele não está sob controle deste
app, então a leitura é deliberadamente defensiva: reconhece vários nomes de
chave para o mesmo dado, ignora o que não entende e nunca quebra por causa de
um arquivo mal formado.

Ordem de prioridade dos dados, da mais forte para a mais fraca:

    carimbo lido  >  info_projeto.json  >  nome da pasta

O carimbo é a fonte primária (é o documento). O info é curadoria humana
anterior, então vale mais que o nome da pasta, que é só convenção.

Para descobrir o esquema real do seu acervo:

    python vigia.py --info
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

_log = logging.getLogger("cv2.info")

NOME_ARQUIVO = "info_projeto.json"

# Chave no arquivo (normalizada) -> campo do nosso esquema.
ALIASES: dict[str, str] = {}
for _campo, _nomes in {
    "projeto": ("projeto", "nome", "nomeprojeto", "titulo", "titulodoprojeto",
                "obra", "nomedaobra", "designacao"),
    "cliente": ("cliente", "proprietario", "contratante", "requerente"),
    "arquiteto": ("arquiteto", "autor", "autoria", "autordoprojeto", "projetista"),
    "escritorio": ("escritorio", "empresa", "escritorioresponsavel", "fundo",
                   "produtor", "colecao"),
    "endereco": ("endereco", "logradouro", "local", "localizacao"),
    "cidade": ("cidade", "municipio", "localidade"),
    "uf": ("uf", "estado", "sigla_uf"),
    "ano": ("ano", "data", "anoprojeto", "anodoprojeto", "datacriacao",
            "dataprojeto", "periodo"),
    "tipo": ("tipo", "tipologia", "categoria", "especie", "tipodocumental"),
    "observacoes": ("observacoes", "obs", "notas", "descricao", "resumo"),
}.items():
    for _nome in _nomes:
        ALIASES[_nome] = _campo


# Casamento por PALAVRA, em ordem de especificidade. Chave real vem cheia de
# underscore (`nome_do_projeto`, `data_projeto`), então comparar a string
# inteira não basta. Comparar por token também evita que "plano" case com
# "ano", que é o tipo de falso positivo que envenena um acervo inteiro.
PALAVRAS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("uf", ("uf", "estado")),
    ("cidade", ("cidade", "municipio", "localidade")),
    ("endereco", ("endereco", "logradouro", "localizacao")),
    ("arquiteto", ("arquiteto", "autor", "autoria", "projetista")),
    ("cliente", ("cliente", "proprietario", "contratante", "requerente")),
    ("escritorio", ("escritorio", "empresa", "fundo", "colecao", "produtor")),
    ("ano", ("ano", "data", "periodo")),
    ("tipo", ("tipo", "tipologia", "categoria", "especie")),
    ("observacoes", ("observacoes", "obs", "notas", "descricao", "resumo")),
    ("projeto", ("projeto", "titulo", "nome", "obra", "designacao")),
)


def _tokens(chave: str) -> list[str]:
    import re

    pedacos = re.split(r"[^A-Za-z0-9À-ÿ]+", str(chave))
    tokens: list[str] = []
    for pedaco in pedacos:
        # separa camelCase: nomeDoProjeto -> nome Do Projeto
        for parte in re.split(r"(?<=[a-zà-ÿ])(?=[A-ZÀ-Þ])", pedaco):
            normalizado = _normalizar_chave(parte)
            if normalizado:
                tokens.append(normalizado)
    return tokens


def mapear(chave: str) -> str | None:
    """Nome da chave do arquivo -> campo do nosso esquema, ou None."""
    exato = ALIASES.get(_normalizar_chave(chave))
    if exato:
        return exato
    tokens = set(_tokens(chave))
    for campo, palavras in PALAVRAS:
        if tokens & set(palavras):
            return campo
    return None


def _normalizar_chave(chave: str) -> str:
    import re
    import unicodedata

    sem_acento = "".join(
        c for c in unicodedata.normalize("NFD", str(chave))
        if unicodedata.category(c) != "Mn"
    )
    return re.sub(r"[^a-z0-9]", "", sem_acento.lower())


def _achatar(dados: dict, prefixo: str = "") -> dict[str, str]:
    """Achata um dicionário aninhado; lista vira texto separado por vírgula."""
    plano: dict[str, str] = {}
    for chave, valor in dados.items():
        if isinstance(valor, dict):
            plano.update(_achatar(valor, f"{prefixo}{chave}."))
        elif isinstance(valor, (list, tuple)):
            plano[f"{prefixo}{chave}"] = ", ".join(str(v) for v in valor if v)
        elif valor is not None:
            plano[f"{prefixo}{chave}"] = str(valor)
    return plano


def ler_bruto(pasta: Path) -> dict[str, str]:
    """Todo o conteúdo do arquivo, achatado, sem tradução de chave."""
    caminho = pasta / NOME_ARQUIVO
    if not caminho.exists():
        return {}
    try:
        dados = json.loads(caminho.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as erro:
        _log.warning("%s ilegível em %s: %s", NOME_ARQUIVO, pasta.name, erro)
        return {}
    if not isinstance(dados, dict):
        return {}
    return _achatar(dados)


def ler(pasta: Path) -> dict[str, str]:
    """Os campos do arquivo já traduzidos para os nomes do nosso esquema."""
    bruto = ler_bruto(pasta)
    campos: dict[str, str] = {}
    ignoradas: list[str] = []

    for chave, valor in bruto.items():
        valor = str(valor).strip()
        if not valor:
            continue
        # Em chave aninhada (projeto.autor), o último pedaço é o que importa.
        alvo = mapear(chave.split(".")[-1])
        if alvo is None:
            ignoradas.append(chave)
            continue
        campos.setdefault(alvo, valor)

    if "ano" in campos:  # "1979-04-03" ou "03/04/1979" viram 1979
        import re

        achado = re.search(r"(1[89]\d{2}|20\d{2})", campos["ano"])
        campos["ano"] = achado.group(1) if achado else ""
        if not campos["ano"]:
            campos.pop("ano")

    if ignoradas:
        _log.debug("%s em %s: chaves não reconhecidas: %s",
                   NOME_ARQUIVO, pasta.name, ", ".join(sorted(ignoradas)))
    return campos


def levantar_esquema(projetos) -> dict[str, int]:
    """Conta quais chaves aparecem nos info_projeto.json do acervo inteiro.

    É como se descobre o esquema real sem adivinhar: rode `--info` e o app diz
    o que existe nos seus arquivos, com quantas ocorrências e um exemplo.
    """
    contagem: dict[str, int] = {}
    for projeto in projetos:
        for chave in ler_bruto(projeto.pasta):
            contagem[chave] = contagem.get(chave, 0) + 1
    return dict(sorted(contagem.items(), key=lambda kv: (-kv[1], kv[0])))


def exemplo_de(projetos, chave: str) -> str:
    for projeto in projetos:
        valor = ler_bruto(projeto.pasta).get(chave, "")
        if valor:
            return valor[:60]
    return ""
