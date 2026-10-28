"""UX-37: valor quebrado da resposta do modelo de visão não vira leitura nem sugestão.

Em produção (F026-P0030, 27/10) a resposta da ferramenta veio com resto de marcação
('UF <parameter name="valor">null') ou com o literal null como texto, e isso foi gravado
como cidade/UF sugerida no pacote. O painel passou a ignorar esses valores
(camp-painel `saidas_cv.valor_sugerido_valido`); aqui a correção é na origem, com o
mesmo critério: valor com marcação de ferramenta (`<parameter`, `</…>`, `<invoke`,
`antml:`…) ou que é só null/None/undefined/NaN/n/a vira vazio.

Recupera a parte boa antes do resíduo só quando é inequívoco: o resíduo é um fechamento
(`SP</parameter>`), o começo de OUTRO parâmetro (`SP <parameter name="confianca">0.9`) ou
uma abertura sem nada depois (`SP <parameter…`). Texto antes E depois da marcação
('UF <parameter name="valor">null') é ambíguo: vazio.
"""

from __future__ import annotations

import logging
import re
from typing import Any

_log = logging.getLogger(__name__)

# Qualquer sinal de marcação de chamada de ferramenta (o mesmo que o painel recusa, e mais).
_MARCA = re.compile(r"<\s*/|<\s*(?:antml:)?(?:parameter|invoke|function\w*|tool\w*|antml)\b|antml:", re.I)
# Uma etiqueta inteira (ou cortada, sem o '>') começando onde a marca começou.
_ETIQUETA = re.compile(r"<\s*(/)?\s*(?:antml:)?(parameter|invoke|function\w*|tool\w*)\b([^<>]*)>?", re.I)
_NOME = re.compile(r"""name\s*=\s*["']?([\w-]+)""", re.I)

NULOS = frozenset({"null", "none", "nulo", "nil", "nan", "undefined", "n/a", "-", "?"})

RESSALVA = "valor descartado: resposta do modelo quebrada"


def _sem_nulo(texto: str) -> str:
    texto = re.sub(r"\s+", " ", texto).strip(" \"'")
    return "" if texto.lower() in NULOS else texto


def tem_marcacao(valor: Any) -> bool:
    return isinstance(valor, str) and bool(_MARCA.search(valor))


def sanear_valor(valor: Any, _nivel: int = 0) -> tuple[str, bool]:
    """(texto limpo, quebrado?). quebrado = havia marcação de ferramenta no valor."""
    if valor is None:
        return "", False
    texto = str(valor)
    marca = _MARCA.search(texto)
    if not marca:
        return _sem_nulo(texto), False
    antes = texto[:marca.start()].strip()
    etiqueta = _ETIQUETA.match(texto, marca.start())
    if etiqueta is None or _nivel > 5:
        return "", True                                   # 'antml:' solto etc.: não dá para saber onde acaba
    fecha, tipo, atributos = etiqueta.group(1), etiqueta.group(2).lower(), etiqueta.group(3) or ""
    nome = (_NOME.search(atributos).group(1).lower() if _NOME.search(atributos) else "")
    embrulho = not fecha and tipo == "parameter" and nome in ("", "valor", "value")
    if fecha or (tipo == "parameter" and not embrulho):
        # Fechamento ou começo de OUTRO parâmetro: o valor acabou antes da marca.
        return _sem_nulo(antes), True
    if not embrulho:
        return "", True                                   # '<invoke…', '<function_calls…' no meio: na dúvida, vazio
    depois_bruto = texto[etiqueta.end():]
    depois, _ = sanear_valor(depois_bruto, _nivel + 1)
    if not antes:
        return depois, True                               # '<parameter name="valor">SÃO PAULO'
    if not depois_bruto.strip():
        return _sem_nulo(antes), True                     # 'SP <parameter…' (cortado)
    return "", True                                       # texto dos dois lados: ambíguo


def _sanear(valor: Any, caminho: str, quebrados: dict[str, bool]) -> Any:
    if isinstance(valor, str):
        limpo, quebrado = sanear_valor(valor)
        if quebrado:
            quebrados[caminho] = quebrados.get(caminho, False) or not limpo
            return limpo
        if not limpo and valor.strip():                   # 'null', 'None', 'undefined' como texto
            return ""
        return valor
    if isinstance(valor, dict):
        return {k: _sanear(v, f"{caminho}.{k}" if caminho else str(k), quebrados) for k, v in valor.items()}
    if isinstance(valor, (list, tuple)):
        itens = [_sanear(v, caminho, quebrados) for v in valor]
        return [v for v in itens if v is not None and v != ""]
    return valor


def sanear(valor: Any, caminho: str = "") -> tuple[Any, dict[str, bool]]:
    """Saneia um valor qualquer (texto, lista, dicionário, em profundidade).

    Devolve (valor limpo, {caminho: ficou_vazio}) só dos textos que tinham marcação.
    Texto sem problema volta idêntico (sem mexer em espaços)."""
    quebrados: dict[str, bool] = {}
    return _sanear(valor, caminho, quebrados), quebrados


def ressalvas(quebrados: dict[str, bool]) -> list[str]:
    """Uma ressalva por campo (o 1º nível do caminho): não some em silêncio."""
    por_campo: dict[str, bool] = {}
    for caminho, vazio in quebrados.items():
        campo = caminho.split(".", 1)[0] or "valor"
        por_campo[campo] = por_campo.get(campo, False) or vazio
    return [f"{RESSALVA} ({campo}{'' if vazio else ', resto de marcação tirado'})"
            for campo, vazio in sorted(por_campo.items())]


def anotar(leitura, quebrados: dict[str, bool], origem: str) -> None:
    for nota in ressalvas(quebrados):
        if nota not in leitura.ressalvas:
            leitura.ressalvas.append(nota)
    if quebrados:
        _log.warning("%s: %s — %s", getattr(leitura, "arquivo", "?"), RESSALVA, origem)


# Campos da Leitura que guardam texto vindo do modelo (ou derivado dele).
_CAMPOS_TEXTO = ("valores", "alternativas", "onde", "lidos_originais", "carimbo", "textual", "foto",
                 "nota_ia", "transcricao_integral", "materiais_citados", "anotacoes_manuscritas",
                 "observacoes_leitura", "endereco_variantes", "data_lida", "data_sugerida", "titulo_publicacao")


def sanear_leitura_gravada(dados: dict[str, Any]) -> tuple[dict[str, Any], dict[str, bool]]:
    """Leitura antiga (leituras.json / checkpoint): o mesmo saneamento, ao carregar.

    Não regrava o arquivo; só impede que o lixo volte para o próximo pacote."""
    todos: dict[str, bool] = {}
    novo = dict(dados)
    for nome in _CAMPOS_TEXTO:
        if nome not in novo:
            continue
        valor, quebrados = sanear(novo[nome])
        novo[nome] = valor
        if nome == "valores":
            todos.update(quebrados)
        else:
            todos.update({f"{nome}.{k}" if k else nome: v for k, v in quebrados.items()})
    antes, depois, confiancas = dados.get("valores"), novo.get("valores"), novo.get("confiancas")
    if isinstance(antes, dict) and isinstance(depois, dict) and isinstance(confiancas, dict):
        esvaziados = {k for k, v in antes.items() if str(v or "").strip() and not str(depois.get(k) or "").strip()}
        novo["confiancas"] = {k: (0.0 if k in esvaziados else c) for k, c in confiancas.items()}
    return novo, todos
