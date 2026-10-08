"""Passada 3 — consolidação por grupo, em CÓDIGO (método de leitura §4).

Nenhuma chamada de modelo. Para cada grupo (projeto lido no carimbo + revisão;
a pasta é só pista) e cada campo do projeto:

- consenso = MODA do valor normalizado (não média, não "o mais completo");
- grafia canônica = a grafia mais frequente dentro do vencedor; empate → a
  mais longa. Um "HOSWALDO" isolado nunca sequestra a canônica porque, sozinho,
  nem chega a ser candidato (quarentena);
- QUARENTENA: valor visto uma vez só não corrige ninguém enquanto existir
  valor confirmado (2+ leituras);
- quem discorda vira outlier_<campo>, com o lido original guardado — nunca
  sobrescrito em silêncio;
- ano do grupo = moda dos anos; o ano de cada prancha fica intacto. Prancha cujo
  ano diverge mas tem o ano do grupo entre as ALTERNATIVAS ganha ressalva
  ("lido 85, série indica 83 — conferir"), não correção.
- grupo com poucas folhas dentro de uma pasta dominada por outro projeto:
  suspeita_grupo (folha de outra obra).
"""

from __future__ import annotations

import logging
import re
import unicodedata
from collections.abc import Callable
from difflib import SequenceMatcher

from collections import Counter

from .esquema import CAMPOS_DO_PROJETO, Leitura

_log = logging.getLogger("cv2.grupos")

LIMIAR_SIMILARIDADE = 0.82


# Abreviações expandidas ANTES de comparar (CV-16): "MAL. FLORIANO" = "MARECHAL FLORIANO".
ABREVIACOES = (
    (r"\bmal\b|\bmar\b|\bmrl\b|\bmlr\b", "marechal"), (r"\bcap\b", "capitao"),
    (r"\bcel\b", "coronel"), (r"\bgal\b|\bgen\b", "general"), (r"\bdr\b", "doutor"),
    (r"\bprof\b", "professor"), (r"\bpres\b", "presidente"), (r"\bsta\b", "santa"),
    (r"\bsto\b", "santo"), (r"\br\b", "rua"), (r"\bav\b", "avenida"), (r"\bal\b", "alameda"),
    (r"\bpca\b", "praca"), (r"\bestr\b", "estrada"), (r"\brod\b", "rodovia"),
    (r"\besq\b|\bx\b", "esquina com"), (r"\bn\b|\bno\b|\bnum\b", ""),
)


def normalizar(texto: str) -> str:
    """Minúsculas, sem acento, sem pontuação, abreviações expandidas, espaços colapsados."""
    if not texto:
        return ""
    sem_acento = "".join(
        c for c in unicodedata.normalize("NFD", texto) if unicodedata.category(c) != "Mn"
    )
    base = re.sub(r"[^a-z0-9 ]+", " ", sem_acento.lower())
    for padrao, troca in ABREVIACOES:
        base = re.sub(padrao, troca, base)
    return re.sub(r"\s+", " ", base).strip()


def unidade_e_revisao(leitura: Leitura) -> tuple[str, str]:
    """('ABC-1', 'R-1') a partir de codigo_unidade/revisao/codigo_serie (CV-08)."""
    v = leitura.valores
    bruto = (v.get("codigo_unidade") or "").strip()
    revisao = (v.get("revisao") or "").strip()
    achado = re.search(r"/\s*(R-?\s*\d+)\b", bruto, re.IGNORECASE)
    if achado:
        revisao = revisao or achado.group(1).upper().replace(" ", "")
        bruto = bruto[:achado.start()].strip()
    if not revisao:
        texto = " ".join(v.get(c, "") or "" for c in ("codigo_serie", "titulo_prancha"))
        achado = re.search(r"/\s*R-?\s*(\d+)\b|\bREV\.?\s*([A-Z0-9]{1,3})\b|\b(\d+)\s*[ªa]\s*revis",
                           texto, re.IGNORECASE)
        if achado:
            revisao = "R-" + next(g for g in achado.groups() if g).upper()
    return bruto, revisao


def _similares(a: str, b: str) -> bool:
    if not a or not b:
        return False
    if a in b or b in a:
        return True
    return SequenceMatcher(None, a, b).ratio() >= LIMIAR_SIMILARIDADE


