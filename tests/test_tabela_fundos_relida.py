"""05/11 (F033 Renato Viégas): fundo cadastrado no painel com o CV2 rodando.

Antes: a tabela de fundos só era lida quando o serviço subia, e o lote que parou por "fundo 'F033' não está na tabela
de autoridade" nunca era tentado de novo (a pasta não muda). Os lotes ficavam parados mesmo depois de reiniciar.
Agora: a tabela é relida do painel e o lote parado só por isso volta sozinho para a fila. Nenhum teste toca a rede.
"""

from __future__ import annotations

import json
import time
import unittest
from unittest import mock

from nucleo import entrada, fundos
from nucleo.livro import Livro
from tests.test_entrada import Base, PainelFalso, conferir_falso, exif_falso
from tests.test_nucleo import ClienteFalso, resposta_padrao

F033 = {"codigo": "F033", "sigla": "RVG", "nome": "Renato Viégas"}


class PainelComTabela(PainelFalso):
    def __init__(self):
        super().__init__()
        self.fundos = [{"codigo": f.codigo, "sigla": f.sigla, "nome": f.nome} for f in fundos.EMBUTIDA]
        self.consultas = 0

    def contexto(self, fundo=""):
        self.consultas += 1
        return {"fundos": list(self.fundos)}


class TestTabelaRelida(Base):
    def setUp(self):
        super().setUp()
        self.painel = PainelComTabela()

    def lote_f033(self):
        pasta = self.scan("F033 - RVG - Renato Viégas", "P0030 - Loteamento e resort em Búzios - 1988", "01 - Desenhos e pranchas", n=1)
        (pasta / "info_projeto.json").write_text(json.dumps({"fundo_codigo": "F033", "nome": "Loteamento e resort em Búzios"}), encoding="utf-8")
        return pasta

    def rodada(self, r):
        with mock.patch.object(entrada.mod_metadados, "gravar_em_lote", side_effect=exif_falso), \
                mock.patch.object(entrada, "conferir_exif", side_effect=conferir_falso):
            return [r.processar(u) for u in r.pendentes()]

    def test_fundo_cadastrado_depois_destrava_o_lote_sem_reiniciar(self):
        self.assertIsNone(fundos.Tabela(fundos.EMBUTIDA).get("F033"), "o teste supõe que a tabela embutida não tem o F033")
        self.lote_f033()
        livro = Livro(self.acervo, self.estado / "reserva")
        r = entrada.Recebedor(self.config, ClienteFalso([resposta_padrao()] * 10), self.painel, livro, self.estado)
        primeira = self.rodada(r)
        self.assertEqual(len(primeira), 1)
        self.assertEqual(primeira[0].status, entrada.ERRO)
        self.assertIn("fundo 'F033' não está na tabela de autoridade", primeira[0].motivo)

        # sem fundo novo no painel: o lote continua parado (não fica rodando em vão)
        r._tabela_em = time.monotonic() - 3600
        self.assertEqual(self.rodada(r), [])

        # o Rafael cadastra o F033 no painel; o CV2 NÃO é reiniciado
        self.painel.fundos.append(F033)
        r._tabela_em = time.monotonic() - 3600
        segunda = self.rodada(r)
        self.assertEqual(len(segunda), 1, "o lote parado só pelo fundo tem de voltar sozinho para a fila")
        self.assertNotIn("tabela de autoridade", segunda[0].motivo or "")
        self.assertIsNotNone(r.tabela.get("F033"))

    def test_nao_pergunta_ao_painel_a_cada_volta(self):
        livro = Livro(self.acervo, self.estado / "reserva")
        r = entrada.Recebedor(self.config, ClienteFalso([resposta_padrao()]), self.painel, livro, self.estado)
        n0 = self.painel.consultas
        for _ in range(5):
            r.pendentes()
        self.assertEqual(self.painel.consultas, n0, "sem lote parado por fundo, relê no máximo a cada MINUTOS_TABELA")

    def test_painel_fora_do_ar_nao_troca_a_tabela_em_uso(self):
        livro = Livro(self.acervo, self.estado / "reserva")
        self.painel.fundos.append(F033)
        r = entrada.Recebedor(self.config, ClienteFalso([resposta_padrao()]), self.painel, livro, self.estado)
        self.assertIsNotNone(r.tabela.get("F033"))
        self.painel.contexto = lambda fundo="": None       # painel caiu
        r._tabela_em = time.monotonic() - 3600
        self.assertFalse(r.atualizar_tabela())
        self.assertIsNotNone(r.tabela.get("F033"), "cache/embutida nunca substituem a tabela que veio do painel")


if __name__ == "__main__":
    unittest.main()
