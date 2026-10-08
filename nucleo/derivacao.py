"""Passada 4 do método de leitura: derivação por REGRAS sobre texto já lido. Sem modelo, sem chute.

Especificação: docs/metodo-de-leitura.md §5. Regra validada em produção no F023 (1.042 gravados, 0 erros de escrita,
0 divergências na reconferência).

Por que é permitido derivar aqui: o nome da prancha é TRANSCRIÇÃO do que está escrito na folha; inferir dali é ler a fonte.
Inferir biografia, contexto, autoria ou "ano provável pelo estilo" é adivinhação e continua proibido (§5.1, §7).
"""
from __future__ import annotations

import re
import unicodedata

# Ordem canônica de saída (§5.2).
ORDEM: tuple[str, ...] = (
    "Implantação", "Planta", "Corte", "Elevação", "Fachada", "Detalhe", "Estrutura", "Instalações",
    "Mobiliário", "Perspectiva", "Croqui", "Fotografia", "Levantamento", "Documento", "Tabela",
)

# Gatilhos (tabela da §5.2). Convenção da tabela:
#   `termo*`  = prefixo de palavra (`detalhe*` pega "detalhes"); `termo` sem asterisco = a PALAVRA INTEIRA.
# É por isso que a tabela lista `foto, fotos` e `forma/formas` separados, e que "Esquadrias de Ferro" (plural; o gatilho é
# `esquadria`) ficou entre os 254 títulos sem tipo. A lição do back-test (§5.4) é a mesma: sempre limite de palavra.
GATILHOS: dict[str, tuple[str, ...]] = {
    "Implantação": ("implanta*", "situação", "locação", "urbanístic*", "urbaniz*", "loteamento"),
    "Planta": ("planta*", "pav", "pavimento", "térreo", "andar", "cobertura", "lay-out", "subsolo", "mezanino", "sobreloja"),
    "Corte": ("corte*", "seção"),
    "Elevação": ("elevaç*",),
    "Fachada": ("fachada*",),
    "Detalhe": ("detalhe*", "ampliaç*", "esquadria"),
    "Estrutura": ("estrutur*", "forma", "formas", "armaç*", "fundaç*", "viga*", "pilar*", "laje", "concreto", "sapata", "baldrame"),
    "Instalações": ("instalaç*", "hidráulic*", "elétric*", "sanitári*", "esgoto", "pluviais", "telefon*", "ar condicionado",
                    "climatiz*", "gás", "luminotéc*"),
    "Mobiliário": ("mobili*", "marcenaria", "móveis", "armário", "bancada"),
    "Perspectiva": ("perspectiva",),
    "Croqui": ("croqui", "esboço"),
    "Fotografia": ("foto", "fotos", "fotografia"),
    "Levantamento": ("levantamento", "curvas de nível", "topografi*"),
    "Documento": ("memorial", "carta", "correspondênc*", "contrato", "ofício"),
    "Tabela": ("tabela", "quadro de", "planilha"),
}
MAXIMO_TERMOS = 3


def normalizar(texto: str | None) -> str:
    """Minúsculas e sem acento (os gatilhos passam pela mesma normalização)."""
    if not texto:
        return ""
    sem_acento = "".join(c for c in unicodedata.normalize("NFD", str(texto)) if unicodedata.category(c) != "Mn")
    return sem_acento.lower()


def _padrao(gatilho: str) -> re.Pattern[str]:
    base = normalizar(gatilho)
    prefixo = base.endswith("*")
    miolo = r"\s+".join(re.escape(p) for p in base.rstrip("*").split())
    return re.compile(r"(?<![a-z0-9])" + miolo + (r"[a-z0-9]*" if prefixo else r"(?![a-z0-9])"))


_DETECTORES: dict[str, tuple[re.Pattern[str], ...]] = {t: tuple(_padrao(g) for g in gs) for t, gs in GATILHOS.items()}


def tipos_detectados(texto: str | None) -> list[str]:
    """Os termos cujos gatilhos aparecem no texto, na ordem canônica, com a supressão Implantação > Planta."""
    t = normalizar(texto)
    achados = [termo for termo in ORDEM if any(p.search(t) for p in _DETECTORES[termo])]
    if "Implantação" in achados and "Planta" in achados:
        achados.remove("Planta")   # uma implantação já é uma planta: "Planta de Situação" -> "Implantação"
    return achados


def tipo_de_desenho(nome_da_prancha: str | None) -> str | None:
    """`Planta / Corte / Fachada` etc.; no máximo 3 termos, na ordem canônica. Nenhum gatilho = None (abstenção: nunca chutar)."""
    achados = tipos_detectados(nome_da_prancha)[:MAXIMO_TERMOS]
    return " / ".join(achados) if achados else None


# §5.5: documentação não é obra. Filtro aplicado ANTES de qualquer classificação de programa/uso/natureza.
# `F023-P0181 "Documentos do escritório"` recebeu Escritórios/Obra nova pela regra de `escritorio`: um erro publicado.
_DOCUMENTACAO = tuple(_padrao(g) for g in (
    "documento*", "currículo*", "caderno*", "pasta", "pastas", "recorte*", "correspondênc*", "memorial*", "contrato*"))


def e_ficha_de_documentacao(titulo: str | None) -> bool:
    """True quando o título descreve DOCUMENTO (currículo, caderno, pasta, recortes, correspondência, memorial, contrato)."""
    t = normalizar(titulo)
    return any(p.search(t) for p in _DOCUMENTACAO)
