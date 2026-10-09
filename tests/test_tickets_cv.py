"""Tickets CV-08 a CV-27 — regras gerais, sem nada específico de um acervo."""

from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace

from nucleo import entrada, estrutura, fundos, grupos, hpgl, programa, projeto
from nucleo.esquema import Leitura


def leitura(codigo: str, **valores) -> Leitura:
    l = Leitura(arquivo=f"{codigo}.jpg")
    l.valores.update(valores)
    return l


class TestPrograma(unittest.TestCase):
    """CV-26 com os 9 bugs achados no back-test (CV-23) como regressão."""

    def casos(self):
        return {
            "Guarita da Santa Casa de Misericórdia": lambda s: s["programa"] != 61,
            "Edifício Quadra-3 Lote 5": lambda s: s["programa"] != 157,
            "Posto de abastecimento Paulo Lopes": lambda s: s["programa"] != 203,
            "Depósito Drogacenter": lambda s: s["programa"] == 206,
            "Residência Banco Cidade - Agência Matriz": lambda s: s["programa"] is None,
            "Residência Fazenda Boa Vista": lambda s: s["programa"] == 61,
            "Documentos do escritório": lambda s: s["programa"] is None and s["natureza"] is None,
            "Fábrica de cimento": lambda s: s["programa"] == 203,
            "Reforma da Residência X": lambda s: (s["programa"], s["natureza"]) == (61, 267),
            "Escola Estadual": lambda s: (s["programa"], s["natureza"]) == (125, 266),
        }

    def test_regras(self):
        for titulo, ok in self.casos().items():
            self.assertTrue(ok(programa.sugerir(titulo)), (titulo, programa.sugerir(titulo)))

    def test_backtest_libera_so_com_85(self):
        linhas = [{"titulo": "Escola X", "programa": "125", "natureza": "266"}] * 9 + \
                 [{"titulo": "Escola Y", "programa": "61", "natureza": "266"}]
        r = programa.backtest(linhas)
        self.assertTrue(r["programa"]["liberado"])
        self.assertEqual(len(r["programa"]["divergentes"]), 1)
        r = programa.backtest(linhas[:1] + linhas[-1:])
        self.assertFalse(r["programa"]["liberado"])


class TestHpgl(unittest.TestCase):
    @staticmethod
    def pe(valores):
        saida = ""
        for v in valores:
            n = (abs(v) << 1) | (1 if v < 0 else 0)
            while n >= 64:
                saida += chr(63 + (n & 63))
                n >>= 6
            saida += chr(191 + n)
        return saida

    def test_comandos_e_pe(self):
        with TemporaryDirectory() as t:
            arq = Path(t) / "a.plt"
            texto = "IN;SP1;PU0,0;PD1000,0,1000,1000,0,1000,0,0;PU;PE<=" + self.pe([0, 0]) + self.pe([500, 500, -500, 0]) + ";"
            arq.write_bytes(texto.encode("latin-1"))
            segs = hpgl.segmentos(arq.read_bytes().decode("latin-1"))
            self.assertEqual(len(segs), 4 + 2)
            self.assertEqual(segs[-1], (500, 500, 0, 500))
            img = hpgl.renderizar(arq, 400)
            self.assertIsNotNone(img)
            self.assertLess(img.convert("L").getextrema()[0], 50)


