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

from . import atualizador, caminho as mod_caminho, eventos as mod_eventos, grupos, imagem
from . import info_projeto as mod_info, lote, planilha
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
    pastas_imagens: list[Path]
    nome: str
    raiz: Path | None = None

    @property
    def pasta_imagens(self) -> Path:
        """A principal, para exibição. O lote lê todas."""
        return self.pastas_imagens[0] if self.pastas_imagens else self.pasta

    def arquivos(self, config: Config) -> list[Path]:
        vistos: set[Path] = set()
        todos: list[Path] = []
        for pasta in self.pastas_imagens:
            for arquivo in imagem.listar_imagens(pasta, config.extensoes):
                if arquivo not in vistos:
                    vistos.add(arquivo)
                    todos.append(arquivo)
        return todos

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


MARCADORES_DE_PROJETO = ("status.json", "info_projeto.json")
# Quanto maior, melhor a pasta de imagens. Preview/JPG ganha do arquivo
# matriz em TIFF: para ler carimbo, JPG basta e é muito mais rápido em rede.
PESOS = (
    (("preview", "jpg", "jpeg"), 3),
    (("imagens", "imagem", "fotos"), 2),
    (("tiff", "tif", "arquivistico", "arquivístico", "matriz", "master"), 1),
)
PASTAS_IGNORADAS = ("catalogacao", "__macosx", "node_modules")


def _pontuar_pasta(pasta: Path) -> int:
    nome = pasta.name.lower()
    for palavras, peso in PESOS:
        if any(palavra in nome for palavra in palavras):
            return peso
    return 2  # pasta sem pista no nome: melhor que TIFF, pior que preview


def _imagens_direto(pasta: Path, config: Config) -> bool:
    return bool(imagem.listar_imagens(pasta, config.extensoes))


def achar_pastas_de_imagens(
    projeto: Path, config: Config, _profundidade: int = 0
) -> list[Path]:
    """Acha, dentro de um projeto, TODAS as pastas de imagem que interessam.

    Cada acervo veio de um fluxo diferente — imagem solta na raiz, dentro de
    JPG/, ou em `01 - Desenhos e Pranchas/03 - Preview (JPG)`. Então a busca
    desce a árvore inteira do projeto, pontua o que acha e fica só com o melhor
    tipo disponível: se há preview em JPG, o TIFF é ignorado; se só há TIFF,
    lê o TIFF.
    """
    candidatas: list[tuple[int, Path]] = []

    def caminhar(pasta: Path, nivel: int) -> None:
        if nivel > config.profundidade_maxima:
            return
        if _imagens_direto(pasta, config):
            candidatas.append((_pontuar_pasta(pasta), pasta))
            return  # achou imagem aqui: não desce mais neste ramo
        try:
            filhos = sorted(pasta.iterdir(), key=lambda p: p.name.lower())
        except (PermissionError, OSError) as erro:
            _log.warning("Não consegui listar %s: %s", pasta, erro)
            return
        for filho in filhos:
            if not filho.is_dir():
                continue
            if filho.name.startswith(".") or filho.name.lower() in PASTAS_IGNORADAS:
                continue
            caminhar(filho, nivel + 1)

    caminhar(projeto, _profundidade)
    if not candidatas:
        return []

    melhor = max(peso for peso, _ in candidatas)
    escolhidas = [pasta for peso, pasta in candidatas if peso == melhor]
    descartadas = [pasta.name for peso, pasta in candidatas if peso != melhor]
    if descartadas:
        _log.info(
            "Projeto %s: usando %s; ignorando %s (versão de menor prioridade).",
            projeto.name,
            ", ".join(p.name for p in escolhidas),
            ", ".join(descartadas),
        )
    return sorted(escolhidas)


PROJETO, CONTAINER, FOLHA, NADA = "projeto", "container", "folha", "nada"


