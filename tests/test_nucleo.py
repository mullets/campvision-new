"""Testes do núcleo. Nenhum toca a rede — o cliente de API é falso.

Rode com: python -m pytest tests -q   (ou python -m unittest discover tests)
"""

from __future__ import annotations

import json
import threading
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from PIL import Image, ImageDraw

from nucleo import grupos, imagem, lote, planilha
from nucleo.aplicar import limpar_nome, montar_nome, planejar
from nucleo.config import Config
from nucleo.esquema import CAMPOS, Leitura, esquema_ferramenta
from nucleo.visao import LeitorDeCarimbo


def campo(valor: str, conf: float = 0.9) -> dict:
    return {"valor": valor, "confianca": conf}


def resposta_padrao(**sobrescritas) -> dict:
    base = {c.nome: campo("") for c in CAMPOS}
    base.update(
        {
            "carimbo_encontrado": True,
            "rotacao": 0,
            "nota": "",
            "regiao_carimbo": [0.7, 0.8, 0.99, 0.99],
            "projeto": campo("CASA DA PRAIA"),
            "arquiteto": campo("OSWALDO CORREA GONCALVES"),
            "ano": campo("1968"),
            "folha": campo("03"),
        }
    )
    base.update(sobrescritas)
    return base


class ClienteFalso:
    """Registra as chamadas e devolve o que o teste mandar."""

    def __init__(self, respostas: list[dict]) -> None:
        self.respostas = list(respostas)
        self.chamadas: list[dict] = []

    def chamar(self, mensagens, ferramenta, sistema):
        self.chamadas.append({"mensagens": mensagens, "ferramenta": ferramenta["name"]})
        resposta = self.respostas.pop(0) if len(self.respostas) > 1 else self.respostas[0]
        return resposta, 1000, 100


def prancha_falsa(caminho: Path, largura=2400, altura=1600) -> Path:
    img = Image.new("RGB", (largura, altura), "white")
    desenho = ImageDraw.Draw(img)
    desenho.rectangle([largura * 0.7, altura * 0.8, largura - 10, altura - 10], outline="black", width=5)
    desenho.text((largura * 0.72, altura * 0.85), "CASA DA PRAIA", fill="black")
    img.save(caminho, quality=90)
    return caminho


class TestImagem(unittest.TestCase):
    def test_reduzir_nunca_amplia(self):
        img = Image.new("RGB", (800, 600))
        self.assertEqual(imagem.reduzir(img, 1568).size, (800, 600))

    def test_reduzir_respeita_lado_maximo_e_proporcao(self):
        img = Image.new("RGB", (18947, 13398))  # tamanho real de prancha unida
        reduzida = imagem.reduzir(img, 1568)
        self.assertEqual(max(reduzida.size), 1568)
        self.assertAlmostEqual(reduzida.width / reduzida.height, 18947 / 13398, places=2)

    def test_recortar_com_margem_nao_estoura_borda(self):
        img = Image.new("RGB", (1000, 1000))
        recorte = imagem.recortar(img, (0.9, 0.9, 1.0, 1.0), margem=0.5)
        self.assertLessEqual(recorte.width, 1000)
        self.assertGreater(recorte.width, 100)

    def test_para_envio_gera_base64_dentro_do_limite(self):
        img = Image.new("RGB", (5000, 4000), "white")
        env = imagem.para_envio(img, 1568)
        self.assertLessEqual(max(env.largura, env.altura), 1568)
        self.assertTrue(env.base64)
        self.assertGreater(env.tokens_estimados, 0)

    def test_girar_horario_troca_lados(self):
        img = Image.new("RGB", (400, 200))
        self.assertEqual(imagem.girar(img, 90).size, (200, 400))
        self.assertEqual(imagem.girar(img, 180).size, (400, 200))


