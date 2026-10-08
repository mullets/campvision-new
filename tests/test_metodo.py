"""Método de leitura: derivação (§5), preparo (§2) e back-test (§9)."""

from __future__ import annotations

import shutil
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from PIL import Image, ImageDraw, ImageFont, ImageOps

from nucleo import backtest, derivacao, preparo
from nucleo.esquema import Leitura
from nucleo.planilha import escrever_json


class TestTipoDeDesenho(unittest.TestCase):
    CASOS = {
        "Planta de Situação": "Implantação",          # supressão
        "PLANTA DO PAVIMENTO TÉRREO E CORTE AA": "Planta / Corte",
        "Corte AA, Elevação Norte, Detalhe 1, Fachada": "Corte / Elevação / Fachada",  # limite 3
        "Prancha 12": "",                              # abstenção
        "Esquadrias de Ferro": "",                     # nomeia assunto, não tipo
        "Luncheonete": "",
        "Perspectiva interna": "Perspectiva",
        "Memorial descritivo": "Documento",
    }

    def test_casos_do_metodo(self):
        for titulo, esperado in self.CASOS.items():
            self.assertEqual(derivacao.tipo_de_desenho(titulo), esperado, titulo)

    def test_limite_de_palavra_bugs_do_5_4(self):
        # substring dentro de outra palavra não pode disparar
        self.assertEqual(derivacao.tipo_de_desenho("Abastecimento"), "")
        self.assertEqual(derivacao.tipo_de_desenho("Gasolina"), "")
        self.assertEqual(derivacao.tipo_de_desenho("Fotógrafo Ana"), "")

    def test_documento_nao_e_obra(self):
        self.assertTrue(derivacao.e_documento("Documentos do escritório"))
        self.assertFalse(derivacao.e_documento("Edifício de Escritórios"))

    def test_ano_so_da_data_escrita(self):
        self.assertEqual(derivacao.ano_da_data("14.10.83"), "1983")
        self.assertEqual(derivacao.ano_da_data("abril de 1972"), "1972")
        self.assertEqual(derivacao.ano_da_data("83"), "", "dois dígitos soltos não viram ano")


class TestAutoria(unittest.TestCase):
    def test_titular_e_intrusa(self):
        self.assertFalse(derivacao.autoria_divergente("ARQ. S. BUSSAB", ["Sami Bussab"]))
        self.assertTrue(derivacao.autoria_divergente("SALVADOR CANDIA", ["Joaquim Barretto"]))
        self.assertFalse(derivacao.autoria_divergente("", ["Joaquim Barretto"]))
        self.assertFalse(derivacao.autoria_divergente("J. C. BELLUCCI", ["José Carlos Bellucci"]))


def _folha_com_texto() -> Image.Image:
    fontes = sorted(Path("/usr/share/fonts").rglob("*.ttf"))
    fonte = ImageFont.truetype(str(fontes[0]), 42) if fontes else None
    img = Image.new("RGB", (2000, 1400), "white")
    d = ImageDraw.Draw(img)
    for i, t in enumerate(["PROJETO: RESIDENCIA DO ARQUITETO", "PLANTA DO PAVIMENTO TERREO",
                           "ESCALA 1:50   FOLHA 03/12", "DATA 14.10.83 RUA AUGUSTA SAO PAULO SP"]):
        d.text((900, 950 + i * 80), t, fill="black", font=fonte)
    return img


TEM_FONTE = bool(sorted(Path("/usr/share/fonts").rglob("*.ttf"))) if Path("/usr/share/fonts").exists() else False


@unittest.skipUnless(shutil.which("tesseract") and TEM_FONTE, "tesseract ou fonte TTF ausente")
class TestOrientacao(unittest.TestCase):
    def test_rotacoes(self):
        original = _folha_com_texto()
        for graus in (0, 90, 270):
            digitalizada = original.rotate(graus, expand=True) if graus else original
            o = preparo.orientar(digitalizada)
            self.assertEqual((o.rotacao, o.espelhada), (graus, False), graus)
            corrigida = preparo.transformar(digitalizada, o)
            self.assertLessEqual(preparo.distancia(preparo.dhash(corrigida), preparo.dhash(original)), 6)

    def test_espelhada_nunca_vira_errada_em_silencio(self):
        # O OCR varia entre versões do tesseract (4.1 no Ubuntu 22.04, 5.x aqui):
        # o que não pode acontecer é decidir errado SEM marcar incerta.
        original = _folha_com_texto()
        digitalizada = ImageOps.mirror(original.rotate(90, expand=True))
        o = preparo.orientar(digitalizada)
        self.assertTrue((o.rotacao, o.espelhada) == (90, True) or o.incerta, o)

    def test_folha_em_branco_e_incerta(self):
        self.assertTrue(preparo.orientar(Image.new("RGB", (1000, 700), "white")).incerta)


@unittest.skipUnless(TEM_FONTE, "fonte TTF ausente")
class TestHashes(unittest.TestCase):
    def test_perceptual_pega_mesma_folha_redigitalizada(self):
        a = _folha_com_texto()
        b = a.resize((1900, 1330)).resize((2000, 1400))  # outra digitalização, bytes diferentes
        c = Image.new("RGB", (2000, 1400), "black")
        self.assertLessEqual(preparo.distancia(preparo.dhash(a), preparo.dhash(b)), preparo.DISTANCIA_PERCEPTUAL)
        self.assertGreater(preparo.distancia(preparo.dhash(a), preparo.dhash(c)), preparo.DISTANCIA_PERCEPTUAL)


class TestBacktest(unittest.TestCase):
    def test_tres_baldes_e_liberacao(self):
        with TemporaryDirectory() as tmp:
            raiz = Path(tmp)
            leituras = [
                Leitura(arquivo=f"F023-P0001-1968-S01-D{i:05d}.jpg",
                        valores={"arquiteto": "OSWALDO CORREA GONCALVES", "tipo": "Planta",
                                 "titulo_prancha": "Planta baixa"}) for i in range(1, 10)
            ]
            leituras.append(Leitura(arquivo="F023-P0001-1968-S01-D00010.jpg",
                                    valores={"arquiteto": "SALVADOR CANDIA", "tipo": "Corte",
                                             "titulo_prancha": "Corte AA"}))
            (raiz / "p" / "catalogacao").mkdir(parents=True)
            escrever_json(leituras, raiz / "p" / "catalogacao" / "leituras.json")
            curado = raiz / "curado.csv"
            linhas = ["codigo,Arquiteto,Tipo"]
            linhas += [f"F023-P0001-1968-S01-D{i:05d},Oswaldo Corrêa Gonçalves,Planta / Corte" for i in range(1, 11)]
            curado.write_text("\n".join(linhas), encoding="utf-8")
            r = {x.campo: x for x in backtest.backtest(backtest.carregar_curado(curado),
                                                       backtest.carregar_leituras(raiz))}
            self.assertEqual(r["arquiteto"].exato, 9)
            self.assertEqual(len(r["arquiteto"].categoria), 1)
            self.assertTrue(r["arquiteto"].liberado)
            self.assertEqual(r["tipo"].granularidade, 10)
            self.assertFalse(r["tipo"].liberado)
            self.assertIn("SALVADOR CANDIA", backtest.relatorio(list(r.values())))
            self.assertEqual(backtest.reconferir_tipo(backtest.carregar_leituras(raiz)), [])


if __name__ == "__main__":
    unittest.main()
