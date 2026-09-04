"""Modo linha de comando — para o watcher automático do Mac (LaunchAgent).

    python cli.py /caminho/da/pasta
    python cli.py /caminho/da/pasta --sem-consolidacao --trabalhadores 6
    python cli.py /caminho/da/pasta --aplicar catalogacao.xlsx        # Fase 2 (simulação)
    python cli.py /caminho/da/pasta --aplicar catalogacao.xlsx --valendo

Sai com código 0 se tudo correu, 1 se houve erro fatal, 2 se o lote terminou
com pranchas em erro (útil para o watcher decidir se muda o status.json).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from nucleo import grupos, lote, planilha, registro
from nucleo.aplicar import executar as aplicar_executar, planejar
from nucleo.config import VERSAO_BUILD, Config
from nucleo.visao import ClienteAnthropic


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=f"CAMP Vision 2 (build {VERSAO_BUILD})")
    p.add_argument("pasta", type=Path, help="Pasta com as pranchas")
    p.add_argument("--modelo", default=None)
    p.add_argument("--trabalhadores", type=int, default=None)
    p.add_argument("--sem-consolidacao", action="store_true")
    p.add_argument("--do-zero", action="store_true", help="Ignora o checkpoint")
    p.add_argument("--aplicar", type=Path, default=None, help="Fase 2 a partir da planilha")
    p.add_argument("--valendo", action="store_true", help="Fase 2 sem simulação")
    args = p.parse_args(argv)

    if not args.pasta.is_dir():
        print(f"Pasta inexistente: {args.pasta}", file=sys.stderr)
        return 1

    registro.configurar(args.pasta / "campvision2.log")

    if args.aplicar:
        caminho = args.aplicar if args.aplicar.is_absolute() else args.pasta / args.aplicar
        acoes = planejar(args.pasta, caminho)
        for mensagem in aplicar_executar(acoes, simular=not args.valendo, gravar_exif=args.valendo):
            print(mensagem)
        print(f"{len(acoes)} prancha(s) {'aplicadas' if args.valendo else 'simuladas'}.")
        return 0

    config = Config.carregar(Path.home() / ".campvision2" / "config.json")
    if args.modelo:
        config.modelo = args.modelo
    if args.trabalhadores:
        config.trabalhadores = args.trabalhadores
    if args.sem_consolidacao:
        config.consolidar_por_projeto = False
    if args.do_zero:
        config.retomar_checkpoint = False

    try:
        cliente = ClienteAnthropic(config)
    except RuntimeError as erro:
        print(erro, file=sys.stderr)
        return 1

    resultado = lote.executar(args.pasta, config, cliente)
    t_in = resultado.progresso.tokens_entrada
    t_out = resultado.progresso.tokens_saida

    if config.consolidar_por_projeto and resultado.leituras:
        _, c_in, c_out = grupos.consolidar(resultado.leituras, cliente)
        t_in += c_in
        t_out += c_out

    custo = config.custo_estimado_usd(t_in, t_out)
    planilha.escrever_xlsx(resultado.leituras, args.pasta / "catalogacao.xlsx")
    planilha.escrever_csv(resultado.leituras, args.pasta / "catalogacao.csv")
    print(planilha.escrever_relatorio(resultado.leituras, args.pasta / "relatorio.txt", custo).read_text())
    return 2 if resultado.progresso.erros else 0


if __name__ == "__main__":
    raise SystemExit(main())