class TestLeitor(unittest.TestCase):
    def test_um_passe_quando_a_confianca_e_boa(self):
        with TemporaryDirectory() as tmp:
            caminho = prancha_falsa(Path(tmp) / "p1.jpg")
            cliente = ClienteFalso([resposta_padrao()])
            leitura = LeitorDeCarimbo(Config(), cliente).ler(caminho)
            self.assertEqual(len(cliente.chamadas), 1)
            self.assertEqual(leitura.valores["projeto"], "CASA DA PRAIA")
            self.assertTrue(leitura.carimbo_encontrado)
            self.assertEqual(leitura.passes, 1)

    def test_sem_carimbo_nao_faz_segundo_passe(self):
        with TemporaryDirectory() as tmp:
            caminho = prancha_falsa(Path(tmp) / "p1.jpg")
            vazia = {c.nome: campo("") for c in CAMPOS}
            vazia.update({"carimbo_encontrado": False, "rotacao": 0, "nota": "sem carimbo"})
            cliente = ClienteFalso([vazia])
            leitura = LeitorDeCarimbo(Config(), cliente).ler(caminho)
            self.assertEqual(len(cliente.chamadas), 1)
            self.assertFalse(leitura.carimbo_encontrado)

    def test_resposta_torta_nao_derruba_a_leitura(self):
        with TemporaryDirectory() as tmp:
            caminho = prancha_falsa(Path(tmp) / "p1.jpg")
            torta = resposta_padrao(
                regiao_carimbo=[2.0, "x", -1, 0.5], rotacao="noventa", ano="1968"
            )
            cliente = ClienteFalso([torta])
            leitura = LeitorDeCarimbo(Config(), cliente).ler(caminho)
            self.assertEqual(leitura.rotacao, 0)
            self.assertEqual(leitura.valores["ano"], "1968")

    def test_arquivo_corrompido_vira_erro_e_nao_excecao(self):
        with TemporaryDirectory() as tmp:
            ruim = Path(tmp) / "quebrado.jpg"
            ruim.write_bytes(b"isso nao e imagem")
            leitura = LeitorDeCarimbo(Config(), ClienteFalso([resposta_padrao()])).ler(ruim)
            self.assertTrue(leitura.erro)
            self.assertFalse(leitura.carimbo_encontrado)


class TestLote(unittest.TestCase):
    def test_le_a_pasta_e_grava_checkpoint(self):
        with TemporaryDirectory() as tmp:
            pasta = Path(tmp)
            for i in range(4):
                prancha_falsa(pasta / f"p{i}.jpg", 1200, 900)
            cliente = ClienteFalso([resposta_padrao()])
            cfg = Config(trabalhadores=2)
            resultado = lote.executar(pasta, cfg, cliente)
            self.assertEqual(len(resultado.leituras), 4)
            self.assertEqual(resultado.progresso.com_carimbo, 4)
            linhas = (pasta / lote.NOME_CHECKPOINT).read_text().strip().splitlines()
            self.assertEqual(len(linhas), 4)
            self.assertIn("CASA DA PRAIA", json.loads(linhas[0])["valores"]["projeto"])

    def test_retoma_do_checkpoint_sem_gastar_api(self):
        with TemporaryDirectory() as tmp:
            pasta = Path(tmp)
            for i in range(3):
                prancha_falsa(pasta / f"p{i}.jpg", 1200, 900)
            cliente1 = ClienteFalso([resposta_padrao()])
            lote.executar(pasta, Config(trabalhadores=1), cliente1)
            self.assertEqual(len(cliente1.chamadas), 3)

            cliente2 = ClienteFalso([resposta_padrao()])
            resultado = lote.executar(pasta, Config(trabalhadores=1), cliente2)
            self.assertEqual(len(cliente2.chamadas), 0, "nada deveria ser reenviado à API")
            self.assertEqual(len(resultado.leituras), 3)

    def test_cancelamento_para_o_lote(self):
        with TemporaryDirectory() as tmp:
            pasta = Path(tmp)
            for i in range(12):
                prancha_falsa(pasta / f"p{i:02d}.jpg", 800, 600)
            evento = threading.Event()

            def parar(p: lote.Progresso) -> None:
                if p.concluidos >= 2:
                    evento.set()

            resultado = lote.executar(
                pasta, Config(trabalhadores=1), ClienteFalso([resposta_padrao()]),
                ao_progredir=parar, cancelar=evento,
            )
            self.assertTrue(resultado.cancelado)
            self.assertLess(len(resultado.leituras), 12)