def classificar(pasta: Path, config: Config, nivel: int = 0) -> str:
    """Diz o que uma pasta é dentro do acervo.

    - PROJETO   — tem marcador (status.json/info_projeto.json), OU seus filhos
                  são todos pastas de imagem (o caso `Projeto/{JPG,TIF}`)
    - FOLHA     — tem imagens direto (é uma pasta de imagem)
    - CONTAINER — tem projetos abaixo (fundo, ano, agrupador)
    - NADA      — nem imagem nem projeto abaixo

    A classificação é de baixo para cima. É isso que distingue
    `Fundo OCG/1968/Teatro/JPG` (container/container/projeto/folha) de
    `Projeto/{JPG,TIF}` (projeto/folha,folha) sem depender do nome das pastas.
    """
    if any((pasta / marcador).exists() for marcador in MARCADORES_DE_PROJETO):
        return PROJETO
    if _imagens_direto(pasta, config):
        return FOLHA
    if nivel > config.profundidade_maxima:
        return NADA

    try:
        filhos = [
            f for f in pasta.iterdir()
            if f.is_dir() and not f.name.startswith(".")
            and f.name.lower() not in PASTAS_IGNORADAS
        ]
    except (PermissionError, OSError) as erro:
        _log.warning("Não consegui listar %s: %s", pasta, erro)
        return NADA

    tipos = [classificar(f, config, nivel + 1) for f in filhos]
    if PROJETO in tipos or CONTAINER in tipos:
        return CONTAINER
    if FOLHA in tipos:
        return PROJETO  # só pastas de imagem abaixo: a pasta É o projeto
    return NADA


def garantir_status(pasta: Path, config: Config) -> str:
    """Cria o status.json quando a pasta não tem um.

    Pasta com imagens e sem semáforo é pasta que chegou por fora do fluxo do
    Windows (cópia manual, disco antigo, importação). Em vez de ignorar em
    silêncio — que foi como projetos inteiros ficaram parados —, o vigia cria
    o arquivo já marcado como pronto e registra quem o criou.
    """
    caminho = pasta / NOME_STATUS
    if caminho.exists():
        return ler_status(caminho)
    if not config.criar_status_ausente:
        return ""
    escrever_status(
        caminho,
        config.status_pronto,
        {"criado_por": "campvision2", "origem": "pasta sem status.json"},
    )
    _log.info("Pasta %s não tinha status.json — criado como '%s'.", pasta.name, config.status_pronto)
    return config.status_pronto


def marcar_fase(pasta: Path, config: Config, extra: dict | None = None) -> None:
    """Carimba `fase` no status.json — o marcador de que passou por esta versão.

    Fica numa chave própria de propósito: mexer no `status` quebraria o watcher
    do QNAP, que procura exatamente por `status_concluido`.
    """
    caminho = pasta / NOME_STATUS
    dados: dict = {}
    if caminho.exists():
        try:
            carregado = json.loads(caminho.read_text(encoding="utf-8"))
            if isinstance(carregado, dict):
                dados = carregado
        except (json.JSONDecodeError, OSError):
            dados = {}
    dados["fase"] = config.fase_organizacao
    dados["fase_em"] = datetime.now().isoformat(timespec="seconds")
    dados.update(extra or {})
    dados.setdefault("status", config.status_pronto)
    caminho.parent.mkdir(parents=True, exist_ok=True)
    caminho.write_text(json.dumps(dados, indent=2, ensure_ascii=False), encoding="utf-8")


def _e_ignoravel(pasta: Path, config: Config) -> bool:
    nome = pasta.name
    if nome.startswith(".") or nome.startswith("_"):
        return True
    return nome in {"catalogacao", config.pasta_acervo.strip("/"), "__MACOSX", "node_modules"}


def descobrir(
    raiz: Path, config: Config, _profundidade: int = 0, _raiz: Path | None = None
) -> list[Projeto]:
    """Acha projetos em qualquer nível abaixo da raiz.

    Usa `classificar` para decidir o que é projeto e o que é agrupador, de
    baixo para cima. Em acervo organizado o projeto (`P0001 - ...`) não tem
    imagem na raiz dele — elas estão duas camadas abaixo, separadas em TIFF e
    preview —, e sem isso cada subpasta de imagem viraria um projeto irmão.
    """
    if not raiz.is_dir() or _profundidade > config.profundidade_maxima:
        return []

    raiz_real = _raiz or raiz
    achados: list[Projeto] = []

    # A própria raiz pode ter imagens soltas (saída de scanner).
    if _profundidade == 0 and _imagens_direto(raiz, config):
        achados.append(Projeto(raiz, [raiz], raiz.name, raiz_real))

    try:
        filhos = sorted(raiz.iterdir(), key=lambda p: p.name.lower())
    except (PermissionError, OSError) as erro:
        _log.warning("Não consegui listar %s: %s", raiz, erro)
        return achados

    for pasta in filhos:
        if not pasta.is_dir() or _e_ignoravel(pasta, config):
            continue
        tipo = classificar(pasta, config)
        if tipo in (PROJETO, FOLHA):
            pastas = achar_pastas_de_imagens(pasta, config)
            if pastas:
                achados.append(Projeto(pasta, pastas, pasta.name, raiz_real))
            else:
                _log.info("Projeto %s marcado, mas sem imagem — pulado.", pasta.name)
            continue  # é projeto: não desce mais
        if tipo == CONTAINER:
            achados.extend(descobrir(pasta, config, _profundidade + 1, raiz_real))
    return achados


