"""Fotografias: série dita pela estação, foto que a triagem achou papel, orientação pela leitura."""

from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from PIL import Image

from nucleo import derivados, entrada, fundos, visao
from nucleo.config import Config
from nucleo.livro import Livro
from tests.test_entrada import Base, conferir_falso, exif_falso
from tests.test_nucleo import ClienteFalso

FOTO = {"tipo_de_imagem": "fotografia", "assunto": "fachada do ginásio", "enquadramento": "externa",
        "legenda_proposta": "Fachada do ginásio com letreiro SESI", "confianca": 0.9,
        "rotacao": 180, "orientacao_clara": True}


def foto_clara(caminho: Path, largura=1200, altura=800) -> Path:
    """Foto de arquitetura clara: muito céu/parede branca — a triagem acha que é papel."""
    img = Image.new("RGB", (largura, altura), (235, 235, 235))
    for x in range(0, largura, 40):
        img.paste((90, 90, 90), (x, altura // 2, x + 20, altura))
    caminho.parent.mkdir(parents=True, exist_ok=True)
    img.save(caminho, quality=90)
    return caminho


class TestSerieDaEstacao(Base):
    def mac(self, series=None) -> Path:
        projeto = self.scanners / "F026 - SBU Sami Bussab" / "P0060 - Edifício Tarumã - 1972"
        foto_clara(projeto / "03 - Fotografias" / "01 - Arquivo Arquivístico (TIFF)" / "scan 001.jpg")
        info = {"estacao": "mac-vuescan", "codigo_fundo": "F026", "numero_projeto": "P0060",
                "projeto": "Edifício Tarumã", "projeto_ja_existia": True}
        if series is not None:
            info["series"] = series
        (projeto / "info_lote_20261014-161453_mac.json").write_text(json.dumps(info), encoding="utf-8")
        return projeto

    def contexto(self):
        [unidade] = entrada.descobrir(self.scanners)
        return entrada.contexto(unidade, fundos.Tabela(fundos.EMBUTIDA))

    def test_series_do_info_lote(self):
        self.mac(series=["03 - Fotografias"])
        c = self.contexto()
        self.assertEqual((c.serie, c.serie_deduzida), ("S03", False))

    def test_pasta_de_material_dentro_do_lote(self):
        self.mac()
        c = self.contexto()
        self.assertEqual((c.serie, c.serie_deduzida), ("S03", False))


class TestLeitorVeFoto(unittest.TestCase):
    def test_prancha_que_e_foto_e_relida_como_foto(self):
        with TemporaryDirectory() as tmp:
            caminho = foto_clara(Path(tmp) / "F026-P0001-1972-S01-D00001.jpg")
            cliente = ClienteFalso([{"legivel": True, "tem_carimbo": False, "e_fotografia": True,
                                     "transcricao_integral": ""}, FOTO])
            l = visao.LeitorDeCarimbo(Config(), cliente).ler(caminho)
        self.assertEqual([c["ferramenta"] for c in cliente.chamadas],
                         ["registrar_prancha", "registrar_fotografia"])
        self.assertEqual(l.modo, "fotografia")
        self.assertEqual(l.valores["titulo_prancha"], "Fachada do ginásio com letreiro SESI")
        self.assertEqual((l.foto["rotacao"], l.foto["orientacao_clara"]), (180, True))
        self.assertEqual(l.tokens_entrada, 2000)

    def test_prancha_de_verdade_fica_uma_chamada(self):
        with TemporaryDirectory() as tmp:
            caminho = foto_clara(Path(tmp) / "F026-P0001-1972-S01-D00001.jpg")
            cliente = ClienteFalso([{"legivel": True, "tem_carimbo": True, "e_fotografia": False,
                                     "transcricao_integral": "CASA"}])
            l = visao.LeitorDeCarimbo(Config(), cliente).ler(caminho)
        self.assertEqual(len(cliente.chamadas), 1)
        self.assertEqual(l.modo, "prancha")


class TestOrientarPelaFoto(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.jpg = foto_clara(Path(self.tmp.name) / "JPG" / "F026-P0001-1972-S03-D00001.jpg", 300, 200)
        self.leitura = foto_clara(Path(self.tmp.name) / "leitura" / "F026-P0001-1972-S03-D00001.jpg", 300, 200)

    def tearDown(self):
        self.tmp.cleanup()

    def test_incerta_e_clara_gira_jpg_e_leitura(self):
        prep = {"rotacao": 0, "incerta": True}
        foto = dict(FOTO, rotacao=90)
        self.assertTrue(derivados.orientar_pela_foto(prep, foto, [self.jpg], [self.leitura]))
        self.assertEqual((prep["rotacao"], prep["incerta"], prep["jpg_orientado"]), (90, False, True))
        with Image.open(self.jpg) as a, Image.open(self.leitura) as b:
            self.assertEqual((a.size, b.size), ((200, 300), (200, 300)))

    def test_soma_ao_giro_que_o_preparo_ja_aplicou(self):
        prep = {"rotacao": 180, "incerta": True}  # chute do OCR já aplicado na imagem de leitura
        derivados.orientar_pela_foto(prep, dict(FOTO, rotacao=180), [self.jpg], [self.leitura])
        self.assertEqual(prep["rotacao"], 0)
        self.assertNotIn("jpg_orientado", prep)  # volta ao original: o JPG do acervo não muda

    def test_modelo_confirma_o_chute_do_ocr_e_o_jpg_recebe_o_giro(self):
        prep = {"rotacao": 90, "incerta": True}  # preview/leitura já giradas 90 pelo chute
        self.assertTrue(derivados.orientar_pela_foto(prep, dict(FOTO, rotacao=0), [self.jpg], [self.leitura]))
        self.assertEqual((prep["rotacao"], prep["incerta"], prep["jpg_orientado"]), (90, False, True))
        with Image.open(self.jpg) as a, Image.open(self.leitura) as b:
            self.assertEqual((a.size, b.size), ((200, 300), (300, 200)))  # leitura não gira de novo

    def test_em_duvida_nao_mexe(self):
        prep = {"rotacao": 0, "incerta": True}
        self.assertFalse(derivados.orientar_pela_foto(prep, dict(FOTO, orientacao_clara=False),
                                                      [self.jpg], [self.leitura]))
        self.assertTrue(prep["incerta"])

    def test_ocr_certo_vale_mais(self):
        prep = {"rotacao": 90, "incerta": False}
        self.assertFalse(derivados.orientar_pela_foto(prep, FOTO, [self.jpg], [self.leitura]))
        self.assertEqual(prep["rotacao"], 90)


class TestLoteDeFotoSemSerie(Base):
    def test_foto_clara_vira_s03_descrita_e_em_pe(self):
        pasta = self.scanners / "F026" / "Tarumã"
        foto_clara(pasta / "scan 001.jpg")
        cliente = ClienteFalso([{"legivel": True, "tem_carimbo": False, "e_fotografia": True,
                                 "transcricao_integral": ""}, FOTO])
        livro = Livro(self.acervo, self.estado / "reserva")
        r = entrada.Recebedor(self.config, cliente, self.painel, livro, self.estado,
                              fundos.Tabela(fundos.EMBUTIDA))
        with mock.patch.object(entrada.mod_metadados, "gravar_em_lote", side_effect=exif_falso), \
                mock.patch.object(entrada, "conferir_exif", side_effect=conferir_falso), \
                mock.patch.object(entrada.mod_triagem, "triar",
                                  return_value=entrada.mod_triagem.Triagem("S01", False, "papel com desenho")):
            res = r.processar(r.pendentes()[0])
        self.assertEqual(res.status, entrada.PRONTO, res.motivo)
        jpgs = [p for p in res.pasta_projeto.rglob("*.jpg") if "catalogacao" not in p.parts]
        self.assertEqual(len(jpgs), 1)
        self.assertIn("-S03-", jpgs[0].name)
        with Image.open(jpgs[0]) as img:  # 180°: a listra escura de baixo à esquerda subiu à direita
            self.assertLess(img.getpixel((img.width - 10, 10))[0], 150)
            self.assertGreater(img.getpixel((img.width - 10, img.height - 10))[0], 200)
        leituras = json.loads((res.pasta_projeto / "catalogacao" / "leituras.json").read_text())
        self.assertEqual(leituras[0]["valores"]["titulo_prancha"], "Fachada do ginásio com letreiro SESI")
        preparo = json.loads((res.pasta_projeto / "catalogacao" / "preparo.json").read_text())
        self.assertFalse(next(iter(preparo.values()))["incerta"])


if __name__ == "__main__":
    unittest.main()


class TestAvisarPainel(unittest.TestCase):
    def test_gira_o_pendente_e_avisa(self):
        import vigia as mod_vigia_cli

        with TemporaryDirectory() as tmp:
            raiz = Path(tmp)
            pasta = raiz / "F026 - SBU Sami Bussab" / "01 - Projetos" / "F026-P0060 - Tarumã"
            jpg = foto_clara(pasta / "03 - Fotografias" / "JPG" / "F026-P0060-1972-S03-D00001.jpg", 300, 200)
            (pasta / "catalogacao").mkdir(parents=True)
            (pasta / "catalogacao" / "preparo.json").write_text(json.dumps(
                {jpg.stem: {"rotacao": 90, "incerta": False}}), encoding="utf-8")
            (pasta / "status.json").write_text(json.dumps({"status": "pronto"}), encoding="utf-8")
            config = Config(pasta_acervo_final=str(raiz), painel_url="http://painel")
            with mock.patch("nucleo.painel.Painel.aviso", return_value=True) as aviso:
                msg = mod_vigia_cli._avisar_painel(config, "f026-p0060")
            aviso.assert_called_once_with("F026-P0060", str(pasta.relative_to(raiz)), "pronto")
            self.assertIn("1 JPG girado", msg)
            with Image.open(jpg) as img:
                self.assertEqual(img.size, (200, 300))