class TestGrupos(unittest.TestCase):
    def _leitura(self, arquivo: str, projeto: str, **extras) -> Leitura:
        valores = {"projeto": projeto, **extras}
        return Leitura(
            arquivo=arquivo,
            valores=valores,
            confiancas={k: 0.9 for k in valores},
            carimbo_encontrado=True,
        )

    def test_agrupa_grafias_parecidas(self):
        leituras = [
            self._leitura("a.jpg", "TEATRO DE SANTOS"),
            self._leitura("b.jpg", "TEATRO DE SANTOS "),
            self._leitura("c.jpg", "TEATR0 DE SANTOS"),  # erro de leitura
            self._leitura("d.jpg", "CASA DA PRAIA"),
        ]
        resultado = grupos.agrupar(leituras)
        self.assertEqual(len(resultado), 2)
        maior = max(resultado.values(), key=len)
        self.assertEqual(len(maior), 3)

    def test_sem_projeto_nao_e_espalhado(self):
        leituras = [self._leitura("a.jpg", "TEATRO"), self._leitura("b.jpg", "")]
        resultado = grupos.agrupar(leituras)
        self.assertIn("(sem projeto)", resultado)
        self.assertEqual(len(resultado["(sem projeto)"]), 1)


class TestPlanilha(unittest.TestCase):
    def _amostra(self) -> list[Leitura]:
        return [
            Leitura(
                arquivo="p2.jpg",
                valores={"projeto": "TEATRO", "folha": "10", "arquiteto": "OCG"},
                confiancas={"projeto": 0.95, "folha": 0.5, "arquiteto": 0.9},
                carimbo_encontrado=True,
            ),
            Leitura(
                arquivo="p1.jpg",
                valores={"projeto": "TEATRO", "folha": "2", "arquiteto": "OCG"},
                confiancas={"projeto": 0.95, "folha": 0.9, "arquiteto": 0.9},
                carimbo_encontrado=True,
            ),
        ]

    def test_csv_sai_ordenado_por_folha(self):
        from tests.test_acervo import ler_csv

        with TemporaryDirectory() as tmp:
            linhas = ler_csv(planilha.escrever_csv(self._amostra(), Path(tmp) / "c.csv"))
            self.assertEqual(linhas[0][:2], ["Código", "Arquivo"])
            self.assertEqual(linhas[1][1], "p1.jpg")
            self.assertEqual(linhas[2][1], "p2.jpg")

    def test_csv_tem_as_mesmas_colunas(self):
        import csv as _csv

        with TemporaryDirectory() as tmp:
            destino = planilha.escrever_csv(self._amostra(), Path(tmp) / "c.csv")
            with destino.open(encoding="utf-8-sig") as f:
                linhas = list(_csv.reader(f))
            self.assertEqual(linhas[0][:4], ["Código", "Arquivo", "OK?", "Revisar"])
            self.assertEqual(len(linhas), 3)

    def test_relatorio_conta_campos_fracos(self):
        with TemporaryDirectory() as tmp:
            destino = planilha.escrever_relatorio(self._amostra(), Path(tmp) / "r.txt", 1.23)
            texto = destino.read_text()
            self.assertIn("Pranchas processadas: 2", texto)
            self.assertIn("US$ 1.23", texto)
            self.assertIn("a revisar: 1", texto)  # a folha "10" tem confiança 0.5


class TestAplicar(unittest.TestCase):
    def test_limpar_nome_tira_caractere_proibido(self):
        self.assertEqual(limpar_nome('CASA/DA:PRAIA?'), "CASADAPRAIA")

    def test_montar_nome_usa_projeto_e_sequencial(self):
        linha = {"Projeto": "TEATRO DE SANTOS", "Título da prancha": "PLANTA TERREO"}
        self.assertEqual(montar_nome(linha, 3), "TEATRO DE SANTOS - 003 - PLANTA TERREO")

    def test_planejar_le_o_csv_revisado_e_nao_toca_em_disco(self):
        with TemporaryDirectory() as tmp:
            pasta = Path(tmp)
            prancha_falsa(pasta / "p1.jpg", 600, 400)
            leitura = Leitura(
                arquivo="p1.jpg",
                valores={"projeto": "TEATRO DE SANTOS", "ano": "1968", "titulo_prancha": "PLANTA"},
                confiancas={},
                carimbo_encontrado=True,
            )
            csv_path = planilha.escrever_csv([leitura], pasta / "catalogacao.csv")
            acoes = planejar(pasta, csv_path)
            self.assertEqual(len(acoes), 1)
            self.assertIn("1968", str(acoes[0].destino))
            self.assertIn("TEATRO DE SANTOS", acoes[0].destino.name)
            self.assertFalse(acoes[0].destino.exists(), "planejar não pode criar nada")


