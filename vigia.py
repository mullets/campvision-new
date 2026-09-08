"""CAMP Vision 2 — vigia (modo terminal / automático).

    python vigia.py                          # painel ao vivo, roda até Ctrl+C
    python vigia.py --pasta /Volumes/acervos # define e salva a pasta vigiada
    python vigia.py --uma-vez                # processa o que está pronto e sai (cron)
    python vigia.py --sem-painel             # log corrido, para LaunchAgent
    python vigia.py --relatorio 2026-09-03   # regera o relatório de um dia
    python vigia.py --relatorio-geral         # acervo inteiro desde o começo
    python vigia.py --planilha-geral          # planilha única de todo o acervo
    python vigia.py --marcar-fase             # carimba a fase nova em todos
    python vigia.py --identidade              # confere o crédito que vai nas imagens
    python vigia.py --criar-config            # cria o config.json com os padrões
    python vigia.py --info                    # esquema real dos info_projeto.json
    python vigia.py --todos --estimativa      # quanto custaria passar em tudo
    python vigia.py --todos --uma-vez         # mutirão: passa em tudo, uma vez
    python vigia.py --refazer TEXTO           # devolve projetos à fila (TEXTO=tudo p/ todos)
    python vigia.py --status                 # o que está pendente agora, sem processar

Ctrl+C encerra com elegância: termina a prancha em andamento, grava o
checkpoint e sai. O projeto interrompido não avança de status, então na próxima
subida ele é retomado do ponto em que parou.
"""

from __future__ import annotations

import argparse
import logging
import signal
import sys
from datetime import date, datetime
from pathlib import Path

from nucleo import atualizador, eventos as mod_eventos, registro, relatorio_diario, vigia as mod_vigia
from nucleo.config import VERSAO_BUILD, Config
from nucleo.visao import ClienteAnthropic

PASTA_ESTADO = Path.home() / ".campvision2"
CAMINHO_CONFIG = PASTA_ESTADO / "config.json"
RAIZ_REPO = Path(__file__).resolve().parent

LIMPAR = "\033[2J\033[H"
ESCONDER_CURSOR = "\033[?25l"
MOSTRAR_CURSOR = "\033[?25h"
NEGRITO = "\033[1m"
APAGADO = "\033[2m"
NORMAL = "\033[0m"
VERDE = "\033[32m"
AMARELO = "\033[33m"
VERMELHO = "\033[31m"


class Painel:
    """Desenha o estado do vigia. Degrada para log simples fora do terminal."""

    def __init__(self, interativo: bool) -> None:
        self.interativo = interativo
        self._ultimo_resumo = ""

    def desenhar(self, estado: mod_vigia.EstadoVigia) -> None:
        if not self.interativo:
            resumo = f"{estado.situacao} | fila {estado.fila} | {estado.projeto_atual}"
            if resumo != self._ultimo_resumo:
                logging.getLogger("cv2.vigia").info(resumo)
                self._ultimo_resumo = resumo
            return

        cor = {
            "vigiando": VERDE,
            "processando": AMARELO,
            "consolidando": AMARELO,
        }.get(estado.situacao, VERMELHO)
        no_ar = datetime.now() - estado.inicio
        horas, resto = divmod(int(no_ar.total_seconds()), 3600)

        linhas = [
            f"{NEGRITO}CAMP Vision 2 — vigia{NORMAL}  {APAGADO}build {VERSAO_BUILD}{NORMAL}",
            "─" * 62,
            f"  situação    {cor}● {estado.situacao}{NORMAL}",
            f"  no ar       {horas}h{resto // 60:02d}m",
            f"  fila        {estado.fila} projeto(s) esperando",
        ]
        if estado.projeto_atual:
            total = max(1, estado.total_no_projeto)
            feito = estado.concluidos_no_projeto
            largura = 28
            cheio = int(largura * feito / total)
            barra = "█" * cheio + "░" * (largura - cheio)
            linhas += [
                f"  projeto     {estado.projeto_atual[:44]}",
                f"              {barra} {feito}/{estado.total_no_projeto}",
            ]
        linhas += [
            "",
            f"{NEGRITO}  Hoje{NORMAL}",
            f"  projetos    {estado.projetos_hoje}",
            f"  pranchas    {estado.pranchas_hoje}"
            + (f"   {VERMELHO}{estado.erros_hoje} erro(s){NORMAL}" if estado.erros_hoje else ""),
            f"  custo       US$ {estado.custo_hoje:.2f}",
            "",
            f"{APAGADO}  código      {estado.ultima_atualizacao_codigo}{NORMAL}",
            f"{APAGADO}  relatório   {estado.proximo_relatorio}{NORMAL}",
            "",
            "─" * 62,
        ]
        linhas += [f"{APAGADO}{l}{NORMAL}" for l in estado.ultimas_linhas]
        linhas += ["", f"{APAGADO}  Ctrl+C encerra com segurança{NORMAL}"]
        sys.stdout.write(LIMPAR + "\n".join(linhas) + "\n")
        sys.stdout.flush()


