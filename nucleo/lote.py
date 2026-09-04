"""Execução do lote — Fase 1 (extrair).

Princípios:
- Esta fase NÃO move, renomeia nem apaga arquivo nenhum. Só lê e escreve
  planilha. Erro de leitura aqui custa uma linha errada na planilha, nunca uma
  prancha na pasta errada.
- Checkpoint em JSONL a cada arquivo terminado: queda de energia, disco cheio
  ou cancelamento não perdem o que já foi lido nem custam API de novo.
- Cancelar cancela de verdade (shutdown com cancel_futures), não espera a fila.
"""

from __future__ import annotations

import json
import logging
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from . import imagem as img_mod
from .config import Config
from .esquema import Leitura
from .registro import arquivo_em_processamento
from .visao import ClienteAPI, LeitorDeCarimbo

_log = logging.getLogger("cv2.lote")

NOME_CHECKPOINT = "campvision2_checkpoint.jsonl"


@dataclass
class Progresso:
    total: int = 0
    concluidos: int = 0
    com_carimbo: int = 0
    erros: int = 0
    tokens_entrada: int = 0
    tokens_saida: int = 0
    arquivo_atual: str = ""


@dataclass
class ResultadoLote:
    leituras: list[Leitura] = field(default_factory=list)
    progresso: Progresso = field(default_factory=Progresso)
    cancelado: bool = False


class CacheDeRegiao:
    """Guarda a última região de carimbo bem lida — pranchas do mesmo projeto
    quase sempre têm o carimbo no mesmo lugar, então a próxima já vai direto
    ao recorte, com 1 chamada em vez de 2."""

    def __init__(self) -> None:
        self._regiao: img_mod.Caixa | None = None
        self._trava = threading.Lock()

    def obter(self) -> img_mod.Caixa | None:
        with self._trava:
            return self._regiao

    def guardar(self, regiao: img_mod.Caixa | None) -> None:
        if regiao is None:
            return
        with self._trava:
            self._regiao = regiao


def _carregar_checkpoint(caminho: Path) -> dict[str, Leitura]:
    if not caminho.exists():
        return {}
    feitos: dict[str, Leitura] = {}
    for linha in caminho.read_text(encoding="utf-8").splitlines():
        linha = linha.strip()
        if not linha:
            continue
        try:
            leitura = Leitura.de_dict(json.loads(linha))
        except (json.JSONDecodeError, TypeError, KeyError):
            _log.warning("Linha inválida no checkpoint, ignorada.")
            continue
        if not leitura.erro:  # erro não conta como feito: tenta de novo
            feitos[leitura.arquivo] = leitura
    return feitos


def executar(
    pasta: Path,
    config: Config,
    cliente: ClienteAPI,
    ao_progredir: Callable[[Progresso], None] | None = None,
    cancelar: threading.Event | None = None,
) -> ResultadoLote:
    """Lê todas as imagens da pasta e devolve as leituras."""
    cancelar = cancelar or threading.Event()
    arquivos = img_mod.listar_imagens(pasta, config.extensoes)
    checkpoint = pasta / NOME_CHECKPOINT
    feitos = _carregar_checkpoint(checkpoint) if config.retomar_checkpoint else {}
    if feitos:
        _log.info("Checkpoint encontrado: %d prancha(s) já lidas serão puladas.", len(feitos))

    pendentes = [a for a in arquivos if a.name not in feitos]
    resultado = ResultadoLote()
    resultado.leituras.extend(feitos.values())
    prog = resultado.progresso
    prog.total = len(arquivos)
    prog.concluidos = len(feitos)
    prog.com_carimbo = sum(1 for l in feitos.values() if l.carimbo_encontrado)

    if not pendentes:
        _log.info("Nada a fazer: todas as %d pranchas já estavam no checkpoint.", len(arquivos))
        if ao_progredir:
            ao_progredir(prog)
        return resultado

    _log.info("Lote iniciado: %d prancha(s) a ler de %d na pasta.", len(pendentes), len(arquivos))
    leitor = LeitorDeCarimbo(config, cliente)
    cache = CacheDeRegiao()
    trava_arquivo = threading.Lock()

    def processar(caminho: Path) -> Leitura:
        if cancelar.is_set():
            return Leitura(arquivo=caminho.name, erro="cancelado")
        with arquivo_em_processamento(caminho.name):
            leitura = leitor.ler(caminho, cache.obter())
            if leitura.carimbo_encontrado:
                cache.guardar(leitura.regiao)
            return leitura

    with ThreadPoolExecutor(max_workers=max(1, config.trabalhadores)) as executor:
        futuros = {executor.submit(processar, a): a for a in pendentes}
        try:
            for futuro in as_completed(futuros):
                leitura = futuro.result()
                if leitura.erro == "cancelado":
                    continue
                resultado.leituras.append(leitura)
                prog.concluidos += 1
                prog.arquivo_atual = leitura.arquivo
                prog.tokens_entrada += leitura.tokens_entrada
                prog.tokens_saida += leitura.tokens_saida
                if leitura.erro:
                    prog.erros += 1
                elif leitura.carimbo_encontrado:
                    prog.com_carimbo += 1

                with trava_arquivo:
                    with checkpoint.open("a", encoding="utf-8") as f:
                        f.write(json.dumps(leitura.para_dict(), ensure_ascii=False) + "\n")

                if ao_progredir:
                    ao_progredir(prog)

                if cancelar.is_set():
                    resultado.cancelado = True
                    _log.info("Cancelamento pedido — descartando a fila.")
                    executor.shutdown(wait=False, cancel_futures=True)
                    break
        except KeyboardInterrupt:  # pragma: no cover
            resultado.cancelado = True
            executor.shutdown(wait=False, cancel_futures=True)
            raise

    _log.info(
        "Lote terminado: %d lidas, %d com carimbo, %d erro(s).",
        prog.concluidos, prog.com_carimbo, prog.erros,
    )
    return resultado