class TestEsquema(unittest.TestCase):
    def test_ferramenta_exige_todos_os_campos(self):
        ferramenta = esquema_ferramenta()
        obrigatorios = set(ferramenta["input_schema"]["required"])
        from nucleo.esquema import CAMPOS_DO_MODELO
        for campo_def in CAMPOS_DO_MODELO:
            self.assertIn(campo_def.nome, obrigatorios)

    def test_confianca_media_ignora_campo_vazio(self):
        leitura = Leitura(
            valores={"projeto": "X", "cliente": ""},
            confiancas={"projeto": 0.8, "cliente": 0.0},
        )
        self.assertAlmostEqual(leitura.confianca_media, 0.8)


if __name__ == "__main__":
    unittest.main()


class TestConfigExemplo(unittest.TestCase):
    """O config.exemplo.json não pode envelhecer em relação ao código."""

    def _exemplo(self) -> dict:
        caminho = Path(__file__).resolve().parent.parent / "config.exemplo.json"
        self.assertTrue(caminho.exists(), "config.exemplo.json sumiu do repositório")
        return json.loads(caminho.read_text(encoding="utf-8"))

    def test_tem_todas_as_opcoes_do_config(self):
        from dataclasses import fields as campos_de

        exemplo = self._exemplo()
        faltando = [
            c.name for c in campos_de(Config)
            if c.name != "api_key" and c.name not in exemplo
        ]
        self.assertEqual(
            faltando, [],
            f"opções novas em Config sem entrar no exemplo: {faltando}. "
            "Rode: python -c \"from nucleo.config import Config; "
            "open('config.exemplo.json','w').write(Config.modelo_json()+chr(10))\"",
        )

    def test_nao_traz_a_chave_de_api(self):
        self.assertNotIn("api_key", self._exemplo())

    def test_e_carregavel_pelo_config(self):
        with TemporaryDirectory() as tmp:
            caminho = Path(tmp) / "config.json"
            caminho.write_text(Config.modelo_json(), encoding="utf-8")
            cfg = Config.carregar(caminho)
            self.assertEqual(cfg.modelo, Config().modelo)
            self.assertEqual(cfg.extensoes, Config().extensoes)

    def test_criar_se_faltar_nao_sobrescreve(self):
        with TemporaryDirectory() as tmp:
            caminho = Path(tmp) / "config.json"
            self.assertTrue(Config.criar_se_faltar(caminho))
            caminho.write_text('{"trabalhadores": 9}', encoding="utf-8")
            self.assertFalse(Config.criar_se_faltar(caminho))
            self.assertEqual(Config.carregar(caminho).trabalhadores, 9)


class TestMetodoDeLeitura(unittest.TestCase):
    """Método de leitura (projeto Tainacam: claude/campvision-metodo-de-leitura.md)."""

    def test_folha_inteira_uma_chamada_2000px(self):
        import base64, io
        with TemporaryDirectory() as tmp:
            caminho = prancha_falsa(Path(tmp) / "p1.jpg", 5000, 3500)
            cliente = ClienteFalso([resposta_padrao(projeto=campo("X", 0.2))])
            LeitorDeCarimbo(Config(), cliente).ler(caminho)
            self.assertEqual(len(cliente.chamadas), 1, "nunca relê recorte, mesmo com confiança baixa")
            dados = cliente.chamadas[0]["mensagens"][0]["content"][0]["source"]["data"]
            enviada = Image.open(io.BytesIO(base64.b64decode(dados)))
            self.assertEqual(max(enviada.size), 2000)
            self.assertEqual(enviada.size, (2000, 1400), "página inteira, sem recorte")

    def test_prompt_de_prancha_e_literal(self):
        from nucleo import visao
        texto = visao.INSTRUCOES
        for regra in ("literalmente", "INTEIRA", "null", "alternativas", "conhecimento externo"):
            self.assertIn(regra, texto)

    def test_campos_novos_do_esquema(self):
        props = esquema_ferramenta()["input_schema"]["properties"]
        for nome in ("escritorio", "codigo_serie", "revisao", "transcricao_integral",
                     "materiais_citados", "anotacoes_manuscritas"):
            self.assertIn(nome, props)
        self.assertNotIn("tipo", props, "tipo de desenho é derivado em código, não pedido ao modelo")
        self.assertNotIn("ano", props, "ano vem da data escrita, em código")
        self.assertIn("alternativas", props["projeto"]["properties"])
        self.assertIn("onde", props["projeto"]["properties"])

    def test_alternativas_onde_e_derivacoes(self):
        with TemporaryDirectory() as tmp:
            caminho = prancha_falsa(Path(tmp) / "p1.jpg")
            r = resposta_padrao(
                data={"valor": "14.06.85", "confianca": 0.6, "alternativas": ["14.06.83"], "onde": "margem"},
                titulo_prancha=campo("PLANTA DE SITUAÇÃO"),
                transcricao_integral="PLANTA DE SITUAÇÃO 14.06.85",
                materiais_citados=["Perstorp Berilo"],
            )
            leitura = LeitorDeCarimbo(Config(), ClienteFalso([r])).ler(caminho)
            self.assertEqual(leitura.valores["ano"], "1985")
            self.assertEqual(leitura.alternativas["ano"], ["1983"])
            self.assertEqual(leitura.onde["data"], "margem")
            self.assertEqual(leitura.valores["tipo"], "Implantação")
            self.assertEqual(leitura.materiais_citados, ["Perstorp Berilo"])

    def test_fotografia_usa_prompt_proprio(self):
        with TemporaryDirectory() as tmp:
            caminho = prancha_falsa(Path(tmp) / "F026-P0001-1975-S03-D00001.jpg")
            foto = {"tipo_de_imagem": "fotografia", "assunto": "fachada", "enquadramento": "externa",
                    "legenda_proposta": "Fachada principal", "confianca": 0.8,
                    "texto_na_imagem": ["SESC"]}
            cliente = ClienteFalso([foto])
            leitura = LeitorDeCarimbo(Config(), cliente).ler(caminho)
            self.assertEqual(cliente.chamadas[0]["ferramenta"], "registrar_fotografia")
            self.assertEqual(leitura.modo, "fotografia")
            self.assertEqual(leitura.valores["titulo_prancha"], "Fachada principal")
            self.assertEqual(leitura.valores["tipo"], "Fotografia")
            from nucleo import visao
            self.assertIn("NÃO atribua autoria da fotografia", visao.INSTRUCOES_FOTO)


