"""Agrupamento por projeto e consolidação dos campos comuns.

O agrupamento inicial é local e burro de propósito (normalização de texto +
similaridade), só para juntar candidatos. Quem decide a grafia boa é a IA, numa
chamada de TEXTO por grupo — barata, sem imagem.
"""

from __future__ import annotations

import logging
import re
import unicodedata
from collections.abc import Callable
from difflib import SequenceMatcher

from .esquema import CAMPOS_DO_PROJETO, Leitura
from .visao import ClienteAPI, consolidar_grupo

_log = logging.getLogger("cv2.grupos")

LIMIAR_SIMILARIDADE = 0.82


def normalizar(texto: str) -> str:
    """Minúsculas, sem acento, sem pontuação, espaços colapsados."""
    if not texto:
        return ""
    sem_acento = "".join(
        c for c in unicodedata.normalize("NFD", texto) if unicodedata.category(c) != "Mn"
    )
    return re.sub(r"[^a-z0-9 ]+", " ", sem_acento.lower()).strip()


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

    for leitura in leituras:
        bruto = (leitura.valores.get("projeto") or "").strip()
        if not bruto:
            grupos.setdefault("(sem projeto)", []).append(leitura)
            continue
        chave = normalizar(bruto)
        destino = next((k for k in chaves if _similares(k, chave)), None)
        if destino is None:
            chaves[chave] = bruto
            destino = chave
        grupos.setdefault(chaves[destino], []).append(leitura)

    return grupos


def consolidar(
    leituras: list[Leitura],
    cliente: ClienteAPI,
    ao_progredir: Callable[[str], None] | None = None,
) -> tuple[dict[str, dict[str, str]], int, int]:
    """Roda a consolidação em cada grupo com 2+ pranchas.

    Devolve (canonicos_por_grupo, tokens_entrada, tokens_saida) e escreve os
    valores canônicos em `leitura.valores` — o valor lido individualmente fica
    preservado nas colunas "* (lido)" da planilha, para você poder auditar.
    """
    grupos = agrupar(leituras)
    canonicos: dict[str, dict[str, str]] = {}
    total_in = total_out = 0

    for nome, itens in grupos.items():
        if nome == "(sem projeto)" or len(itens) < 2:
            continue
        if ao_progredir:
            ao_progredir(nome)
        try:
            dados, t_in, t_out = consolidar_grupo(cliente, nome, itens)
        except Exception as erro:  # noqa: BLE001
            _log.error("Consolidação do grupo %r falhou: %s", nome, erro)
            continue
        total_in += t_in
        total_out += t_out
        canonicos[nome] = dados

        fora = {n.strip() for n in str(dados.get("pranchas_fora_do_grupo", "")).split(",") if n.strip()}

        # Melhor confiança que o grupo teve em cada campo. É ela que o valor
        # canônico herda — senão a planilha pintaria de vermelho justamente os
        # campos que a consolidação acabou de resolver.
        melhor_confianca = {
            campo: max((l.confiancas.get(campo, 0.0) for l in itens), default=0.0)
            for campo in CAMPOS_DO_PROJETO
        }
        # Fotografado ANTES de escrever qualquer valor canônico: se calculássemos
        # isto dentro do laço, o valor recém-aplicado na 1ª prancha se passaria
        # por leitura original nas seguintes.
        lidos_no_grupo = {
            campo: any(l.valores.get(campo) for l in itens) for campo in CAMPOS_DO_PROJETO
        }

        for leitura in itens:
            leitura.lidos_originais = dict(leitura.valores)
            leitura.grupo = nome
            leitura.suspeita_grupo = leitura.arquivo in fora
            for campo in CAMPOS_DO_PROJETO:
                valor = str(dados.get(campo, "") or "").strip()
                if not valor:
                    continue
                # Nenhuma prancha do grupo leu este campo: o valor seria invenção.
                if not lidos_no_grupo[campo]:
                    _log.warning(
                        "Grupo %r: descartado %r em '%s' — nenhuma prancha leu esse campo.",
                        nome, valor, campo,
                    )
                    continue
                leitura.valores[campo] = valor
                leitura.confiancas[campo] = max(
                    leitura.confiancas.get(campo, 0.0), melhor_confianca[campo]
                )
            ano_grupo = str(dados.get("ano_do_projeto", "") or "").strip()
            if ano_grupo:
                leitura.ano_do_projeto = ano_grupo

    _log.info("Consolidação: %d grupo(s) normalizados.", len(canonicos))
    return canonicos, total_in, total_out