class TestGrupos(unittest.TestCase):
    def test_abreviacoes(self):
        self.assertEqual(grupos.normalizar("R. MAL. FLORIANO, 12"), grupos.normalizar("Rua Marechal Floriano 12"))

    def test_data_iso(self):
        self.assertEqual(grupos.data_iso("14.10.83"), "1983-10-14")
        self.assertEqual(grupos.data_iso("08/82"), "1982-08")
        self.assertEqual(grupos.data_iso("abr 1975"), "1975")

    def test_unidade_e_revisao_separam_obras(self):
        a = leitura("X-D1", codigo_unidade="ABC-1", projeto="Loja")
        b = leitura("X-D2", codigo_unidade="ABC-1/R-1", projeto="Loja")
        c = leitura("X-D3", codigo_unidade="ABC-2", projeto="Loja")
        g = grupos.agrupar([a, b, c])
        self.assertEqual(len(g), 3, g.keys())
        self.assertEqual(b.valores["revisao"], "R-1")

    def test_outlier_de_data(self):
        ls = [leitura(f"F000-P0001-0000-S01-D0000{i}", projeto="Obra", data="14.06.83", ano="1983") for i in range(4)]
        ls.append(leitura("F000-P0001-0000-S01-D00009", projeto="Obra", data="85", ano="1985"))
        grupos.consolidar(ls)
        fora = ls[-1]
        self.assertTrue(fora.data_outlier)
        self.assertEqual((fora.data_lida, fora.data_sugerida), ("85", "1983"))
        self.assertTrue(any("1983" in r for r in fora.ressalvas))
        self.assertEqual(ls[0].data_iso, "1983-06-14")