class TestConsolidacaoEmCodigo(unittest.TestCase):
    def _l(self, arquivo, projeto, **v):
        valores = {"projeto": projeto, **v}
        return Leitura(arquivo=arquivo, valores=valores, confiancas={k: 0.9 for k in valores},
                       carimbo_encontrado=True)

    def test_moda_e_quarentena_hoswaldo_nao_sequestra(self):
        ls = [self._l(f"{i}.jpg", "TEATRO", arquiteto="OSWALDO CORREA GONCALVES") for i in range(3)]
        ls.append(self._l("x.jpg", "TEATRO", arquiteto="HOSWALDO CORREA GONCALVES"))
        _, t_in, t_out = grupos.consolidar(ls)
        self.assertEqual((t_in, t_out), (0, 0), "consolidação não chama modelo")
        self.assertIn("arquiteto", ls[3].outliers)
        self.assertEqual(ls[3].lidos_originais["arquiteto"], "HOSWALDO CORREA GONCALVES")
        self.assertEqual(ls[3].valores["arquiteto"], "HOSWALDO CORREA GONCALVES",
                         "autoria nunca é corrigida pelo consenso")
        self.assertEqual(ls[0].valores["arquiteto"], "OSWALDO CORREA GONCALVES")

    def test_valor_isolado_nao_corrige_ninguem(self):
        ls = [self._l("a.jpg", "TEATRO", cidade="SANTOS"), self._l("b.jpg", "TEATRO", cidade="SAO VICENTE")]
        grupos.consolidar(ls)
        self.assertEqual(ls[0].valores["cidade"], "SANTOS")
        self.assertEqual(ls[1].valores["cidade"], "SAO VICENTE")

    def test_ano_85_vira_ressalva_nao_correcao(self):
        ls = [self._l(f"{i}.jpg", "McD-9", ano="1983") for i in range(4)]
        duvida = self._l("z.jpg", "McD-9", ano="1985")
        duvida.alternativas["ano"] = ["1983"]
        ls.append(duvida)
        grupos.consolidar(ls)
        self.assertEqual(duvida.valores["ano"], "1985", "ano da prancha fica intacto")
        self.assertEqual(duvida.ano_do_projeto, "1983")
        self.assertTrue(duvida.ressalvas and "conferir no original" in duvida.ressalvas[0])

    def test_revisao_separa_reforma(self):
        ls = [self._l("a.jpg", "McD-1"), self._l("b.jpg", "McD-1", revisao="R-1")]
        self.assertEqual(len(grupos.agrupar(ls)), 2)


