"""Passada 4 — derivação por regra sobre o texto JÁ LIDO (método de leitura §5).

Sem modelo. Derivar o tipo de desenho do nome da prancha é ler a fonte, porque o
nome está escrito na folha. Inferir biografia, contexto, autoria ou "ano provável
pelo estilo" é adivinhação e está proibido.

- ano: só de data escrita (4 dígitos, ou dd.mm.aa → 19aa com a data completa);
- tipo de desenho: 15 detectores com limite de palavra, ordem canônica, até 3,
  Implantação suprime Planta, nenhum gatilho → vazio (§5.2);
- documento ≠ obra: títulos que descrevem documento são marcados antes de
  qualquer classificação de obra (§5.5);
- autoria divergente: arquiteto lido fora do titular/coautores do fundo (§4.4).
"""

from __future__ import annotations

import re
import unicodedata


def normalizar(texto: str) -> str:
    sem_acento = "".join(c for c in unicodedata.normalize("NFD", texto or "")
                         if unicodedata.category(c) != "Mn")
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9/ -]", " ", sem_acento.lower())).strip()


# ---------------------------------------------------------------- ano

def ano_da_data(data: str) -> str:
    """Ano de 4 dígitos a partir da data ESCRITA. Sem data, sem ano."""
    achado = re.search(r"\b(1[89]\d{2}|20\d{2})\b", data or "")
    if achado:
        return achado.group(1)
    # dd.mm.aa completo: acervo do século XX (1900–1999)
    achado = re.search(r"\b\d{1,2}[./-]\d{1,2}[./-](\d{2})\b", data or "")
    return f"19{achado.group(1)}" if achado else ""


# ---------------------------------------------------------------- tipo de desenho

ORDEM = ("Implantação", "Planta", "Corte", "Elevação", "Fachada", "Detalhe", "Estrutura",
         "Instalações", "Mobiliário", "Perspectiva", "Croqui", "Fotografia", "Levantamento",
         "Documento", "Tabela")

# Exatamente a tabela do §5.2: "*" vira \w*; sem "*" casa só a palavra exata
# (por isso "Esquadrias de Ferro" fica em branco e "esquadria" vira Detalhe).
GATILHOS: dict[str, tuple[str, ...]] = {
    "Implantação": (r"implanta\w*", r"situacao", r"locacao", r"urbanistic\w*", r"urbaniz\w*",
                    r"loteamento"),
    "Planta": (r"planta\w*", r"pav", r"pavimento", r"terreo", r"andar", r"cobertura",
               r"lay-?out", r"subsolo", r"mezanino", r"sobreloja"),
    "Corte": (r"corte\w*", r"secao"),
    "Elevação": (r"elevac\w*",),
    "Fachada": (r"fachada\w*",),
    "Detalhe": (r"detalhe\w*", r"ampliac\w*", r"esquadria"),
    "Estrutura": (r"estrutur\w*", r"forma", r"formas", r"armac\w*", r"fundac\w*", r"viga\w*",
                  r"pilar\w*", r"laje", r"concreto", r"sapata", r"baldrame"),
    "Instalações": (r"instalac\w*", r"hidraulic\w*", r"eletric\w*", r"sanitari\w*", r"esgoto",
                    r"pluviais", r"telefon\w*", r"ar condicionado", r"climatiz\w*", r"gas",
                    r"luminotec\w*"),
    "Mobiliário": (r"mobili\w*", r"marcenaria", r"moveis", r"armario", r"bancada"),
    "Perspectiva": (r"perspectiva",),
    "Croqui": (r"croqui", r"esboco"),
    "Fotografia": (r"foto", r"fotos", r"fotografia"),
    "Levantamento": (r"levantamento", r"curvas de nivel", r"topografi\w*"),
    "Documento": (r"memorial", r"carta", r"correspondenc\w*", r"contrato", r"oficio"),
    "Tabela": (r"tabela", r"quadro de", r"planilha"),
}
_COMPILADOS = {t: re.compile(r"\b(" + "|".join(g) + r")\b") for t, g in GATILHOS.items()}


def tipo_de_desenho(titulo: str) -> str:
    """'Planta / Corte' — ou '' quando nenhum gatilho dispara (nunca chutar)."""
    texto = normalizar(titulo)
    if not texto:
        return ""
    achados = [t for t in ORDEM if _COMPILADOS[t].search(texto)]
    if "Implantação" in achados and "Planta" in achados:
        achados.remove("Planta")  # implantação já é planta
    return " / ".join(achados[:3])


TERMOS_DOCUMENTO = re.compile(
    r"\b(documento\w*|curriculo\w*|caderno\w*|pasta\w*|recortes?|correspondenc\w*|memorial\w*|contrato\w*)\b")


def e_documento(titulo: str) -> bool:
    """Ficha de documentação, não obra (caso F023-P0181 'Documentos do escritório')."""
    return bool(TERMOS_DOCUMENTO.search(normalizar(titulo)))


# ---------------------------------------------------------------- autoria

def _tokens_nome(nome: str) -> set[str]:
    ignorar = {"arquiteto", "arquitetos", "arq", "eng", "engenheiro", "associados", "ltda", "sc",
               "s/c", "e", "de", "da", "do", "dos", "das", "arquitetura", "escritorio"}
    return {t for t in normalizar(nome).replace("/", " ").split() if len(t) >= 4 and t not in ignorar}


def autoria_divergente(arquiteto_lido: str, autorizados: list[str]) -> bool:
    """True se o arquiteto lido não é o titular nem um coautor registrado.

    Comparação por sobrenome/nome (tokens de 4+ letras): "ARQ. S. BUSSAB" bate
    com "Sami Bussab". Sem arquiteto lido → não acusa (campo vazio não é prova).
    """
    lidos = _tokens_nome(arquiteto_lido)
    if not lidos or not autorizados:
        return False
    for nome in autorizados:
        if lidos & _tokens_nome(nome):
            return False
    return True