def varrer(raiz: Path, config: Config) -> list[Projeto]:
    """Lista os projetos PRONTOS para processar, em ordem de chegada."""
    if not raiz.is_dir():
        return []
    prontos: list[tuple[float, Projeto]] = []
    for projeto in descobrir(raiz, config):
        status = garantir_status(projeto.pasta, config)
        if config.exigir_status_json:
            if status != config.status_pronto:
                continue
        else:
            if status == config.status_concluido or (projeto.pasta / NOME_PLANILHA).exists():
                continue
        prontos.append((projeto.pasta.stat().st_mtime, projeto))
    return [p for _, p in sorted(prontos, key=lambda item: item[0])]


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
            arquivos=projeto.arquivos(config),
            pasta_checkpoint=projeto.pasta,
        )
        t_in = resultado.progresso.tokens_entrada
        t_out = resultado.progresso.tokens_saida

        if config.consolidar_por_projeto and resultado.leituras:
            if estado:
                estado.situacao = "consolidando"
            _, c_in, c_out = grupos.consolidar(resultado.leituras, cliente)
            t_in += c_in
            t_out += c_out

        # A pasta entra AQUI: depois da leitura e da consolidação, nunca antes.
        if config.usar_pasta_como_pista and resultado.leituras:
            pista = mod_caminho.extrair(projeto.pasta, projeto.raiz)
            do_info = mod_info.ler(projeto.pasta)
            preenchidos, divergentes = mod_caminho.aplicar(
                resultado.leituras, pista, do_info=do_info
            )
            if preenchidos or divergentes:
                _log.info(
                    "Pasta %s: %d campo(s) preenchidos pela pasta, %d divergência(s).",
                    projeto.nome, preenchidos, divergentes,
                )

        custo = config.custo_estimado_usd(t_in, t_out)
        destino = projeto.pasta / "catalogacao"
        planilha.escrever_xlsx(resultado.leituras, destino / NOME_PLANILHA)
        planilha.escrever_csv(resultado.leituras, destino / "catalogacao.csv")
        planilha.escrever_relatorio(resultado.leituras, destino / "relatorio.txt", custo)
        # Leituras finais (já consolidadas) em JSON: é daqui que a planilha
        # única do acervo é remontada, sem gastar API de novo.
        planilha.escrever_json(resultado.leituras, destino / "leituras.json")

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
            marcar_fase(projeto.pasta, config)
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
        pasta = self.pasta_estado / "relatorios"
        do_dia = mod_eventos.ler(self.caminho_eventos, dia)
        caminho = relatorio_diario.escrever(do_dia, pasta, dia)
        # O geral é reescrito junto: sempre reflete o acervo inteiro até agora.
        relatorio_diario.escrever_geral(
            mod_eventos.ler(self.caminho_eventos), pasta, self.estado.fila
        )
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

        if feitos:
            self.atualizar_planilha_do_acervo()
        self.estado.situacao = "vigiando"
        self.estado.projeto_atual = ""
        return feitos

    def atualizar_planilha_do_acervo(self) -> None:
        """Reescreve a planilha única da raiz. Não gasta API: remonta dos JSONs."""
        from . import acervo as mod_acervo

        try:
            self.estado.situacao = "montando planilha do acervo"
            _, projetos, pranchas = mod_acervo.escrever(Path(self.config.pasta_vigiada), self.config)
            self.estado.anotar(f"planilha do acervo: {projetos} projeto(s), {pranchas} prancha(s)")
        except Exception as erro:  # noqa: BLE001 - não pode derrubar o vigia
            _log.error("Falha ao montar a planilha do acervo: %s", erro)
            self.estado.anotar(f"planilha do acervo falhou: {erro}")

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