def _comando_status(config: Config) -> int:
    if not config.pasta_vigiada:
        print("Nenhuma pasta vigiada. Rode com --pasta /caminho uma vez.", file=sys.stderr)
        return 1
    raiz = Path(config.pasta_vigiada)
    if not raiz.is_dir():
        print(f"Pasta vigiada indisponível: {raiz}", file=sys.stderr)
        return 1
    todos = mod_vigia.descobrir(raiz, config)
    pendentes = mod_vigia.varrer(raiz, config)
    nomes_pendentes = {p.pasta for p in pendentes}
    print(f"Pasta: {raiz}")
    modo = "mutirão" if config.processar_tudo_sem_fase else "fila normal"
    print(f"Projetos encontrados: {len(todos)}   pendentes: {len(pendentes)}   ({modo})")
    for projeto in todos:
        try:
            relativo = projeto.pasta.relative_to(raiz)
        except ValueError:
            relativo = projeto.pasta
        marca = "PENDENTE" if projeto.pasta in nomes_pendentes else "ok"
        print(f"  [{marca:>8}] {relativo}  ({projeto.pasta_imagens.name}/)")
    if config.auto_atualizar:
        _, mensagem = atualizador.verificar(RAIZ_REPO)
        print(f"Código: {mensagem}")
    return 0


def _comando_identidade(config: Config) -> int:
    """Mostra exatamente o que será gravado dentro de cada imagem."""
    from nucleo import metadados as mod_metadados

    identidade = config.identidade()
    print("Identidade gravada em toda imagem que sai da Fase 2:\n")
    print(f"  Publisher / Credit   {identidade.credito}")
    print(f"  WebStatement         {identidade.site or '(vazio)'}")
    print(f"  Copyright (exemplo)  {identidade.direitos('OSWALDO CORRÊA GONÇALVES')}")
    print(f"  Contato              {identidade.contato or '(vazio)'}")
    print(f"  exiftool             {'encontrado' if mod_metadados.disponivel() else 'AUSENTE — brew install exiftool'}")
    faltando = identidade.problemas()
    if faltando:
        print(f"\n  ATENÇÃO: falta preencher {', '.join(faltando)} em {CAMINHO_CONFIG}")
        return 1
    return 0


def _comando_refazer(config: Config, alvo: str) -> int:
    if not config.pasta_vigiada or not Path(config.pasta_vigiada).is_dir():
        print("Defina a pasta primeiro: --pasta /caminho", file=sys.stderr)
        return 1
    raiz = Path(config.pasta_vigiada)
    todos = mod_vigia.descobrir(raiz, config)
    alvo_baixo = alvo.lower()
    limpos = 0
    for projeto in todos:
        if alvo_baixo != "tudo" and alvo_baixo not in str(projeto.pasta).lower():
            continue
        if mod_vigia.limpar_fase(projeto.pasta):
            limpos += 1
            print(f"  devolvido à fila: {projeto.nome}")
    if not limpos:
        print("Nenhum projeto correspondia (ou nenhum tinha a fase marcada).")
        return 0
    print(f"\n{limpos} projeto(s) voltaram para a fila.")
    print("As pranchas já lidas ficam no checkpoint e não serão pagas de novo.")
    print("Rode: python vigia.py --todos --uma-vez")
    return 0


def _comando_estimativa(config: Config) -> int:
    from nucleo import acervo as mod_acervo

    if not config.pasta_vigiada or not Path(config.pasta_vigiada).is_dir():
        print("Defina a pasta primeiro: --pasta /caminho", file=sys.stderr)
        return 1
    historico = mod_eventos.ler(PASTA_ESTADO / "eventos.jsonl")
    e = mod_acervo.estimar(Path(config.pasta_vigiada), config, historico)
    modo = "mutirão (tudo sem a fase)" if config.processar_tudo_sem_fase else "fila normal"
    print(f"Modo: {modo}")
    print(f"Projetos a processar: {e['projetos']}")
    print(f"Pranchas:             {e['pranchas']}")
    if not e["pranchas"]:
        print("\nNada a fazer.")
        return 0
    print(f"Custo estimado:       US$ {e['custo_min']:.2f} a US$ {e['custo_max']:.2f}")
    print(f"                      ({e['origem']})")
    print(f"Tempo aproximado:     {e['horas']:.1f} h com {config.trabalhadores} em paralelo")
    print("\nMaiores projetos:")
    for nome, n in e["por_projeto"][:12]:
        print(f"  {n:>5}  {nome[:64]}")
    if len(e["por_projeto"]) > 12:
        print(f"  ... e mais {len(e['por_projeto']) - 12} projeto(s)")
    print("\nNúmeros aproximados. O custo real aparece ao vivo no painel.")
    return 0


