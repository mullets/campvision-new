"""UX-60: resposta mal formada do modelo de visão é REPARADA antes de ser descartada.

Caso real (F001-P0033-1979-S01-D00001, 11/2026): ~15 campos da folha vieram como
"valor descartado: resposta do modelo quebrada". O saneamento (UX-37, `saneamento.py`)
joga fora o que tem resto de marcação da ferramenta; aqui, ANTES dele, recupera o que dá
para recuperar sem adivinhar:

- campo que devia ser objeto ({valor, confianca, …}) e veio como TEXTO com JSON dentro:
  cercas ```json, texto antes/depois, vírgula sobrando, aspas tipográficas e JSON cortado
  no fim (fecha o que ficou aberto e descarta o último item incompleto);
- a resposta inteira (ou uma chave estranha) como texto com o JSON da folha dentro;
- vários parâmetros da ferramenta "colados" dentro de um valor
  (`X</parameter><parameter name="cidade">{…}`): cada um volta para o seu campo, mas
  só preenche campo que veio vazio (nunca troca o que o modelo pôs no lugar certo).

O que é ambíguo (texto dos DOIS lados da marcação) continua com o saneamento, que
descarta. Depois disso o leitor (visao.py) faz UMA nova tentativa; só então descarta.
"""

from __future__ import annotations

import json
import re
from typing import Any

# Etiqueta de parâmetro da ferramenta (com ou sem prefixo), aberta.
_ABRE = re.compile(r"<\s*(?:antml:)?parameter\s+name\s*=\s*[\"']?([\w-]+)[\"']?\s*>", re.I)
_FECHA = re.compile(r"<\s*/\s*(?:antml:)?parameter\s*>", re.I)
_QUALQUER_MARCA = re.compile(r"<\s*/?\s*(?:antml:)?(?:parameter|invoke|function\w*|tool\w*)\b[^<>]*>?|antml:", re.I)
_CERCA = re.compile(r"```[ \t]*(?:json|JSON)?[ \t]*\n?(.*?)(?:```|\Z)", re.S)
_VIRGULA_SOBRANDO = re.compile(r",(\s*[}\]])")
_TIPOGRAFICAS = str.maketrans({"“": '"', "”": '"', "„": '"', "‟": '"', "″": '"'})
SUBCAMPOS = ("valor", "confianca", "alternativas", "onde")


def _tentar(texto: str) -> Any:
    try:
        return json.loads(texto)
    except (ValueError, TypeError):
        return None


def _fechar(texto: str) -> str | None:
    """JSON cortado no fim: fecha aspas e chaves/colchetes abertos. None se não há o que fechar."""
    pilha: list[str] = []
    em_texto = escape = False
    for c in texto:
        if em_texto:
            if escape:
                escape = False
            elif c == "\\":
                escape = True
            elif c == '"':
                em_texto = False
        elif c == '"':
            em_texto = True
        elif c in "{[":
            pilha.append("}" if c == "{" else "]")
        elif c in "}]":
            if not pilha:
                return None
            pilha.pop()
    if not pilha and not em_texto:
        return None
    s = texto + ('"' if em_texto else "")
    s = s.rstrip()
    if s.endswith(":"):
        s += " null"
    s = s.rstrip(",")
    return s + "".join(reversed(pilha))


def _ultima_virgula_fora_de_texto(texto: str) -> int:
    em_texto = escape = False
    ultima = -1
    for i, c in enumerate(texto):
        if em_texto:
            if escape:
                escape = False
            elif c == "\\":
                escape = True
            elif c == '"':
                em_texto = False
        elif c == '"':
            em_texto = True
        elif c == ",":
            ultima = i
    return ultima


def json_reparado(texto: Any) -> Any:
    """O valor JSON dentro do texto, consertando o que é seguro consertar. None se não deu.

    Só devolve objeto ou lista (texto solto não é JSON da ferramenta)."""
    if not isinstance(texto, str) or not texto.strip():
        return None
    s = texto.strip()
    candidatos: list[str] = [s]
    cerca = _CERCA.search(s)
    if cerca:
        s = cerca.group(1).strip()
        candidatos.append(s)
    inicio = min((i for i in (s.find("{"), s.find("[")) if i >= 0), default=-1)
    if inicio < 0:
        return None
    s = s[inicio:]
    fim = max(s.rfind("}"), s.rfind("]"))
    candidatos += [s[: fim + 1]] if fim >= 0 else []
    candidatos.append(s)
    vistos: set[str] = set()
    for base in candidatos:
        for c in (base, _VIRGULA_SOBRANDO.sub(r"\1", base)):
            for d in (c, c.translate(_TIPOGRAFICAS)):
                if d in vistos:
                    continue
                vistos.add(d)
                valor = _tentar(d)
                if isinstance(valor, (dict, list)):
                    return valor
    # Cortado no fim: fecha o que ficou aberto; se o último item está pela metade, tira ele.
    s = _VIRGULA_SOBRANDO.sub(r"\1", s.translate(_TIPOGRAFICAS))
    for _ in range(50):
        fechado = _fechar(s)
        if fechado is None:
            break
        valor = _tentar(fechado)
        if isinstance(valor, (dict, list)):
            return valor
        corte = _ultima_virgula_fora_de_texto(s)
        if corte <= 0:
            break
        s = s[:corte]
    return None