class TestProjeto(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.base = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_canonica_congelada_nao_e_sequestrada(self):
        boas = {f"D{i}": leitura(f"D{i}", arquiteto="OSWALDO CORRÊA GONÇALVES") for i in range(6)}
        projeto.congelar_canonicas(boas, "F000", self.base)
        lote2 = {"D9": leitura("D9", arquiteto="HOSWALDO CORREA GONÇALVES")}
        projeto.congelar_canonicas(lote2, "F000", self.base)
        self.assertEqual(lote2["D9"].valores["arquiteto"], "OSWALDO CORRÊA GONÇALVES")
        self.assertTrue(lote2["D9"].ressalvas)
        banco = json.loads((self.base / "canonicas" / "F000.json").read_text())
        grafias = {v["grafia"] for v in banco["arquiteto"].values()}
        self.assertNotIn("HOSWALDO CORREA GONÇALVES", grafias)

    def test_titulo_sem_nome_do_projeto(self):
        l = leitura("D1", titulo_prancha="DECORAÇÃO DA RESIDÊNCIA X - PLANTA DO FORRO", projeto="Residência X")
        t = projeto.titulo_publicacao(l, "Residência X")
        self.assertNotIn("RESIDÊNCIA X", t.upper().replace("RESIDENCIA", "RESIDÊNCIA"))
        self.assertTrue(t[0].isupper() and not t.isupper())

    def test_fontes_que_discordam_deixam_vazio(self):
        ctx = SimpleNamespace(ano="1971", projeto="Shopping")
        l = leitura("D1", projeto="Shopping")
        l.ano_do_projeto = "1975"
        canon, fontes, lacunas = projeto.fontes_e_lacunas(ctx, {"D1": l})
        self.assertEqual(canon["ano"], "")
        self.assertEqual(fontes["ano"], {"pasta": "1971", "carimbo": "1975"})
        self.assertTrue(any("ano" in x for x in lacunas))

    def test_melhor_versao_publica_a_maior(self):
        a, b = leitura("A"), leitura("B")
        b.duplicata_de, b.tipo_duplicata = "A", "quase"
        projeto.melhor_versao({"A": a, "B": b}, {"A": {"tamanho": 100}, "B": {"tamanho": 900}})
        self.assertEqual((a.duplicata_de, b.duplicata_de), ("B", ""))

    def test_propostas_de_obras(self):
        ls = {f"D{i}": leitura(f"D{i}", codigo_unidade=u) for i, u in enumerate(["ABC-1", "ABC-1", "ABC-2"])}
        p = projeto.propostas(ls, {})
        self.assertEqual(len(p["obras_na_pasta"]), 2)

    def test_decisao_registrada_e_retira_do_pacote(self):
        cat = self.base / "catalogacao"
        projeto.registrar_decisao(cat, "F000-P0001-1972-S03-D00020", "retirar", "não é desta obra", "Equipe")
        with self.assertRaises(ValueError):
            projeto.registrar_decisao(cat, "X", "apagar", "m", "p")
        fundo = fundos.EMBUTIDA[0]
        ctx = SimpleNamespace(fundo=fundo, serie="S03", ano="1972", projeto="Obra")
        ls = {c: leitura(c, titulo_prancha="Foto") for c in
              ("F000-P0001-1972-S03-D00020", "F000-P0001-1972-S03-D00021")}
        pac = projeto.pacote("F000-P0001", "Obra", ctx, ls, {}, {}, cat)
        self.assertEqual([d["codigo"] for d in pac["retirados"]], ["F000-P0001-1972-S03-D00020"])
        self.assertEqual(pac["retirados"][0]["decisao"]["por"], "Equipe")
        self.assertEqual(pac["documentos"][0]["fotografo"], "")  # modo prancha
        self.assertIn("capa_sugerida", pac["projeto"])


class TestRelatorioEContagem(unittest.TestCase):
    def test_falta_c1(self):
        ls = [leitura(f"D{i}", folha=f"C-{i}/12") for i in range(2, 13)]
        self.assertEqual(entrada.folhas_faltantes(ls), ["série C de 12: faltam C-1"])

    def test_numeros_p_nunca_reusa(self):
        with TemporaryDirectory() as t:
            raiz = Path(t)
            for nome in ("F000-P0001 - A", "F000-P0007 - B"):
                (raiz / "F000 - Teste" / estrutura.PASTA_PROJETOS / nome).mkdir(parents=True)
            maior, usados = estrutura.numeros_p(raiz, "F000")
            self.assertEqual((maior, usados), (7, {"F000-P0001", "F000-P0007"}))


if __name__ == "__main__":
    unittest.main()


class TestOrganizarFormatos(unittest.TestCase):
    def test_move_arquivos_soltos_para_subpasta_do_formato(self):
        from nucleo import renomear

        with TemporaryDirectory() as t:
            raiz = Path(t)
            serie = raiz / "F000 - X" / estrutura.PASTA_PROJETOS / "F000-P0001 - Obra" / "01 - Desenhos e pranchas"
            serie.mkdir(parents=True)
            for ext in ("tif", "jpg"):
                (serie / f"F000-P0001-1970-S01-D00001.{ext}").write_bytes(b"x")
            cat = serie.parent / "catalogacao"
            cat.mkdir()
            (cat / "mapa_origem.json").write_text(json.dumps(
                {"a": {"destino": "F000 - X/01 - Projetos/F000-P0001 - Obra/01 - Desenhos e pranchas/F000-P0001-1970-S01-D00001.tif"}}))
            self.assertEqual(renomear.organizar_formatos(raiz, raiz / "estado"), 2)
            self.assertTrue((serie / "TIF" / "F000-P0001-1970-S01-D00001.tif").exists())
            self.assertTrue((serie / "JPG" / "F000-P0001-1970-S01-D00001.jpg").exists())
            self.assertIn("/TIF/", (cat / "mapa_origem.json").read_text())
            self.assertEqual(renomear.organizar_formatos(raiz, raiz / "estado"), 0)  # idempotente


class TestReservaExistente(unittest.TestCase):
    """Card 83: 202 aguarda; existente=true entra no projeto que já existe (sem a trava CV-27)."""

    def test_existente_entra_e_colisao_comum_espera(self):
        from unittest import mock

        from tests.test_entrada import Base, conferir_falso, exif_falso

        class Caso(Base):
            def runTest(self):
                pass

        caso = Caso()
        caso.setUp()
        try:
            pasta_existente = (caso.acervo / "F026 - SBU Sami Bussab" / estrutura.PASTA_PROJETOS
                               / "F026-P0001 - Clube Sírio")
            pasta_existente.mkdir(parents=True)
            caso.scan("F026", "Clube Sirio Libanes", n=1, formatos=("jpg",))

            def reservar(fundo, titulo, chave, **extra):
                caso.painel.ultima_reserva = {"existente": modo["existente"]}
                return "F026-P0001"

            modo = {"existente": False}
            caso.painel.reservar = reservar
            r = caso.recebedor()
            with mock.patch.object(entrada.mod_metadados, "gravar_em_lote", side_effect=exif_falso), \
                    mock.patch.object(entrada, "conferir_exif", side_effect=conferir_falso):
                res = r.processar(r.pendentes()[0])
                self.assertEqual(res.status, entrada.AGUARDANDO)
                self.assertIn("já é outro projeto", res.motivo)
                modo["existente"] = True
                r = caso.recebedor()
                r.painel.reservar = reservar
                res = r.processar(r.pendentes()[0])
            self.assertEqual(res.status, entrada.PRONTO, res.motivo)
            self.assertEqual(res.pasta_projeto, pasta_existente)
        finally:
            caso.tearDown()


class TestReleitura(unittest.TestCase):
    """Pedido de releitura: relê AO LADO, nunca sobrescreve leituras.json."""

    def test_reler_grava_ao_lado_e_compara(self):
        from unittest import mock

        from nucleo import releitura
        from nucleo.livro import Livro
        from tests.test_entrada import Base, conferir_falso, exif_falso

        class Caso(Base):
            def runTest(self):
                pass

        caso = Caso()
        caso.setUp()
        try:
            caso.scan("F026", "Clube Sirio", n=2, formatos=("jpg",))
            r = caso.recebedor()
            with mock.patch.object(entrada.mod_metadados, "gravar_em_lote", side_effect=exif_falso), \
                    mock.patch.object(entrada, "conferir_exif", side_effect=conferir_falso):
                res = r.processar(r.pendentes()[0])
            self.assertEqual(res.status, entrada.PRONTO, res.motivo)
            principal = (res.pasta_projeto / "catalogacao" / "leituras.json").read_text()

            class LeitorNovo:
                def ler(self, caminho):
                    l = Leitura(arquivo=caminho.name)
                    l.valores.update({"projeto": "CLUBE SÍRIO", "cliente": "Clube Novo"})
                    l.carimbo_encontrado = True
                    return l

            resumo = releitura.reler(caso.acervo, caso.estado, caso.config, None, "F026-P0001",
                                     "projeto", motivo="teste", pedido_por="Rafa",
                                     livro=Livro(caso.acervo), leitor=LeitorNovo())
            self.assertEqual(resumo["relidos"], 2)
            # Contrato §14 do painel: regrava leituras/pacote; o antes fica guardado ao lado.
            self.assertEqual(resumo["regravados"], 2)
            self.assertEqual((caso.acervo / resumo["pasta"] / "antes_leituras.json").read_text(), principal)
            self.assertIn("Clube Novo", (res.pasta_projeto / "catalogacao" / "leituras.json").read_text())
            self.assertIn("Clube Novo", (res.pasta_projeto / "catalogacao" / "pacote_tainacan.json").read_text())
            comp = json.loads((caso.acervo / resumo["pasta"] / "comparacao.json").read_text())
            doc = next(iter(comp["documentos"].values()))
            self.assertEqual(doc["mudou"]["cliente"]["agora"], "Clube Novo")
            self.assertTrue(any(l["acao"] == "relido" for l in Livro(caso.acervo).todas()))
            with self.assertRaises(ValueError):
                releitura.alvos(res.pasta_projeto, "tudo")
            um = releitura.alvos(res.pasta_projeto, "folha", ["F026-P0001-1968-S01-D00002"])
            self.assertEqual(len(um), 1 if any("D00002" in c for c in releitura.alvos(res.pasta_projeto, "projeto")) else 0)
        finally:
            caso.tearDown()


class TestCdrSemBitmap(unittest.TestCase):
    def test_folha_branca_vira_texto(self):
        from PIL import Image

        from nucleo import formatos

        self.assertTrue(formatos._quase_branca(Image.new("RGB", (500, 500), "white")))
        img = formatos._texto_em_folha("PLANTA BAIXA\nESC 1:50")
        self.assertFalse(formatos._quase_branca(img))