def agrupar(leituras: list[Leitura]) -> dict[str, list[Leitura]]:
    """Agrupa por nome de projeto aproximado.

    Pranchas sem projeto legível caem em "(sem projeto)" — não são espalhadas
    por adivinhação. Ficam visíveis na planilha para você decidir.
    """
    grupos: dict[str, list[Leitura]] = {}
    chaves: dict[str, str] = {}  # chave normalizada -> nome de exibição
    chaves_rev: dict[str, str] = {}

    for leitura in leituras:
        unidade, revisao = unidade_e_revisao(leitura)
        if revisao and not leitura.valores.get("revisao"):
            leitura.valores["revisao"] = revisao
        # Código de unidade escrito na folha separa obras melhor que o nome (CV-08).
        bruto = unidade or (leitura.valores.get("projeto") or "").strip()
        if bruto and revisao:  # McD-1 e McD-1/R-1 são obras diferentes (§3.2)
            bruto = f"{bruto} [{revisao}]"
        if not bruto:
            grupos.setdefault("(sem projeto)", []).append(leitura)
            continue
        chave = normalizar(bruto)
        rev = normalizar(revisao)
        # Revisão diferente nunca junta (McD-1 × McD-1/R-1), por mais parecido que seja.
        destino = next((k for k in chaves if chaves_rev.get(k) == rev and _similares(k, chave)), None)
        if destino is None:
            chaves[chave] = bruto
            chaves_rev[chave] = rev
            destino = chave
        grupos.setdefault(chaves[destino], []).append(leitura)

    return grupos


def _consenso(valores: list[str]) -> tuple[str, str]:
    """(normalizado_vencedor, grafia_canônica) ou ('', '') sem consenso."""
    if not valores:
        return "", ""
    contagem = Counter(normalizar(v) for v in valores)
    confirmados = {k: n for k, n in contagem.items() if n >= 2}
    if confirmados:
        vencedor = max(confirmados, key=lambda k: (confirmados[k], len(k)))
    elif len(contagem) == 1:
        vencedor = next(iter(contagem))
    else:
        return "", ""  # só valores isolados e diferentes: ninguém corrige ninguém
    grafias = Counter(v for v in valores if normalizar(v) == vencedor)
    canonica = max(grafias, key=lambda g: (grafias[g], len(g)))
    return vencedor, canonica


def data_iso(data: str) -> str:
    """'14.10.83' → '1983-10-14'; '08/82' → '1982-08'; '1975' → '1975'. Sem chute."""
    d = (data or "").strip()
    achado = re.search(r"\b(\d{1,2})[./-](\d{1,2})[./-](\d{2}|\d{4})\b", d)
    if achado:
        dia, mes, ano = achado.groups()
        ano = ano if len(ano) == 4 else f"19{ano}"
        if 1 <= int(mes) <= 12 and 1 <= int(dia) <= 31:
            return f"{ano}-{int(mes):02d}-{int(dia):02d}"
    achado = re.search(r"\b(\d{1,2})[./-](\d{2}|\d{4})\b", d)
    if achado and 1 <= int(achado.group(1)) <= 12:
        ano = achado.group(2) if len(achado.group(2)) == 4 else f"19{achado.group(2)}"
        return f"{ano}-{int(achado.group(1)):02d}"
    return _ano(d)


def _ano(valor: str) -> str:
    achado = re.search(r"\b(1[89]\d{2}|20\d{2})\b", valor or "")
    return achado.group(1) if achado else ""


