"""Planilha única do acervo.

Junta o que cada projeto produziu numa planilha só, na raiz. Não gasta API:
é remontada dos `catalogacao/leituras.json` de cada projeto, então você pode
rodar quantas vezes quiser, a qualquer momento, e ela sempre reflete o estado
atual do acervo.

Três CSV em `_catalogacao/` (só CSV desde 07/10/2026):
  acervo.csv    — uma linha por prancha, o acervo inteiro
  projetos.csv  — uma linha por projeto: quantas pranchas, cobertura, o que revisar
  pendentes.csv — o que ainda não passou pela leitura
"""

from __future__ import annotations

import csv
import logging
from dataclasses import dataclass
from pathlib import Path

from . import planilha as mod_planilha
from .config import Config
from .esquema import CAMPOS, Leitura
from .planilha import LIMIAR_ATENCAO
from .vigia import NOME_STATUS, descobrir, ler_status

_log = logging.getLogger("cv2.acervo")

NOME_ARQUIVO = "acervo.csv"


@dataclass
class ProjetoNoAcervo:
    nome: str
    caminho_relativo: str
    leituras: list[Leitura]
    status: str
    fase: str

    @property
    def pranchas(self) -> int:
        return len(self.leituras)

    @property
    def com_carimbo(self) -> int:
        return sum(1 for l in self.leituras if l.carimbo_encontrado)

    @property
    def a_revisar(self) -> int:
        return sum(
            1
            for l in self.leituras
            for nome, valor in l.valores.items()
            if valor and l.confiancas.get(nome, 0.0) < LIMIAR_ATENCAO
        )

    @property
    def fundo(self) -> str:
        for leitura in self.leituras:
            if leitura.pista_fundo:
                return leitura.pista_fundo
        return ""

    @property
    def divergencias(self) -> int:
        return sum(1 for l in self.leituras if l.divergencias)

    @property
    def anos(self) -> str:
        valores = sorted({l.valores.get("ano", "") for l in self.leituras if l.valores.get("ano")})
        if not valores:
            return ""
        return valores[0] if len(valores) == 1 else f"{valores[0]}–{valores[-1]}"

    def campo_predominante(self, campo: str) -> str:
        contagem: dict[str, int] = {}
        for leitura in self.leituras:
            valor = (leitura.valores.get(campo) or "").strip()
            if valor:
                contagem[valor] = contagem.get(valor, 0) + 1
        return max(contagem, key=contagem.get) if contagem else ""


def estimar(raiz: Path, config: Config, historico: list | None = None) -> dict:
    """Conta o que seria processado e estima custo e tempo, sem chamar a API.

    Números aproximados de propósito: servem para decidir se vale soltar o
    mutirão, não para fechar orçamento. O custo real aparece ao vivo no painel.
    """
    from .vigia import varrer

    pendentes = varrer(raiz, config)
    pranchas = 0
    por_projeto: list[tuple[str, int]] = []
    for projeto in pendentes:
        n = len(projeto.arquivos(config))
        pranchas += n
        try:
            rel = str(projeto.pasta.relative_to(raiz))
        except ValueError:
            rel = projeto.pasta.name
        por_projeto.append((rel, n))

    # Se já há histórico de lotes reais, o custo médio por prancha medido vale
    # muito mais que qualquer conta minha — o primeiro palpite estava pela
    # metade do observado. Sem histórico, cai no valor calibrado abaixo.
    medido = _custo_medio_por_prancha(historico or [])
    if medido:
        piso, teto = pranchas * medido * 0.8, pranchas * medido * 1.5
        origem = f"medido no seu acervo (US$ {medido:.3f}/prancha)"
    else:
        # Medido no acervo CAMP em setembro/2026: ~US$ 0,05 por prancha, com a
        # maioria precisando de 2 chamadas. A primeira estimativa deste app
        # ficou 5x abaixo disso — por isso o número agora vem de medição, e o
        # histórico real substitui esta constante assim que existir.
        piso, teto = pranchas * 0.035, pranchas * 0.06
        origem = "referência do acervo CAMP (sem histórico seu ainda)"

    # ~4s por chamada, dividido pelos trabalhadores.
    segundos = pranchas * 4 / max(1, config.trabalhadores)

    return {
        "projetos": len(pendentes),
        "pranchas": pranchas,
        "origem": origem,
        "custo_min": piso,
        "custo_max": teto,
        "horas": segundos / 3600,
        "por_projeto": sorted(por_projeto, key=lambda kv: -kv[1]),
    }


