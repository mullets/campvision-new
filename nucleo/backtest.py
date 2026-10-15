"""Protocolo de validação (método de leitura §9) — contra a base curada à mão.

    python vigia.py --backtest CURADO.csv --contra /caminho/do/acervo

CURADO.csv: uma linha por documento, com a coluna `codigo` (ou `Código`/`arquivo`)
e as colunas curadas que se quer conferir, pelo nome do campo (`arquiteto`,
`ano`, `tipo`...) ou pelo rótulo da planilha (`Arquiteto`, `Ano`, `Tipo`...).
Exporte do Tainacan/painel os registros JÁ revisados por gente (F016, F023).

Para cada campo, três baldes (§9.1):
  exato        — igual depois de normalizar (acento, caixa, pontuação)
  granularidade— ramo certo, granularidade errada: um contém o outro, ou os
                 tipos de desenho se sobrepõem
  categoria    — diferente. Estes são listados UM A UM: é onde moram os bugs.

Critério de liberação: ≥ 85% de acerto exato e as divergências de categoria
examinadas à mão. Abaixo disso, a regra não grava.

Também (§9.2) `reconferir_tipo`: recalcula o tipo de desenho a partir do título
gravado e compara com o tipo gravado — tem que dar 0 divergência.
"""

from __future__ import annotations

import csv
import json
import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path

from .derivacao import tipo_de_desenho
from .esquema import CAMPOS, CAMPOS_POR_NOME, Leitura
from .planilha import ler_json

LIMIAR = 0.85
POR_ROTULO = {c.rotulo.lower(): c.nome for c in CAMPOS}


def _norm(texto: str) -> str:
    sem = "".join(c for c in unicodedata.normalize("NFD", str(texto or ""))
                  if unicodedata.category(c) != "Mn")
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9/ ]", " ", sem.lower())).strip()


@dataclass
class ResultadoCampo:
    campo: str
    exato: int = 0
    granularidade: int = 0
    categoria: list[tuple[str, str, str]] = field(default_factory=list)  # (codigo, curado, lido)
    sem_leitura: int = 0

    @property
    def total(self) -> int:
        return self.exato + self.granularidade + len(self.categoria)

    @property
    def taxa(self) -> float:
        return self.exato / self.total if self.total else 0.0

    @property
    def liberado(self) -> bool:
        return self.total > 0 and self.taxa >= LIMIAR


def comparar(curado: str, lido: str, campo: str) -> str:
    a, b = _norm(curado), _norm(lido)
    if a == b:
        return "exato"
    if campo == "tipo":
        sa = {t.strip() for t in a.split("/") if t.strip()}
        sb = {t.strip() for t in b.split("/") if t.strip()}
        return "granularidade" if sa & sb else "categoria"
    if a and b and (a in b or b in a):
        return "granularidade"
    return "categoria"


def carregar_leituras(raiz: Path) -> dict[str, Leitura]:
    leituras: dict[str, Leitura] = {}
    for arquivo in raiz.rglob("leituras.json"):
        for l in ler_json(arquivo):
            leituras[Path(l.arquivo).stem.lower()] = l
    return leituras


def carregar_curado(caminho: Path) -> list[dict[str, str]]:
    texto = caminho.read_text(encoding="utf-8-sig")
    try:
        dialeto = csv.Sniffer().sniff(texto.splitlines()[0], delimiters=",;\t")
    except csv.Error:
        dialeto = csv.excel
    return list(csv.DictReader(texto.splitlines(), dialect=dialeto))


def _chave(linha: dict[str, str]) -> str:
    for nome in ("codigo", "código", "Código", "arquivo", "Arquivo", "codigo_documento"):
        if linha.get(nome):
            return Path(linha[nome].strip()).stem.lower()
    return ""


