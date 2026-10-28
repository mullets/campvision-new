"""28/10 (Rafael, F008-P0001 "Residência Max Define" virou a pasta "CONSTRUÇÃO DE UMA RESIDENCIA"): o nome do projeto e da pasta é o
que a pessoa digitou na estação (ou o título oficial do painel, com o código vindo da estação), nunca o título lido no carimbo.
O carimbo só dá nome quando a pasta mistura várias obras. Nenhum teste toca a rede."""

from __future__ import annotations

import json

from unittest import mock

from nucleo import entrada, estrutura, fundos
from nucleo.livro import Livro
from tests.test_entrada import Base, conferir_falso, exif_falso
from tests.test_identidade import BASE, PainelComBase, respostas
from tests.test_nucleo import ClienteFalso

CARIMBO = {"projeto": "CONSTRUÇÃO DE UMA RESIDENCIA", "endereco": "R. Qualquer 10"}


class TestNomeDaEstacao(Base):
    def rodar_com(self, cliente, painel):
        self.painel = painel
        livro = Livro(self.acervo, self.estado / "reserva")
        r = entrada.Recebedor(self.config, cliente, painel, livro, self.estado, fundos.Tabela(fundos.EMBUTIDA))
        with mock.patch.object(entrada.mod_metadados, "gravar_em_lote", side_effect=exif_falso), \
                mock.patch.object(entrada, "conferir_exif", side_effect=conferir_falso):
            return [r.processar(u) for u in r.pendentes()]

    def pasta_final(self, codigo):
        base = self.acervo / "F026 - SBU Sami Bussab" / "01 - Projetos"
        return next(p for p in base.iterdir() if p.is_dir() and p.name.startswith(codigo))

    def test_codigo_da_estacao_usa_o_titulo_do_painel_e_nao_o_carimbo(self):
        base = BASE + [{"codigo": "F026-P0120", "projeto": "Residência Max Define"}]
        self.scan("F026", "01 - Desenhos e pranchas", "F026-P0120 - Residência Max Define", n=2, formatos=("jpg",))
        [r] = self.rodar_com(ClienteFalso(respostas(CARIMBO, CARIMBO)), PainelComBase(base))
        self.assertEqual(r.status, entrada.PRONTO, r.motivo)
        pasta = self.pasta_final("F026-P0120")
        self.assertEqual(estrutura.nome_sem_codigo(pasta.name), "Residência Max Define")
        info = json.loads((pasta / "info_projeto.json").read_text(encoding="utf-8"))
        self.assertEqual(info["nome"], "Residência Max Define")
        self.assertNotIn("CONSTRU", pasta.name.upper())

    def test_projeto_novo_reserva_com_o_nome_digitado_na_estacao(self):
        self.scan("F026", "01 - Desenhos e pranchas", "Residência Max Define", n=2, formatos=("jpg",))
        painel = PainelComBase(BASE)
        [r] = self.rodar_com(ClienteFalso(respostas(CARIMBO, CARIMBO)), painel)
        self.assertEqual(r.status, entrada.PRONTO, r.motivo)
        self.assertEqual([p["titulo"] for p in painel.pedidos], ["Residência Max Define"])
        [codigo] = set(painel.reservas.values())
        pasta = self.pasta_final(codigo)
        self.assertEqual(estrutura.nome_sem_codigo(pasta.name), "Residência Max Define")
        self.assertEqual(json.loads((pasta / "info_projeto.json").read_text(encoding="utf-8"))["nome"], "Residência Max Define")