def _custo_medio_por_prancha(eventos: list) -> float:
    """Custo por prancha observado nos lotes já rodados. 0 se não há amostra."""
    pranchas = sum(e.pranchas for e in eventos if not e.falha)
    custo = sum(e.custo_usd for e in eventos if not e.falha)
    if pranchas < 20 or custo <= 0:  # amostra pequena demais para confiar
        return 0.0
    return custo / pranchas


def _ler_fase(caminho: Path) -> str:
    import json

    if not caminho.exists():
        return ""
    try:
        dados = json.loads(caminho.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ""
    return str(dados.get("fase", "")) if isinstance(dados, dict) else ""


def levantar(raiz: Path, config: Config) -> tuple[list[ProjetoNoAcervo], list[str]]:
    """Percorre o acervo e devolve (processados, pendentes)."""
    processados: list[ProjetoNoAcervo] = []
    pendentes: list[str] = []

    for projeto in descobrir(raiz, config):
        try:
            relativo = str(projeto.pasta.relative_to(raiz))
        except ValueError:
            relativo = projeto.pasta.name
        json_leituras = projeto.pasta / "catalogacao" / "leituras.json"
        if not json_leituras.exists():
            pendentes.append(relativo)
            continue
        leituras = mod_planilha.ler_json(json_leituras)
        if not leituras:
            pendentes.append(relativo)
            continue
        processados.append(
            ProjetoNoAcervo(
                nome=projeto.nome,
                caminho_relativo=relativo,
                leituras=leituras,
                status=ler_status(projeto.pasta / NOME_STATUS),
                fase=_ler_fase(projeto.pasta / NOME_STATUS),
            )
        )
    return processados, pendentes


def _escrever_csv(destino: Path, cabecalho: list[str], linhas: list[list]) -> None:
    destino.parent.mkdir(parents=True, exist_ok=True)
    temporario = destino.with_name(f".{destino.name}.tmp")
    with temporario.open("w", encoding="utf-8-sig", newline="") as f:
        escritor = csv.writer(f)
        escritor.writerow(cabecalho)
        escritor.writerows(linhas)
    temporario.replace(destino)


def _linhas_projetos(projetos: list[ProjetoNoAcervo]) -> list[list]:
    linhas = []
    for projeto in sorted(projetos, key=lambda p: -p.pranchas):
        pct = round(projeto.com_carimbo / projeto.pranchas * 100) if projeto.pranchas else 0
        linhas.append([
            projeto.caminho_relativo, projeto.fundo,
            projeto.campo_predominante("projeto"), projeto.campo_predominante("arquiteto"),
            projeto.campo_predominante("cidade"), projeto.anos,
            projeto.pranchas, projeto.com_carimbo, pct, projeto.a_revisar,
            projeto.divergencias, projeto.status, projeto.fase,
        ])
    return linhas


COLUNAS_PROJETOS = [
    "Pasta", "Fundo", "Projeto (lido)", "Arquiteto", "Cidade", "Ano(s)",
    "Pranchas", "Com carimbo", "%", "A revisar", "Divergências", "Status", "Fase",
]


def escrever(raiz: Path, config: Config) -> tuple[Path, int, int]:
    """Monta os CSV do acervo. Devolve (acervo.csv, projetos, pranchas)."""
    projetos, pendentes = levantar(raiz, config)
    pasta_saida = raiz / config.pasta_acervo
    todas = [l for p in projetos for l in p.leituras]

    destino = pasta_saida / NOME_ARQUIVO
    mod_planilha.escrever_csv(todas, destino)
    _escrever_csv(pasta_saida / "projetos.csv", COLUNAS_PROJETOS, _linhas_projetos(projetos))
    _escrever_csv(pasta_saida / "pendentes.csv", ["Pasta ainda não catalogada"],
                  [[c] for c in sorted(pendentes)])

    pranchas = sum(p.pranchas for p in projetos)
    _log.info(
        "Planilha do acervo: %d projeto(s), %d prancha(s), %d pendente(s) → %s",
        len(projetos), pranchas, len(pendentes), destino,
    )
    return destino, len(projetos), pranchas