def _parametros(texto: str) -> tuple[str, list[tuple[str, str]]]:
    """(texto antes da 1ª marca, [(nome, conteúdo)] dos parâmetros colados no texto)."""
    primeira = _QUALQUER_MARCA.search(texto)
    if not primeira:
        return texto, []
    antes = texto[: primeira.start()]
    pares: list[tuple[str, str]] = []
    aberturas = list(_ABRE.finditer(texto))
    for i, m in enumerate(aberturas):
        fim = aberturas[i + 1].start() if i + 1 < len(aberturas) else len(texto)
        conteudo = texto[m.end(): fim]
        fecha = _FECHA.search(conteudo)
        pares.append((m.group(1).lower(), (conteudo[: fecha.start()] if fecha else conteudo).strip()))
    return antes, pares


def _como_campo(conteudo: Any) -> dict | None:
    """Conteúdo de um campo do modelo ({valor, confianca, …}) a partir de texto ou objeto."""
    if isinstance(conteudo, dict):
        return conteudo if any(k in conteudo for k in SUBCAMPOS) else None
    if isinstance(conteudo, str):
        obj = json_reparado(conteudo)
        if isinstance(obj, dict) and any(k in obj for k in SUBCAMPOS):
            return obj
    return None


def _vazio(valor: Any) -> bool:
    if isinstance(valor, dict):
        return _vazio(valor.get("valor"))
    return valor is None or (isinstance(valor, str) and valor.strip().lower() in ("", "null", "none"))


def reparar_resposta(entrada: Any, campos: tuple[str, ...],
                     esperadas: tuple[str, ...] = ()) -> tuple[dict[str, Any], list[str]]:
    """(entrada consertada, [o que foi consertado]). `campos` = os campos-objeto da ferramenta;
    `esperadas` = as outras chaves dela (para reconhecer a resposta inteira escondida num texto).

    Nunca inventa valor: só move/decodifica o que o próprio modelo escreveu."""
    reparos: list[str] = []
    if isinstance(entrada, str):
        obj = json_reparado(entrada)
        entrada = obj if isinstance(obj, dict) else {}
        reparos.append("resposta inteira em texto")
    if not isinstance(entrada, dict):
        return {}, reparos
    novo = dict(entrada)
    conjunto = set(campos)
    conhecidas = conjunto | set(esperadas)

    def colocar(nome: str, conteudo: Any, origem: str) -> None:
        """Parâmetro achado fora do lugar: só ocupa campo vazio."""
        if nome in conjunto:
            obj = _como_campo(conteudo) or ({"valor": conteudo, "confianca": 0.0}
                                            if isinstance(conteudo, str) and conteudo.strip() else None)
            if obj is not None and _vazio(novo.get(nome)):
                novo[nome] = obj
                reparos.append(f"{nome} ({origem})")
        elif nome not in novo or _vazio(novo.get(nome)):
            obj = json_reparado(conteudo) if isinstance(conteudo, str) else None
            if obj is not None or (isinstance(conteudo, str) and conteudo.strip()):
                novo[nome] = obj if obj is not None else conteudo
                reparos.append(f"{nome} ({origem})")

    # 1. A resposta inteira escondida numa chave (texto com o JSON da folha).
    for chave, valor in list(entrada.items()):
        if isinstance(valor, str) and chave not in conjunto:
            obj = json_reparado(valor)
            if isinstance(obj, dict) and conhecidas & set(obj) - {chave}:
                for nome, conteudo in obj.items():
                    colocar(nome, conteudo, f"dentro de '{chave}'")
    # 2. Cada campo-objeto: texto com JSON, parâmetros colados no valor.
    for nome in campos:
        valor = novo.get(nome)
        if isinstance(valor, str):
            obj = _como_campo(valor)
            if obj is not None:
                novo[nome] = valor = obj
                reparos.append(f"{nome} (JSON em texto)")
            else:
                valor = {"valor": valor}               # é o que a leitura faria; só grava se consertar abaixo
        if not isinstance(valor, dict):
            continue
        texto = valor.get("valor")
        if not isinstance(texto, str) or not _QUALQUER_MARCA.search(texto):
            continue
        antes, pares = _parametros(texto)
        if not pares:
            continue
        proprio = [c for n, c in pares if n in ("valor", "value")]
        if antes.strip() and proprio and any(c.strip() for c in proprio):
            continue                                   # texto dos dois lados: ambíguo, o saneamento descarta
        corrigido = dict(valor)
        corrigido["valor"] = antes.strip() or (proprio[0] if proprio else None)
        for n, c in pares:
            if n in ("valor", "value"):
                continue
            if n in SUBCAMPOS:
                obj = json_reparado(c) if n == "alternativas" else None
                corrigido[n] = obj if obj is not None else c
            else:
                colocar(n, c, f"colado em {nome}")
        if corrigido.get("valor") is not None and _QUALQUER_MARCA.search(str(corrigido["valor"])):
            continue                                   # sobrou marcação: deixa para o saneamento
        novo[nome] = corrigido
        reparos.append(f"{nome} (parâmetros colados)")
    return novo, reparos
