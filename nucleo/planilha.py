"""Planilha — o entregável da Fase 1, só em CSV (decisão de 07/10/2026).

A planilha é feita para REVISÃO: a coluna "Revisar" diz sim quando há campo
com confiança baixa, divergência com a pasta, carimbo não achado ou erro, e
"Campos a revisar" diz quais. Cada campo tem sua confiança numa coluna
espelho, e "OK?" é onde se marca o que já foi conferido. A Fase 2 (aplicar)
lê este mesmo arquivo depois de editado.

CSV em UTF-8 com BOM e vírgula — abre certo no Numbers, no Excel e no painel.
"""

from __future__ import annotations

import csv
import json
import logging
from pathlib import Path

from .esquema import CAMPOS, CAMPOS_POR_NOME, Leitura

_log = logging.getLogger("cv2.planilha")

LIMIAR_ATENCAO = 0.60
LIMIAR_BOM = 0.85


def _colunas() -> list[str]:
    fixas = ["Código", "Arquivo", "OK?", "Revisar", "Campos a revisar",
             "Grupo/Projeto", "Carimbo?", "Confiança média"]
    campos = [c.rotulo for c in CAMPOS]
    da_pasta = ["Fundo (pasta)", "Projeto (pasta)", "Ano (pasta)", "Divergência"]
    espelho = [f"conf. {c.rotulo}" for c in CAMPOS]
    lidos = [f"lido: {c.rotulo}" for c in CAMPOS if c.do_projeto]
    return (
        fixas + campos + da_pasta
        + ["Rotação", "Passes", "Nota da IA", "Erro"] + espelho + lidos
    )


def campos_a_revisar(leitura: Leitura) -> list[str]:
    """Rótulos dos campos que pedem olho humano nesta leitura."""
    nomes = [
        c.rotulo for c in CAMPOS
        if (leitura.valores.get(c.nome) or "").strip()
        and leitura.confiancas.get(c.nome, 0.0) < LIMIAR_ATENCAO
    ]
    nomes += [CAMPOS_POR_NOME[n].rotulo for n in leitura.divergencias
              if n in CAMPOS_POR_NOME and CAMPOS_POR_NOME[n].rotulo not in nomes]
    return nomes


def precisa_revisar(leitura: Leitura) -> bool:
    return bool(
        leitura.erro or not leitura.carimbo_encontrado or leitura.suspeita_grupo
        or campos_a_revisar(leitura)
    )


def _linha(leitura: Leitura) -> list[object]:
    fixas: list[object] = [
        Path(leitura.arquivo).stem,
        leitura.arquivo,
        "",
        "sim" if precisa_revisar(leitura) else "não",
        ", ".join(campos_a_revisar(leitura)),
        leitura.grupo or leitura.valores.get("projeto", ""),
        "sim" if leitura.carimbo_encontrado else "NÃO",
        round(leitura.confianca_media, 2),
    ]
    campos = [leitura.valores.get(c.nome, "") for c in CAMPOS]
    rotulos_divergentes = ", ".join(
        CAMPOS_POR_NOME[n].rotulo for n in leitura.divergencias if n in CAMPOS_POR_NOME
    )
    da_pasta: list[object] = [
        leitura.pista_fundo,
        leitura.pista_projeto,
        leitura.pista_ano,
        f"carimbo ≠ pasta: {rotulos_divergentes}" if rotulos_divergentes else "",
    ]
    extras: list[object] = [
        leitura.rotacao,
        leitura.passes,
        leitura.nota_ia + (" [pode ser de outro projeto]" if leitura.suspeita_grupo else ""),
        leitura.erro,
    ]
    espelho = [round(leitura.confiancas.get(c.nome, 0.0), 2) for c in CAMPOS]
    lidos = [leitura.lidos_originais.get(c.nome, "") for c in CAMPOS if c.do_projeto]
    return fixas + campos + da_pasta + extras + espelho + lidos


def _ordenar(leituras: list[Leitura]) -> list[Leitura]:
    """Agrupadas por projeto, e dentro do projeto por número de folha."""

    def folha_num(leitura: Leitura) -> tuple[int, str]:
        bruto = (leitura.valores.get("folha") or "").strip()
        digitos = "".join(ch for ch in bruto.split("/")[0] if ch.isdigit())
        return (int(digitos), bruto) if digitos else (10**9, leitura.arquivo)

    return sorted(
        leituras,
        key=lambda l: ((l.grupo or l.valores.get("projeto") or "zzz").lower(), folha_num(l)),
    )