def consolidar(
    leituras: list[Leitura],
    cliente=None,
    ao_progredir: Callable[[str], None] | None = None,
) -> tuple[dict[str, dict[str, str]], int, int]:
    """Consolida cada grupo em código. `cliente` é ignorado (mantido por compatibilidade).

    Devolve (canonicos_por_grupo, 0, 0): não há custo de API.
    """
    grupos = agrupar(leituras)
    canonicos: dict[str, dict[str, str]] = {}
    total = len(leituras)
    maior = max((len(v) for k, v in grupos.items() if k != "(sem projeto)"), default=0)

    for nome, itens in grupos.items():
        if nome == "(sem projeto)":
            continue
        if ao_progredir:
            ao_progredir(nome)
        for leitura in itens:
            leitura.grupo = nome
            if not leitura.lidos_originais:
                leitura.lidos_originais = dict(leitura.valores)
        # Folhas de outra obra no meio da pasta (Araruama dentro de A. Abreu).
        if total >= 5 and len(itens) < maior and len(itens) / total <= 0.2:
            for leitura in itens:
                leitura.suspeita_grupo = True

        dados: dict[str, str] = {}
        for campo in CAMPOS_DO_PROJETO:
            lidos = [l.lidos_originais.get(campo, "") for l in itens]
            vencedor, canonica = _consenso([v for v in lidos if v])
            if not vencedor:
                continue
            dados[campo] = canonica
            melhor_conf = max((l.confiancas.get(campo, 0.0) for l in itens
                               if normalizar(l.lidos_originais.get(campo, "")) == vencedor), default=0.0)
            for leitura in itens:
                original = leitura.lidos_originais.get(campo, "")
                if original and normalizar(original) != vencedor:
                    leitura.outliers.append(campo)  # discorda: marcado, não apagado
                    if campo == "arquiteto":
                        continue  # autoria nunca é "corrigida" pelo consenso (§4.4)
                leitura.valores[campo] = canonica
                leitura.confiancas[campo] = max(leitura.confiancas.get(campo, 0.0), melhor_conf)

        # Endereço: variantes e conflito de verdade (rua ou número diferentes) — CV-16.
        enderecos = [l.lidos_originais.get("endereco", "") for l in itens if l.lidos_originais.get("endereco")]
        variantes = sorted(set(enderecos))
        nums = {tuple(re.findall(r"\d+", normalizar(e))) for e in enderecos}
        ruas = {re.sub(r"\d+", "", normalizar(e)).strip() for e in enderecos}
        conflito = len(variantes) > 1 and (len(nums) > 1 or not all(
            _similares(a, b) for a in ruas for b in ruas))
        for leitura in itens:
            leitura.endereco_variantes = variantes if len(variantes) > 1 else []
            leitura.conflito_endereco = conflito

        anos = [_ano(l.valores.get("ano", "")) for l in itens]
        anos = [a for a in anos if a]
        if anos:
            moda = Counter(anos).most_common(1)[0]
            ano_grupo = moda[0] if (moda[1] >= 2 or len(set(anos)) == 1) else ""
        else:
            ano_grupo = ""
        dados["ano_do_projeto"] = ano_grupo
        for leitura in itens:
            leitura.ano_do_projeto = ano_grupo
            proprio = _ano(leitura.valores.get("ano", ""))
            leitura.data_lida = leitura.data_lida or leitura.lidos_originais.get("data", "") \
                or leitura.valores.get("data", "")
            leitura.data_iso = data_iso(leitura.data_lida)
            if ano_grupo and proprio and abs(int(proprio) - int(ano_grupo)) >= 2:
                # CV-12: lida preservada, sugerida = ano do grupo, ressalva em português.
                leitura.data_outlier = True
                leitura.data_sugerida = ano_grupo
                nota = (f"data lida '{leitura.data_lida or proprio}' destoa do conjunto ({ano_grupo}); "
                        f"leitura mais provável {ano_grupo} — conferir no original antes de publicar "
                        f"qualquer data de {proprio}")
                if nota not in leitura.ressalvas:
                    leitura.ressalvas.append(nota)
            if ano_grupo and proprio and proprio != ano_grupo:
                leitura.outliers.append("ano")
                alternativas = " ".join(leitura.alternativas.get("data", []) + leitura.alternativas.get("ano", []))
                sufixo = ano_grupo[2:]
                if ano_grupo in alternativas or re.search(rf"\b{sufixo}\b", alternativas):
                    leitura.ressalvas.append(
                        f"ano lido {proprio}, mas a série indica {ano_grupo} e essa leitura "
                        f"está entre as alternativas — conferir no original antes de publicar")
        for leitura in itens:
            leitura.outliers = sorted(set(leitura.outliers))
        canonicos[nome] = dados

    _log.info("Consolidação (código): %d grupo(s).", len(canonicos))
    return canonicos, 0, 0