def backtest(curado: list[dict[str, str]], leituras: dict[str, Leitura]) -> list[ResultadoCampo]:
    if not curado:
        return []
    colunas = {}
    for coluna in curado[0]:
        nome = coluna if coluna in CAMPOS_POR_NOME else POR_ROTULO.get(coluna.strip().lower())
        if nome:
            colunas[coluna] = nome
    resultados = {nome: ResultadoCampo(nome) for nome in colunas.values()}
    for linha in curado:
        chave = _chave(linha)
        leitura = leituras.get(chave)
        for coluna, nome in colunas.items():
            valor_curado = (linha.get(coluna) or "").strip()
            if not valor_curado:
                continue
            r = resultados[nome]
            if leitura is None:
                r.sem_leitura += 1
                continue
            lido = leitura.valores.get(nome, "")
            balde = comparar(valor_curado, lido, nome)
            if balde == "exato":
                r.exato += 1
            elif balde == "granularidade":
                r.granularidade += 1
            else:
                r.categoria.append((chave, valor_curado, lido))
    return list(resultados.values())


def reconferir_tipo(leituras: dict[str, Leitura]) -> list[tuple[str, str, str]]:
    """(codigo, gravado, recalculado) para cada divergência. Esperado: lista vazia."""
    divergencias = []
    for chave, l in sorted(leituras.items()):
        if l.modo != "prancha":
            continue
        recalculado = tipo_de_desenho(l.valores.get("titulo_prancha", ""))
        if (l.valores.get("tipo") or "") != recalculado:
            divergencias.append((chave, l.valores.get("tipo", ""), recalculado))
    return divergencias


def regravar_tipo(raiz: Path, simular: bool = False) -> list[tuple[str, str, str]]:
    """Recalcula o tipo de desenho e REGRAVA leituras.json (e o tipo no pacote_tainacan.json
    vizinho). Devolve (codigo, gravado, novo) do que mudou; `simular` só lista (CAMP-051)."""
    from . import atomico
    mudou: list[tuple[str, str, str]] = []
    for arquivo in sorted(raiz.rglob("leituras.json")):
        leituras = ler_json(arquivo)
        novos: dict[str, str] = {}
        for l in leituras:
            if l.modo != "prancha":
                continue
            novo = tipo_de_desenho(l.valores.get("titulo_prancha", ""))
            antigo = l.valores.get("tipo") or ""
            if antigo != novo:
                novos[Path(l.arquivo).stem.lower()] = novo
                mudou.append((Path(l.arquivo).stem.lower(), antigo, novo))
                l.valores["tipo"] = novo
        if not novos or simular:
            continue
        atomico.gravar_json(arquivo, [l.para_dict() for l in leituras], indent=1)
        pacote = arquivo.parent / "pacote_tainacan.json"
        if pacote.exists():
            try:
                dados = json.loads(pacote.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            alterou = False
            for lista in ("documentos", "retirados", "itens"):
                for d in dados.get(lista) or []:
                    cod = str(d.get("codigo", "")).lower()
                    if cod in novos:
                        d["tipo"] = d["tipo_de_desenho"] = novos[cod]
                        if isinstance(d.get("metadados"), dict):
                            d["metadados"]["tipo"] = novos[cod]
                        alterou = True
            if alterou:
                atomico.gravar_json(pacote, dados, indent=2)
    return mudou


def relatorio(resultados: list[ResultadoCampo]) -> str:
    linhas = ["Back-test contra a base curada (método §9.1) — liberação: ≥ 85% exato", ""]
    for r in resultados:
        estado = "LIBERADO" if r.liberado else "NÃO LIBERADO"
        linhas.append(
            f"{r.campo:<16} {r.taxa:6.1%} exato  ({r.exato} exato · {r.granularidade} granularidade · "
            f"{len(r.categoria)} categoria · {r.sem_leitura} sem leitura)  {estado}")
    for r in resultados:
        if r.categoria:
            linhas += ["", f"Divergências de CATEGORIA em {r.campo} — examinar uma a uma:"]
            linhas += [f"  {c}: curado {cur!r} · lido {lid!r}" for c, cur, lid in r.categoria]
    return "\n".join(linhas)