def escrever_csv(leituras: list[Leitura], destino: Path) -> Path:
    destino.parent.mkdir(parents=True, exist_ok=True)
    with destino.open("w", encoding="utf-8-sig", newline="") as f:
        escritor = csv.writer(f)
        escritor.writerow(_colunas())
        for leitura in _ordenar(leituras):
            escritor.writerow(_linha(leitura))
    _log.info("CSV escrito: %s", destino)
    return destino


def escrever_json(leituras: list[Leitura], destino: Path) -> Path:
    """Leituras finais em JSON — a fonte para remontar a planilha do acervo."""
    destino.parent.mkdir(parents=True, exist_ok=True)
    destino.write_text(
        json.dumps([l.para_dict() for l in leituras], ensure_ascii=False, indent=1),
        encoding="utf-8",
    )
    return destino


def ler_json(caminho: Path) -> list[Leitura]:
    """Lê o JSON de leituras. Arquivo corrompido devolve lista vazia com aviso."""
    try:
        dados = json.loads(caminho.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as erro:
        _log.warning("Não consegui ler %s: %s", caminho, erro)
        return []
    if not isinstance(dados, list):
        return []
    return [Leitura.de_dict(d) for d in dados if isinstance(d, dict)]


def escrever_relatorio(leituras: list[Leitura], destino: Path, custo_usd: float = 0.0) -> Path:
    """Resumo em texto: o que dá para olhar em 10 segundos e saber se o lote prestou."""
    total = len(leituras)
    com_carimbo = sum(1 for l in leituras if l.carimbo_encontrado)
    com_erro = sum(1 for l in leituras if l.erro)
    linhas = [
        "CAMP Vision 2 — relatório do lote",
        "=" * 40,
        f"Pranchas processadas: {total}",
        f"Carimbo encontrado:   {com_carimbo} ({com_carimbo / total * 100:.0f}%)" if total else "",
        f"Erros:                {com_erro}",
        f"Custo estimado:       US$ {custo_usd:.2f}",
        "",
        "Preenchimento por campo:",
    ]
    for campo in CAMPOS:
        n = sum(1 for l in leituras if l.valores.get(campo.nome))
        fracos = sum(
            1 for l in leituras
            if l.valores.get(campo.nome) and l.confiancas.get(campo.nome, 0) < LIMIAR_ATENCAO
        )
        pct = (n / total * 100) if total else 0
        linhas.append(f"  {campo.rotulo:<20} {n:>4}/{total} ({pct:>3.0f}%)  a revisar: {fracos}")

    com_2o = [l for l in leituras if l.fez_segundo_passe]
    if com_2o:
        melhorou = [l for l in com_2o if l.confianca_media > l.confianca_antes_do_2o + 0.05]
        delta = sum(l.confianca_media - l.confianca_antes_do_2o for l in com_2o) / len(com_2o)
        ganho = sum(l.ganho_de_resolucao for l in com_2o) / len(com_2o)
        linhas += [
            "",
            "Segundo passe (dobra o custo da prancha — este é o retorno dele):",
            f"  Pranchas que releram   {len(com_2o)}/{total}"
            + (f" ({len(com_2o) / total * 100:.0f}%)" if total else ""),
            f"  Melhoraram de fato     {len(melhorou)}"
            + (f" ({len(melhorou) / len(com_2o) * 100:.0f}%)" if com_2o else ""),
            f"  Confiança média        {delta:+.2f}",
            f"  Ganho de resolução     {ganho:.1f}x",
        ]
        if len(melhorou) < len(com_2o) * 0.4:
            linhas.append(
                "  → o 2º passe está rendendo pouco: considere baixar "
                "confianca_minima_para_aceitar no config."
            )

    grupos: dict[str, int] = {}
    for leitura in leituras:
        chave = leitura.grupo or leitura.valores.get("projeto") or "(sem projeto)"
        grupos[chave] = grupos.get(chave, 0) + 1
    linhas += ["", f"Projetos distintos: {len(grupos)}"]
    for nome, n in sorted(grupos.items(), key=lambda kv: -kv[1]):
        linhas.append(f"  {n:>4}  {nome}")

    destino.parent.mkdir(parents=True, exist_ok=True)
    destino.write_text("\n".join(l for l in linhas if l is not None), encoding="utf-8")
    return destino
