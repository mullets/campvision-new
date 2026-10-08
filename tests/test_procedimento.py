"""Procedimento padrão de catalogação: triagem, chaves de identidade, período,
leitura textual, relatório em 8 partes, log de orientação, ERRO.txt e espaço."""

from __future__ import annotations

import json
import random
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from PIL import Image

from nucleo import derivacao, entrada, fundos, triagem, visao
from nucleo.esquema import Leitura
from tests.test_entrada import Base, conferir_falso, exif_falso


def foto_falsa(caminho: Path, tamanho=(900, 650)) -> Path:
    rnd = random.Random(7)
    from PIL import ImageDraw

    img = Image.new("RGB", tamanho, (60, 70, 50))
    d = ImageDraw.Draw(img)
    for _ in range(80):  # manchas grandes de tom: céu, fachada, sombra
        x, y = rnd.randint(0, tamanho[0]), rnd.randint(0, tamanho[1])
        d.rectangle([x, y, x + rnd.randint(40, 300), y + rnd.randint(40, 200)],
                    fill=(rnd.randint(0, 190), rnd.randint(0, 170), rnd.randint(0, 150)))
    caminho.parent.mkdir(parents=True, exist_ok=True)
    img.save(caminho)
    return caminho


class TestTriagem(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.base = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_papel_deitado_e_prancha(self):
        p = self.base / "a.jpg"
        Image.new("RGB", (1400, 1000), "white").save(p)
        self.assertEqual(triagem.triar(p, {"0": 30.0}).serie, "S01")

    def test_foto_e_s03(self):
        t = triagem.triar(foto_falsa(self.base / "f.jpg"), {"0": 0.0})
        self.assertEqual((t.serie, t.incerta), ("S03", False))

    def test_a4_em_pe_com_texto_e_s02(self):
        p = self.base / "c.jpg"
        Image.new("RGB", (1000, 1414), "white").save(p)
        self.assertEqual(triagem.triar(p, {"0": 120.0}).serie, "S02")
        meio = triagem.triar(p, {"0": 30.0})
        self.assertTrue(meio.incerta)


class TestChavesEPeriodo(unittest.TestCase):
    CHAVES = (("Francisco Segnini Jr.", "barba, bigode e óculos"), ("Joaquim Barretto", "só bigode"))

    def test_sinal_vira_tracos(self):
        self.assertEqual(derivacao.tracos_do_sinal("só bigode"),
                         {"bigode": True, "barba": False, "oculos": False})

    def test_identifica_os_dois(self):
        pessoas = [{"barba": True, "bigode": True, "oculos": True}, {"bigode": True}]
        self.assertEqual(derivacao.identificar_pessoas(pessoas, self.CHAVES),
                         ["Francisco Segnini Jr.", "Joaquim Barretto"])

    def test_pessoa_sem_chave_ninguem_e_nomeado(self):
        pessoas = [{"bigode": True}, {"barba": False, "bigode": False, "oculos": False}]
        self.assertEqual(derivacao.identificar_pessoas(pessoas, self.CHAVES), [])

    def test_parametros_do_fundo_em_json(self):
        with TemporaryDirectory() as t:
            raiz = Path(t)
            (raiz / "_campvision" / "fundos").mkdir(parents=True)
            (raiz / "_campvision" / "fundos" / "F002.json").write_text(json.dumps({
                "codigo": "F002", "periodo_atuacao": {"de": 1966, "ate": 1981},
                "chaves_de_identidade": [{"pessoa": p, "sinal": s} for p, s in self.CHAVES],
                "fundos_relacionados": [{"codigo": "F010", "nome": "Francisco Segnini Jr."}],
            }), encoding="utf-8")
            tabela = fundos.com_parametros(fundos.Tabela(fundos.EMBUTIDA), raiz)
        f = tabela.get("F002")
        self.assertEqual(f.periodo, (1966, 1981))
        self.assertTrue(f.fora_do_periodo("1983"))
        self.assertFalse(f.fora_do_periodo("1972"))
        self.assertIn("Francisco Segnini Jr.", f.autorizados)
        self.assertEqual(len(f.chaves_de_identidade), 2)


class TestLeituraTextual(unittest.TestCase):
    def test_s02_le_com_prompt_de_documento(self):
        self.assertEqual(visao.modo_do_arquivo("F002-P0002-1972-S02-D00010.jpg"), "textual")
        l = visao._para_textual({"tipo_documental": "carta", "remetente": "IAB/SP",
                                 "data": "22/11/1991", "ano": "1991", "assunto": "Premiação",
                                 "transcricao": "Prezados...", "legivel": True, "confianca": 0.9},
                                Leitura(arquivo="x.jpg"))
        self.assertEqual(l.modo, "textual")
        self.assertEqual(l.valores["ano"], "1991")
        self.assertIn("IAB/SP", l.valores["titulo_prancha"])
        self.assertEqual(l.transcricao_integral, "Prezados...")


class TestRecebimentoComProcedimento(Base):
    def _processar(self, r):
        with mock.patch.object(entrada.mod_metadados, "gravar_em_lote", side_effect=exif_falso), \
                mock.patch.object(entrada, "conferir_exif", side_effect=conferir_falso):
            return r.processar(r.pendentes()[0])

    def test_pasta_misturada_foto_vai_para_s03_e_relatorio_sai(self):
        pasta = self.scan("F026", "Clube Sirio", n=2, formatos=("jpg",))
        foto_falsa(pasta / "JPG" / "scan 003.jpg")
        res = self._processar(self.recebedor())
        self.assertEqual(res.status, entrada.PRONTO, res.motivo)
        fotos = list((res.pasta_projeto / "03 - Fotografias").glob("*-S03-D00003.jpg"))
        self.assertEqual(len(fotos), 1, list(res.pasta_projeto.rglob("*.jpg")))
        cat = res.pasta_projeto / "catalogacao"
        rel = (cat / "relatorio.txt").read_text()
        for n in range(1, 9):
            self.assertIn(f"\n{n}. ", "\n" + rel)
        self.assertIn("S03", rel)
        self.assertIn("sem giro", (cat / "orientacao.txt").read_text())
        pacote = json.loads((cat / "pacote_tainacan.json").read_text())
        self.assertTrue(all(i["arquivo_origem"] and i["arquivo_origem"][0]["arquivo_origem"].startswith("100 - Scanners")
                            for i in pacote["itens"]))
        self.assertEqual({i["origem_formato"] for i in pacote["itens"]}, {"jpg"})
        self.assertFalse((cat / "catalogacao_ERRO.txt").exists())

    def test_falha_grava_catalogacao_erro(self):
        self.scan("F026", "Tarumã", n=1, formatos=("jpg",))

        class Recusa:
            def chamar(self, *a, **k):
                raise RuntimeError("Error code: 401")

        r = self.recebedor()
        r.cliente = Recusa()
        self.config.max_tentativas_api = 1
        res = self._processar(r)
        self.assertEqual(res.status, entrada.ERRO)
        self.assertIn("401", (res.pasta_projeto / "catalogacao" / "catalogacao_ERRO.txt").read_text())

    def test_sem_espaco_nao_comeca(self):
        pasta = self.scan("F026", "Grande", n=1, formatos=("jpg",))
        r = self.recebedor()
        uso = mock.Mock(free=1024)
        with mock.patch.object(entrada.shutil, "disk_usage", return_value=uso):
            res = self._processar(r)
        self.assertEqual(res.status, entrada.ERRO)
        self.assertIn("espaço insuficiente", res.motivo)
        self.assertTrue((pasta / "JPG" / "scan 001.jpg").exists())


if __name__ == "__main__":
    unittest.main()