def _comando_info(config: Config) -> int:
    """Levanta o esquema real dos info_projeto.json do acervo."""
    from nucleo import info_projeto as mod_info

    if not config.pasta_vigiada or not Path(config.pasta_vigiada).is_dir():
        print("Defina a pasta primeiro: --pasta /caminho", file=sys.stderr)
        return 1
    projetos = mod_vigia.descobrir(Path(config.pasta_vigiada), config)
    esquema = mod_info.levantar_esquema(projetos)
    if not esquema:
        print(f"Nenhum info_projeto.json encontrado em {len(projetos)} projeto(s).")
        return 0

    print(f"{len(projetos)} projeto(s). Chaves nos info_projeto.json:\n")
    print(f"  {'chave':<28} {'ocorrências':>11}  {'campo':<12} exemplo")
    for chave, n in esquema.items():
        alvo = mod_info.mapear(chave.split(".")[-1]) or "—"
        print(f"  {chave:<28} {n:>11}  {alvo:<12} {mod_info.exemplo_de(projetos, chave)}")
    nao_mapeadas = [c for c in esquema if not mod_info.mapear(c.split(".")[-1])]
    if nao_mapeadas:
        print(f"\n{len(nao_mapeadas)} chave(s) sem campo correspondente (marcadas com —).")
        print("Se alguma delas importa, dá para mapear em nucleo/info_projeto.py.")
    return 0


def _comando_planilha_geral(config: Config) -> int:
    from nucleo import acervo as mod_acervo

    if not config.pasta_vigiada:
        print("Nenhuma pasta definida. Rode com --pasta /caminho.", file=sys.stderr)
        return 1
    raiz = Path(config.pasta_vigiada)
    if not raiz.is_dir():
        print(f"Pasta indisponível: {raiz}", file=sys.stderr)
        return 1
    caminho, projetos, pranchas = mod_acervo.escrever(raiz, config)
    print(f"{projetos} projeto(s), {pranchas} prancha(s)")
    print(f"Planilha: {caminho}")
    return 0


def _comando_marcar_fase(config: Config) -> int:
    if not config.pasta_vigiada:
        print("Nenhuma pasta definida. Rode com --pasta /caminho.", file=sys.stderr)
        return 1
    raiz = Path(config.pasta_vigiada)
    if not raiz.is_dir():
        print(f"Pasta indisponível: {raiz}", file=sys.stderr)
        return 1
    projetos = mod_vigia.descobrir(raiz, config)
    for projeto in projetos:
        mod_vigia.marcar_fase(projeto.pasta, config)
    print(f"Fase '{config.fase_organizacao}' carimbada em {len(projetos)} projeto(s).")
    return 0


def _comando_relatorio_geral(config: Config) -> int:
    todos = mod_eventos.ler(PASTA_ESTADO / "eventos.jsonl")
    pendentes = None
    if config.pasta_vigiada and Path(config.pasta_vigiada).is_dir():
        pendentes = len(mod_vigia.varrer(Path(config.pasta_vigiada), config))
    caminho = relatorio_diario.escrever_geral(todos, PASTA_ESTADO / "relatorios", pendentes)
    print(caminho.read_text(encoding="utf-8"))
    return 0


