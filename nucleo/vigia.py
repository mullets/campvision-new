"""Vigia — o modo automático.

Varre a pasta montada, acha projetos prontos, lê os carimbos, escreve a
planilha e avança o semáforo `status.json`:

    enviado_windows  ->  campvision_concluido

Nunca processa duas vezes o mesmo projeto: quem manda é o status no disco, não
estado em memória — reiniciar o Mac não perde nem repete nada.

A Fase 2 (mexer em arquivo) continua desligada por padrão mesmo aqui. O vigia
automatiza a leitura, não a decisão.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path

from . import atualizador, eventos as mod_eventos, grupos, imagem, lote, planilha
from . import relatorio_diario
from .config import Config
from .planilha import LIMIAR_ATENCAO
from .visao import ClienteAPI

_log = logging.getLogger("cv2.vigia")

NOME_STATUS = "status.json"
NOME_PLANILHA = "catalogacao.xlsx"
SUBPASTAS_IMAGEM = ("JPG", "jpg", "JPEG", "IMAGENS")


@dataclass
class Projeto:
    pasta: Path
    pasta_imagens: Path
    nome: str

    @property
    def caminho_status(self) -> Path:
        return self.pasta / NOME_STATUS


@dataclass
class EstadoVigia:
    """O que o painel mostra. Só leitura para quem desenha."""

    inicio: datetime = field(default_factory=datetime.now)
    situacao: str = "iniciando"
    projeto_atual: str = ""
    concluidos_no_projeto: int = 0
    total_no_projeto: int = 0
    fila: int = 0
    projetos_hoje: int = 0
    pranchas_hoje: int = 0
    erros_hoje: int = 0
    custo_hoje: float = 0.0
    ultima_atualizacao_codigo: str = "—"
    proximo_relatorio: str = "—"
    ultimas_linhas: list[str] = field(default_factory=list)

    def anotar(self, texto: str) -> None:
        carimbo = datetime.now().strftime("%H:%M:%S")
        self.ultimas_linhas.append(f"{carimbo}  {texto}")
        del self.ultimas_linhas[:-8]


def ler_status(caminho: Path) -> str:
    if not caminho.exists():
        return ""
    try:
        dados = json.loads(caminho.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return ""
    if isinstance(dados, str):
        return dados
    if isinstance(dados, dict):
        return str(dados.get("status", ""))
    return ""


def escrever_status(caminho: Path, status: str, extra: dict | None = None) -> None:
    """Preserva o que já estava no arquivo — outras máquinas escrevem aqui também."""
    atual: dict = {}
    if caminho.exists():
        try:
            carregado = json.loads(caminho.read_text(encoding="utf-8"))
            if isinstance(carregado, dict):
                atual = carregado
        except (json.JSONDecodeError, OSError):
            pass
    atual["status"] = status
    atual["campvision2_em"] = datetime.now().isoformat(timespec="seconds")
    atual.update(extra or {})
    caminho.write_text(json.dumps(atual, indent=2, ensure_ascii=False), encoding="utf-8")


def _pasta_de_imagens(pasta: Path, config: Config) -> Path | None:
    """Prefere a subpasta JPG/ (é o que o F10 do Windows gera); senão, a raiz."""
    for nome in SUBPASTAS_IMAGEM:
        candidata = pasta / nome
        if candidata.is_dir() and imagem.listar_imagens(candidata, config.extensoes):
            return candidata
    return pasta if imagem.listar_imagens(pasta, config.extensoes) else None


def varrer(raiz: Path, config: Config) -> list[Projeto]:
    """Lista os projetos prontos para processar, em ordem de chegada."""
    if not raiz.is_dir():
        return []
    achados: list[tuple[float, Projeto]] = []
    for pasta in raiz.iterdir():
        if not pasta.is_dir() or pasta.name.startswith("."):
            continue
        status = ler_status(pasta / NOME_STATUS)
        if config.exigir_status_json:
            if status != config.status_pronto:
                continue
        else:
            if status == config.status_concluido or (pasta / NOME_PLANILHA).exists():
                continue
        pasta_imagens = _pasta_de_imagens(pasta, config)
        if pasta_imagens is None:
            continue
        achados.append((pasta.stat().st_mtime, Projeto(pasta, pasta_imagens, pasta.name)))
    return [p for _, p in sorted(achados, key=lambda item: item[0])]


def processar(
    projeto: Projeto,
    config: Config,
    cliente: ClienteAPI,
    estado: EstadoVigia | None = None,
    cancelar: threading.Event | None = None,
) -> mod_eventos.Evento:
    """Processa um projeto de ponta a ponta e devolve o evento do diário."""
    comeco = time.monotonic()
    evento = mod_eventos.Evento(projeto=projeto.nome, pasta=str(projeto.pasta))
    _log.info("Projeto %s: iniciando.", projeto.nome)
    if estado:
        estado.situacao = "processando"
        estado.projeto_atual = projeto.nome
        estado.concluidos_no_projeto = 0

    try:
        def progresso(p: lote.Progresso) -> None:
            if estado:
                estado.concluidos_no_projeto = p.concluidos
                estado.total_no_projeto = p.total

        resultado = lote.executar(
            projeto.pasta_imagens, config, cliente,
            ao_progredir=progresso, cancelar=cancelar,
        )
        t_in = resultado.progresso.tokens_entrada
        t_out = resultado.progresso.tokens_saida

        if config.consolidar_por_projeto and resultado.leituras:
            if estado:
                estado.situacao = "consolidando"
            _, c_in, c_out = grupos.consolidar(resultado.leituras, cliente)
            t_in += c_in
            t_out += c_out

        custo = config.custo_estimado_usd(t_in, t_out)
        destino = projeto.pasta / "catalogacao"
        planilha.escrever_xlsx(resultado.leituras, destino / NOME_PLANILHA)
        planilha.escrever_csv(resultado.leituras, destino / "catalogacao.csv")
        planilha.escrever_relatorio(resultado.leituras, destino / "relatorio.txt", custo)

        evento.pranchas = len(resultado.leituras)
        evento.com_carimbo = sum(1 for l in resultado.leituras if l.carimbo_encontrado)
        evento.erros = sum(1 for l in resultado.leituras if l.erro)
        evento.campos_a_revisar = sum(
            1
            for l in resultado.leituras
            for nome, valor in l.valores.items()
            if valor and l.confiancas.get(nome, 0.0) < LIMIAR_ATENCAO
        )
        evento.custo_usd = custo

        if resultado.cancelado:
            evento.falha = "cancelado antes de terminar"
            _log.warning("Projeto %s: cancelado, status não avançou.", projeto.nome)
        else:
            escrever_status(
                projeto.caminho_status,
                config.status_concluido,
                {
                    "campvision2_pranchas": evento.pranchas,
                    "campvision2_com_carimbo": evento.com_carimbo,
                    "campvision2_a_revisar": evento.campos_a_revisar,
                },
            )
            _log.info(
                "Projeto %s: %d prancha(s), %d com carimbo, US$ %.2f.",
                projeto.nome, evento.pranchas, evento.com_carimbo, custo,
            )
    except Exception as erro:  # noqa: BLE001 - um projeto ruim não pode derrubar o vigia
        evento.falha = str(erro)
        _log.exception("Projeto %s falhou.", projeto.nome)

    evento.duracao_segundos = int(time.monotonic() - comeco)
    return evento


def _proxima_hora(hora_texto: str, agora: datetime) -> datetime:
    try:
        hora, minuto = (int(p) for p in hora_texto.split(":", 1))
    except ValueError:
        hora, minuto = 18, 0
    alvo = agora.replace(hour=hora, minute=minuto, second=0, microsecond=0)
    return alvo if alvo > agora else alvo + timedelta(days=1)


class Vigia:
    """O laço principal. Sem dependência de UI — o painel é opcional."""

    def __init__(
        self,
        config: Config,
        cliente: ClienteAPI,
        pasta_estado: Path,
        repo: Path | None = None,
    ) -> None:
        self.config = config
        self.cliente = cliente
        self.pasta_estado = pasta_estado
        self.repo = repo
        self.caminho_eventos = pasta_estado / "eventos.jsonl"
        self.estado = EstadoVigia()
        self.cancelar = threading.Event()
        self._proximo_relatorio = _proxima_hora(config.hora_relatorio, datetime.now())
        self._proxima_atualizacao = datetime.now()
        self.estado.proximo_relatorio = self._proximo_relatorio.strftime("%d/%m %H:%M")

    # ------------------------------------------------------------ tarefas
    def _talvez_atualizar(self) -> None:
        if not (self.config.auto_atualizar and self.repo):
            return
        if datetime.now() < self._proxima_atualizacao:
            return
        self._proxima_atualizacao = datetime.now() + timedelta(
            minutes=self.config.intervalo_atualizacao_minutos
        )
        atualizou, mensagem = atualizador.atualizar(self.repo)
        self.estado.ultima_atualizacao_codigo = (
            f"{datetime.now():%d/%m %H:%M} — {mensagem}"
        )
        if atualizou:
            self.estado.anotar(f"código atualizado ({mensagem}), reiniciando")
            atualizador.reiniciar_processo()

    def _talvez_relatorio(self) -> None:
        if datetime.now() < self._proximo_relatorio:
            return
        dia = (self._proximo_relatorio - timedelta(minutes=1)).date()
        do_dia = mod_eventos.ler(self.caminho_eventos, dia)
        caminho = relatorio_diario.escrever(do_dia, self.pasta_estado / "relatorios", dia)
        erro = relatorio_diario.enviar_email(
            self.config, caminho.read_text(encoding="utf-8"), dia
        )
        self.estado.anotar(
            f"relatório de {dia:%d/%m} pronto"
            + (f" (email: {erro[:40]})" if erro else " e enviado por email")
        )
        self._proximo_relatorio = _proxima_hora(self.config.hora_relatorio, datetime.now())
        self.estado.proximo_relatorio = self._proximo_relatorio.strftime("%d/%m %H:%M")

    def _contabilizar(self, evento: mod_eventos.Evento) -> None:
        mod_eventos.registrar(self.caminho_eventos, evento)
        self.estado.projetos_hoje += 1
        self.estado.pranchas_hoje += evento.pranchas
        self.estado.erros_hoje += evento.erros
        self.estado.custo_hoje += evento.custo_usd
        self.estado.anotar(
            f"{evento.projeto}: "
            + (f"FALHOU — {evento.falha[:60]}" if evento.falha
               else f"{evento.com_carimbo}/{evento.pranchas} com carimbo, US$ {evento.custo_usd:.2f}")
        )

    def _recarregar_contadores_do_dia(self) -> None:
        hoje = mod_eventos.ler(self.caminho_eventos, date.today())
        self.estado.projetos_hoje = len(hoje)
        self.estado.pranchas_hoje = sum(e.pranchas for e in hoje)
        self.estado.erros_hoje = sum(e.erros for e in hoje)
        self.estado.custo_hoje = sum(e.custo_usd for e in hoje)

    # -------------------------------------------------------------- laços
    def uma_rodada(self) -> int:
        """Processa tudo o que está pronto agora. Devolve quantos projetos rodou."""
        raiz = Path(self.config.pasta_vigiada)
        pendentes = varrer(raiz, self.config)
        self.estado.fila = len(pendentes)
        if not pendentes:
            self.estado.situacao = "vigiando"
            self.estado.projeto_atual = ""
            return 0

        feitos = 0
        for projeto in pendentes:
            if self.cancelar.is_set():
                break
            self._talvez_atualizar()  # só entre projetos, nunca no meio de um lote
            evento = processar(projeto, self.config, self.cliente, self.estado, self.cancelar)
            self._contabilizar(evento)
            feitos += 1
            self.estado.fila = max(0, self.estado.fila - 1)
        self.estado.situacao = "vigiando"
        self.estado.projeto_atual = ""
        return feitos

    def rodar(self, ao_desenhar=None) -> None:
        """Laço infinito até cancelar. `ao_desenhar` recebe o EstadoVigia."""
        self._recarregar_contadores_do_dia()
        raiz = Path(self.config.pasta_vigiada)
        _log.info("Vigia no ar. Pasta: %s", raiz)
        ultimo_dia = date.today()

        while not self.cancelar.is_set():
            if date.today() != ultimo_dia:  # virou o dia: zera o painel
                ultimo_dia = date.today()
                self._recarregar_contadores_do_dia()
            if not raiz.is_dir():
                self.estado.situacao = "pasta indisponível"
                self.estado.anotar(f"pasta {raiz} fora do ar (SMB caiu?) — seguindo tentando")
            else:
                self.uma_rodada()
            self._talvez_relatorio()
            if ao_desenhar:
                ao_desenhar(self.estado)
            # Espera fatiada para o cancelamento responder na hora.
            for _ in range(max(1, self.config.intervalo_varredura_segundos)):
                if self.cancelar.is_set():
                    break
                time.sleep(1)
                if ao_desenhar:
                    ao_desenhar(self.estado)
        _log.info("Vigia encerrado.")
