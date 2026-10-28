"""28/10: fotos enviadas pelo painel (D90001+) passam a ser gravadas pelo painel na pasta do projeto no QNAP. A faixa é do painel:
o CAMP Vision não numera a partir dela; mas lê e legenda essas folhas quando o painel pede (releitura da folha)."""

from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from nucleo import estrutura, releitura


class TestFaixaDoPainel(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.pj = Path(self.tmp.name) / "F008-P0001 - Residência Max Define"
        for cod, fmt in (("F008-P0001-1976-S01-D00003", "JPG"), ("F008-P0001-1976-S03-D90001", "JPG"), ("F008-P0001-1976-S03-D90002", "TIF")):
            serie = cod.split("-")[3]
            d = self.pj / estrutura.SERIES[serie] / fmt
            d.mkdir(parents=True, exist_ok=True)
            (d / f"{cod}.{fmt.lower()}").write_bytes(b"x")

    def tearDown(self):
        self.tmp.cleanup()

    def test_proximo_d_ignora_a_faixa_do_painel(self):
        self.assertEqual(estrutura.proximo_documento(self.pj), 4)

    def test_e_do_painel(self):
        self.assertTrue(estrutura.e_do_painel("F008-P0001-1976-S03-D90001"))
        self.assertFalse(estrutura.e_do_painel("F008-P0001-1976-S01-D00003"))
        self.assertFalse(estrutura.e_do_painel("qualquer"))

    def test_releitura_le_as_fotos_do_painel_quando_pedido(self):
        self.assertEqual(releitura.alvos(self.pj, "folha", ["F008-P0001-1976-S03-D90001"]), ["F008-P0001-1976-S03-D90001"])

if __name__ == "__main__":
    unittest.main()


class TestRelerFotoDoPainel(unittest.TestCase):
    """A foto que o painel gravou na pasta do projeto (D90001, só JPG) é lida pela releitura de FOLHA e entra no pacote do projeto."""

    def test_releitura_da_folha_do_painel_entra_no_pacote(self):
        import json
        from unittest import mock
        from nucleo import entrada, fundos
        from nucleo.livro import Livro
        from PIL import Image
        from tests.test_entrada import Base, PainelFalso, conferir_falso, exif_falso
        from tests.test_nucleo import ClienteFalso, resposta_padrao, campo

        class B(Base):
            def runTest(self):
                pass
        b = B(); b.setUp()
        try:
            b.scan("F026", "01 - Desenhos e pranchas", "F026-P0001 - Casa Nova", n=2, formatos=("jpg",))
            livro = Livro(b.acervo, b.estado / "reserva")
            r = entrada.Recebedor(b.config, ClienteFalso([resposta_padrao(arquiteto=campo("Sami Bussab"))]), PainelFalso(), livro, b.estado,
                                  fundos.Tabela(fundos.EMBUTIDA))
            with mock.patch.object(entrada.mod_metadados, "gravar_em_lote", side_effect=exif_falso), \
                    mock.patch.object(entrada, "conferir_exif", side_effect=conferir_falso):
                [res] = [r.processar(u) for u in r.pendentes()]
            pasta = res.pasta_projeto
            cod = f"{estrutura.codigo_da_pasta(pasta)}-0000-S03-D90001"
            ano = json.loads((pasta / "catalogacao" / "pacote_tainacan.json").read_text())["documentos"][0]["codigo"].split("-")[2]
            cod = cod.replace("-0000-", f"-{ano}-")
            destino = estrutura.destino_documento(pasta, "S03", cod, ".jpg")
            destino.parent.mkdir(parents=True, exist_ok=True)
            Image.new("RGB", (900, 600), (200, 190, 180)).save(destino)
            resumo = releitura.reler(b.acervo, b.estado, b.config,
                                     ClienteFalso([resposta_padrao(titulo_prancha=campo("Fachada principal"), arquiteto=campo("Sami Bussab"))]),
                                     estrutura.codigo_da_pasta(pasta), "folha", [cod], "foto enviada pelo painel", "painel", livro)
            self.assertEqual(resumo["relidos"], 1, resumo)
            pacote = json.loads((pasta / "catalogacao" / "pacote_tainacan.json").read_text())
            docs = {d["codigo"]: d for d in pacote["documentos"] + pacote.get("retirados", [])}
            self.assertIn(cod, docs)
            self.assertEqual(estrutura.proximo_documento(pasta), 3)      # a foto do painel não empurra a numeração do CAMP Vision
        finally:
            b.tearDown()


class TestPastaSoComFotosDoPainel(unittest.TestCase):
    """28/10 (log: "Aviso de F008-P0001 aceito mas com ok:false — sem info_projeto.json nem status.json nesta pasta"): a pasta que só tem
    fotos enviadas pelo painel não ganha status.json do CAMP Vision e a releitura não manda aviso dela ao painel."""

    def test_acertar_status_ignora_e_vigia_nao_avisa(self):
        import threading, types
        from unittest import mock
        from nucleo import entrada, fundos, vigia
        from nucleo.livro import Livro
        from tests.test_entrada import Base, PainelFalso
        from tests.test_nucleo import ClienteFalso

        class B(Base):
            def runTest(self):
                pass
        b = B(); b.setUp()
        try:
            pj = b.acervo / "F026 - SBU Sami Bussab" / "01 - Projetos" / "F026-P0040 - Só fotos"
            d = pj / estrutura.SERIES["S03"] / "JPG"; d.mkdir(parents=True)
            (d / "F026-P0040-0000-S03-D90001.jpg").write_bytes(b"x")
            painel = PainelFalso()
            r = entrada.Recebedor(b.config, ClienteFalso([]), painel, Livro(b.acervo, b.estado / "reserva"), b.estado, fundos.Tabela(fundos.EMBUTIDA))
            acerto = r.acertar_status(pj)
            self.assertFalse(acerto.get("mudou"), acerto)
            self.assertFalse((pj / "status.json").exists())
            eu = types.SimpleNamespace(config=types.SimpleNamespace(raiz_final=str(b.acervo)), _recebedor=lambda: r)
            res = vigia.Vigia._acertar_status_da_releitura(eu, "F026-P0040")
            self.assertNotIn("pasta", res)                 # sem pasta no acerto = sem aviso ao painel
            self.assertEqual(painel.avisos, [])
        finally:
            b.tearDown()