def _comando_relatorio(config: Config, texto_data: str) -> int:
    try:
        dia = date.fromisoformat(texto_data) if texto_data != "hoje" else date.today()
    except ValueError:
        print("Data inválida. Use AAAA-MM-DD ou 'hoje'.", file=sys.stderr)
        return 1
    do_dia = mod_eventos.ler(PASTA_ESTADO / "eventos.jsonl", dia)
    caminho = relatorio_diario.escrever(do_dia, PASTA_ESTADO / "relatorios", dia)
    print(caminho.read_text(encoding="utf-8"))
    erro = relatorio_diario.enviar_email(config, caminho.read_text(encoding="utf-8"), dia)
    if erro and config.email_ativo:
        print(f"\n(email não enviado: {erro})", file=sys.stderr)
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=f"CAMP Vision 2 — vigia (build {VERSAO_BUILD})")
    p.add_argument("--pasta", type=str, help="Pasta a vigiar; aceita smb:// (salva no config)")
    p.add_argument("--uma-vez", action="store_true", help="Processa o pendente e sai")
    p.add_argument("--sem-painel", action="store_true", help="Log corrido, sem redesenho")
    p.add_argument("--status", action="store_true", help="Mostra o pendente e sai")
    p.add_argument("--relatorio", metavar="AAAA-MM-DD", help="Regera o relatório de um dia")
    p.add_argument("--relatorio-geral", action="store_true", help="Acervo inteiro desde o começo")
    p.add_argument("--planilha-geral", action="store_true", help="Planilha única de todo o acervo")
    p.add_argument("--marcar-fase", action="store_true", help="Carimba a fase nova em todos os projetos")
    p.add_argument("--identidade", action="store_true", help="Mostra o crédito gravado nas imagens")
    p.add_argument("--criar-config", action="store_true", help="Cria o config.json com os padrões")
    p.add_argument("--info", action="store_true", help="Mostra o esquema dos info_projeto.json")
    p.add_argument("--todos", action="store_true",
                   help="Mutirão: ignora o status antigo, processa tudo que não tem a fase")
    p.add_argument("--refazer", metavar="TEXTO",
                   help="Tira a fase dos projetos cujo caminho contém TEXTO "
                        "(use 'tudo' para todos), devolvendo-os à fila")
    p.add_argument("--estimativa", action="store_true",
                   help="Conta pranchas e estima custo e tempo, sem chamar a API")
    p.add_argument("--sem-auto-atualizar", action="store_true")
    p.add_argument("--intervalo", type=int, help="Segundos entre varreduras")
    args = p.parse_args(argv)

    PASTA_ESTADO.mkdir(parents=True, exist_ok=True)
    if args.criar_config:
        criado = Config.criar_se_faltar(CAMINHO_CONFIG)
        print(("Criado: " if criado else "Já existia (não mexi): ") + str(CAMINHO_CONFIG))
        if not criado:
            print("Para começar do zero, apague o arquivo e rode de novo.")
        return 0
    config = Config.carregar(CAMINHO_CONFIG)
    if args.pasta:
        from nucleo.caminho import de_url_smb

        alvo = de_url_smb(str(args.pasta))
        if not alvo.is_dir():
            print(f"Pasta não encontrada: {alvo}", file=sys.stderr)
            print(mod_vigia.diagnosticar_pasta(alvo), file=sys.stderr)
            return 1
        config.pasta_vigiada = str(alvo)
        config.salvar(CAMINHO_CONFIG)
        print(f"Pasta vigiada: {alvo}")
    if args.intervalo:
        config.intervalo_varredura_segundos = args.intervalo
    if args.sem_auto_atualizar:
        config.auto_atualizar = False
    if args.todos:
        config.processar_tudo_sem_fase = True

    registro.configurar(PASTA_ESTADO / "vigia.log")
    logging.getLogger("cv2").info("CAMP Vision 2 build %s", VERSAO_BUILD)

    if args.identidade:
        return _comando_identidade(config)
    if args.refazer:
        return _comando_refazer(config, args.refazer)
    if args.estimativa:
        return _comando_estimativa(config)
    if args.info:
        return _comando_info(config)
    if args.planilha_geral:
        return _comando_planilha_geral(config)
    if args.marcar_fase:
        return _comando_marcar_fase(config)
    if args.relatorio_geral:
        return _comando_relatorio_geral(config)
    if args.relatorio:
        return _comando_relatorio(config, args.relatorio)
    if args.status:
        return _comando_status(config)

    if not config.pasta_vigiada:
        print("Nenhuma pasta vigiada. Rode com --pasta /caminho uma vez.", file=sys.stderr)
        return 1

    try:
        cliente = ClienteAnthropic(config)
    except RuntimeError as erro:
        print(erro, file=sys.stderr)
        return 1

    vigilante = mod_vigia.Vigia(config, cliente, PASTA_ESTADO, repo=RAIZ_REPO)
    interativo = sys.stdout.isatty() and not args.sem_painel
    painel = Painel(interativo)

    def encerrar(*_):
        vigilante.cancelar.set()

    signal.signal(signal.SIGINT, encerrar)
    signal.signal(signal.SIGTERM, encerrar)

    if args.uma_vez:
        feitos = vigilante.uma_rodada()
        print(f"{feitos} projeto(s) processado(s).")
        return 0

    if interativo:
        sys.stdout.write(ESCONDER_CURSOR)
    try:
        vigilante.rodar(ao_desenhar=painel.desenhar)
    finally:
        if interativo:
            sys.stdout.write(MOSTRAR_CURSOR + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
