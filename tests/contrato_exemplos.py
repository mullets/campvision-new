"""Gera os exemplos REAIS do contrato com o painel (ticket 88).

    python -m tests.contrato_exemplos          # regrava docs/contrato/exemplos/

Roda um lote pequeno de verdade (com o modelo simulado) e copia o que o CV2
grava em catalogacao/ e os corpos que ele manda ao painel. O teste
`TestContrato` compara a ESTRUTURA (chaves e tipos) do que o CV2 grava hoje
com estes exemplos: mudou um campo sem mudar `CONTRATO` = teste falha.
"""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path
from unittest import mock

RAIZ = Path(__file__).resolve().parents[1]
EXEMPLOS = RAIZ / "docs" / "contrato" / "exemplos"
ARQUIVOS = ("pacote_tainacan.json", "erros.json", "propostas.json", "decisoes.json", "relatorio.txt",
            "orientacao.txt")


def gerar(destino: Path) -> dict[str, Path]:
    from nucleo import entrada, fundos, projeto, vigia
    from nucleo.config import CONTRATO
    from nucleo.painel import Painel
    from tests.test_entrada import Base, conferir_falso, exif_falso
    from tests.test_nucleo import ClienteFalso, resposta_padrao

    class Caso(Base):
        def runTest(self):
            pass

    destino.mkdir(parents=True, exist_ok=True)
    caso = Caso()
    caso.setUp()
    try:
        pasta = caso.scan("F026 - SBU Sami Bussab", "P0001 - Edificio Taruma 1972", n=2, formatos=("jpg",))
        (pasta / "info_projeto.json").write_text(json.dumps({
            "fundo": "F026", "nome": "Edifício Tarumã", "ano": "1972", "estacao": "scanner-a3",
            "tipo_estacao": "windows", "operador": "Beatriz", "operador_email": "beatriz@camp.arq.br"}),
            encoding="utf-8")
        v = vigia.Vigia(caso.config, ClienteFalso([resposta_padrao(
            arquiteto={"valor": "Sami Bussab", "confianca": 0.9},
            codigo_unidade={"valor": "ABC-1", "confianca": 0.8})]), caso.estado, painel=caso.painel)
        with mock.patch.object(entrada.mod_metadados, "gravar_em_lote", side_effect=exif_falso), \
                mock.patch.object(entrada, "conferir_exif", side_effect=conferir_falso), \
                mock.patch.object(entrada.mod_fundos, "carregar", return_value=fundos.Tabela(fundos.EMBUTIDA)):
            v.uma_rodada()
        proj = next((caso.acervo / "F026 - SBU Sami Bussab" / "01 - Projetos").iterdir())
        cat = proj / "catalogacao"
        doc = json.loads((cat / "pacote_tainacan.json").read_text())
        primeiro = (doc["documentos"] + doc["retirados"])[0]["codigo"]
        projeto.registrar_decisao(cat, primeiro, "nota", "exemplo de decisão humana", "Rafa")
        if not (cat / "propostas.json").exists():
            (cat / "propostas.json").write_text(json.dumps(doc.get("propostas") or {}, ensure_ascii=False, indent=1),
                                                encoding="utf-8")
        for nome in ARQUIVOS:
            shutil.copy2(cat / nome, destino / nome)
        for nome in ("status.json", "info_projeto.json"):
            shutil.copy2(proj / nome, destino / nome)
        # Corpos HTTP de verdade, capturados no cliente do painel.
        v._talvez_heartbeat(forcar=True)
        corpos = {"heartbeat": caso.painel.heartbeats[-1]}
        capturados: list = []
        p = Painel("http://painel", "tok", caso.estado)
        with mock.patch.object(p, "_chamar", side_effect=lambda m, c, corpo=None: capturados.append((m, c, corpo))
                               or (200, {"codigo": "F026-P0001"})):
            p.reservar("F026", "Edifício Tarumã", "0" * 32, ano="1972", cidade="São Paulo",
                       identificacao_original="P0001 - Edifício Tarumã", operador="Beatriz",
                       proximo_p_local="F026-P0002")
            p.aviso("F026-P0001", str(proj.relative_to(caso.acervo)), "pronto")
            p.releitura_iniciada(7, "campvision2")
            p.releitura_concluida(7, {"projeto_codigo": "F026-P0001", "escopo": "folha", "relidos": 1,
                                      "com_mudanca": 1, "campos_preenchidos": 2, "falhas": [], "custo_usd": 0.01,
                                      "regravados": 1, "pasta": "F026 - SBU Sami Bussab/…/releituras/…"},
                                  ok=True, mensagem="1 folha relida")
        for metodo, caminho, corpo in capturados:
            nome = caminho.split("?")[0].rstrip("/").split("/")[-1]
            corpos[nome] = {"metodo": metodo, "caminho": caminho.split("?")[0], "corpo": corpo}
        (destino / "http.json").write_text(json.dumps(corpos, ensure_ascii=False, indent=1, default=str),
                                           encoding="utf-8")
        (destino / "VERSAO").write_text(CONTRATO + "\n", encoding="utf-8")
    finally:
        caso.tearDown()
    return {p.name: p for p in destino.iterdir()}


def estrutura(valor):
    """Chaves e tipos, sem os valores (o que o contrato promete)."""
    if isinstance(valor, dict):
        return {k: estrutura(v) for k, v in sorted(valor.items())}
    if isinstance(valor, list):
        return [estrutura(valor[0])] if valor else []
    if valor is None:
        return "null"
    return type(valor).__name__


if __name__ == "__main__":
    if EXEMPLOS.exists():
        shutil.rmtree(EXEMPLOS)
    gerados = gerar(EXEMPLOS)
    print(f"{len(gerados)} exemplo(s) em {EXEMPLOS.relative_to(RAIZ)}")
    sys.exit(0)
