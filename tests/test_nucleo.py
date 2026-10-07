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

    def test_segundo_passe_quando_a_confianca_e_baixa(self):
        with TemporaryDirectory() as tmp:
            caminho = prancha_falsa(Path(tmp) / "p1.jpg")
            fraca = resposta_padrao(projeto=campo("CASA DA PR?IA", 0.35), arquiteto=campo("O. C. G.", 0.3))
            forte = resposta_padrao(projeto=campo("CASA DA PRAIA", 0.95))
            cliente = ClienteFalso([fraca, forte])
            leitura = LeitorDeCarimbo(Config(), cliente).ler(caminho)
            self.assertEqual(len(cliente.chamadas), 2, "deveria reler o recorte em alta")
            self.assertEqual(leitura.valores["projeto"], "CASA DA PRAIA")
            self.assertEqual(leitura.passes, 2)

    def test_sem_carimbo_nao_faz_segundo_passe(self):
        with TemporaryDirectory() as tmp:
            caminho = prancha_falsa(Path(tmp) / "p1.jpg")
            vazia = {c.nome: campo("") for c in CAMPOS}
            vazia.update({"carimbo_encontrado": False, "rotacao": 0, "nota": "sem carimbo"})
            cliente = ClienteFalso([vazia])
            leitura = LeitorDeCarimbo(Config(), cliente).ler(caminho)
            self.assertEqual(len(cliente.chamadas), 1)
            self.assertFalse(leitura.carimbo_encontrado)

    def test_cache_de_regiao_usa_uma_chamada_so(self):
        with TemporaryDirectory() as tmp:
            caminho = prancha_falsa(Path(tmp) / "p1.jpg")
            cliente = ClienteFalso([resposta_padrao()])
            leitura = LeitorDeCarimbo(Config(), cliente).ler(caminho, regiao_sugerida=(0.7, 0.8, 1.0, 1.0))
            self.assertEqual(len(cliente.chamadas), 1)
            self.assertEqual(leitura.regiao, (0.7, 0.8, 1.0, 1.0), "guarda a região da PÁGINA, não a do recorte")

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

    def test_consolidacao_normaliza_e_preserva_o_lido(self):
        leituras = [
            self._leitura("a.jpg", "TEATRO DE SANTOS", arquiteto="OSWALDO CORREA GONCALVES"),
            self._leitura("b.jpg", "TEATRO DE SANTOS", arquiteto="HOSWALDO CORREA GONCALVES"),
        ]
        canonico = {
            "projeto": "TEATRO DE SANTOS",
            "arquiteto": "OSWALDO CORRÊA GONÇALVES",
            "cliente": "", "escritorio": "", "endereco": "", "cidade": "", "uf": "",
            "ano_do_projeto": "1968",
            "justificativa": "O H inicial é erro de leitura em uma prancha.",
        }
        grupos.consolidar(leituras, ClienteFalso([canonico]))
        self.assertEqual(leituras[1].valores["arquiteto"], "OSWALDO CORRÊA GONÇALVES")
        self.assertEqual(leituras[1].lidos_originais["arquiteto"], "HOSWALDO CORREA GONCALVES")
        self.assertEqual(leituras[0].ano_do_projeto, "1968")


    def test_consolidacao_nao_inventa_campo_que_ninguem_leu(self):
        leituras = [
            self._leitura("a.jpg", "TEATRO DE SANTOS"),
            self._leitura("b.jpg", "TEATRO DE SANTOS"),
        ]
        canonico = {
            "projeto": "TEATRO DE SANTOS",
            "arquiteto": "",
            "cliente": "",
            "escritorio": "",
            "endereco": "RUA QUE NINGUEM LEU, 100",  # invenção: nenhuma prancha trouxe
            "cidade": "", "uf": "",
            "ano_do_projeto": "",
            "justificativa": "",
        }
        grupos.consolidar(leituras, ClienteFalso([canonico]))
        self.assertEqual(leituras[0].valores.get("endereco", ""), "")

    def test_valor_consolidado_herda_confianca_do_grupo(self):
        boa = self._leitura("a.jpg", "TEATRO", cidade="SANTOS")
        ruim = self._leitura("b.jpg", "TEATRO")
        ruim.valores["cidade"] = ""
        ruim.confiancas["cidade"] = 0.0
        canonico = {
            "projeto": "TEATRO", "arquiteto": "", "cliente": "", "escritorio": "",
            "endereco": "", "cidade": "SANTOS", "uf": "",
            "ano_do_projeto": "", "justificativa": "",
        }
        grupos.consolidar([boa, ruim], ClienteFalso([canonico]))
        self.assertEqual(ruim.valores["cidade"], "SANTOS")
        self.assertGreater(ruim.confiancas["cidade"], 0.6,
                           "campo resolvido pela consolidação não pode ficar marcado como fraco")


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
        for campo_def in CAMPOS:
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


class TestCacheQueSeDesliga(unittest.TestCase):
    """O cache de região tem que parar de custar quando não está poupando."""

    def test_desliga_apos_as_falhas_toleradas(self):
        from nucleo.lote import CacheDeRegiao

        cache = CacheDeRegiao(falhas_toleradas=2)
        cache.guardar((0.7, 0.8, 1.0, 1.0))
        self.assertIsNotNone(cache.obter())
        cache.registrar_falha()
        self.assertIsNotNone(cache.obter(), "uma falha ainda não desliga")
        cache.registrar_falha()
        self.assertIsNone(cache.obter())
        self.assertTrue(cache.desligado)

    def test_desligado_nao_volta_a_guardar(self):
        from nucleo.lote import CacheDeRegiao

        cache = CacheDeRegiao(falhas_toleradas=1)
        cache.registrar_falha()
        cache.guardar((0.1, 0.1, 0.5, 0.5))
        self.assertIsNone(cache.obter())

    def test_lote_desliga_o_cache_e_para_de_gastar_chamada_extra(self):
        with TemporaryDirectory() as tmp:
            pasta = Path(tmp)
            for i in range(6):
                prancha_falsa(pasta / f"p{i}.jpg", 3000, 2000)

            # Resposta boa o bastante para guardar a região, mas o recorte
            # do cache sempre volta fraco: é o caso do acervo real.
            forte = resposta_padrao()
            fraca = resposta_padrao(projeto=campo("BORRADO", 0.2), arquiteto=campo("", 0.0))

            class ClienteAlternado:
                def __init__(self):
                    self.chamadas = []

                def chamar(self, mensagens, ferramenta, sistema):
                    self.chamadas.append(mensagens)
                    # a 1ª chamada de cada prancha via cache manda o recorte:
                    # devolve fraco para simular o cache que não serve
                    texto = str(mensagens)
                    return (fraca if "RECORTE" in texto.upper() else forte), 1000, 100

            cliente = ClienteAlternado()
            cfg = Config(trabalhadores=1, falhas_de_cache_toleradas=2,
                         confianca_para_guardar_regiao=0.5)
            resultado = lote.executar(pasta, cfg, cliente)
            self.assertEqual(len(resultado.leituras), 6)
            # Sem o desligamento seriam ~2 chamadas por prancha depois da 1ª.
            # Com ele, as últimas pranchas gastam 1 chamada só.
            self.assertLess(len(cliente.chamadas), 11,
                            f"cache deveria ter se desligado; chamadas={len(cliente.chamadas)}")


class TestGanhoDeResolucao(unittest.TestCase):
    """O 2º passe só se paga quando o recorte fica mesmo mais nítido."""

    def _leitor(self, **extra):
        return LeitorDeCarimbo(Config(**extra), ClienteFalso([resposta_padrao()]))

    def test_pagina_pequena_nao_ganha_nada(self):
        img = Image.new("RGB", (1200, 900))
        ganho = self._leitor()._ganho_de_resolucao(img, (0.7, 0.8, 1.0, 1.0))
        self.assertAlmostEqual(ganho, 1.0, places=2)

    def test_pagina_grande_ganha_muito(self):
        img = Image.new("RGB", (9000, 6000))
        ganho = self._leitor()._ganho_de_resolucao(img, (0.8, 0.85, 1.0, 1.0))
        self.assertGreater(ganho, 3.0)

    def _resposta_fraca(self) -> dict:
        """Todos os campos fracos, para a média cair mesmo abaixo do limiar."""
        return resposta_padrao(
            projeto=campo("BORRADO", 0.3),
            arquiteto=campo("ILEGIVEL", 0.3),
            ano=campo("19??", 0.3),
            folha=campo("0?", 0.3),
        )

    def test_nao_faz_segundo_passe_sem_ganho(self):
        """Página pequena: o recorte já foi enviado na resolução máxima."""
        with TemporaryDirectory() as tmp:
            caminho = prancha_falsa(Path(tmp) / "pequena.jpg", 1200, 900)
            cliente = ClienteFalso([self._resposta_fraca()])
            leitura = LeitorDeCarimbo(Config(), cliente).ler(caminho)
            self.assertLess(leitura.confianca_media, 0.75, "a média tem que estar baixa")
            self.assertEqual(len(cliente.chamadas), 1,
                             "reler o mesmo pixel não melhora nada")
            self.assertEqual(leitura.passes, 1)

    def test_faz_segundo_passe_quando_ha_ganho(self):
        with TemporaryDirectory() as tmp:
            caminho = prancha_falsa(Path(tmp) / "grande.jpg", 6000, 4000)
            cliente = ClienteFalso([self._resposta_fraca(), resposta_padrao()])
            LeitorDeCarimbo(Config(), cliente).ler(caminho)
            self.assertEqual(len(cliente.chamadas), 2)


class TestMedicaoDoSegundoPasse(unittest.TestCase):
    """O 2º passe dobra o custo da prancha; o relatório tem que medir o retorno."""

    def _fraca(self, conf=0.3) -> dict:
        return resposta_padrao(
            projeto=campo("BORRADO", conf), arquiteto=campo("ILEGIVEL", conf),
            ano=campo("19??", conf), folha=campo("0?", conf),
        )

    def test_registra_confianca_antes_e_ganho(self):
        with TemporaryDirectory() as tmp:
            caminho = prancha_falsa(Path(tmp) / "g.jpg", 6000, 4000)
            cliente = ClienteFalso([self._fraca(), resposta_padrao()])
            leitura = LeitorDeCarimbo(Config(), cliente).ler(caminho)
            self.assertTrue(leitura.fez_segundo_passe)
            self.assertAlmostEqual(leitura.confianca_antes_do_2o, 0.3, places=2)
            self.assertGreater(leitura.ganho_de_resolucao, 1.3)
            self.assertGreater(leitura.confianca_media, leitura.confianca_antes_do_2o)

    def test_sem_segundo_passe_nao_marca_nada(self):
        with TemporaryDirectory() as tmp:
            caminho = prancha_falsa(Path(tmp) / "g.jpg", 6000, 4000)
            leitura = LeitorDeCarimbo(Config(), ClienteFalso([resposta_padrao()])).ler(caminho)
            self.assertFalse(leitura.fez_segundo_passe)

    def test_relatorio_mostra_o_rendimento(self):
        boa = Leitura(arquivo="a.jpg", valores={"projeto": "X"}, confiancas={"projeto": 0.95},
                      carimbo_encontrado=True, fez_segundo_passe=True,
                      confianca_antes_do_2o=0.40, ganho_de_resolucao=2.4)
        inutil = Leitura(arquivo="b.jpg", valores={"projeto": "Y"}, confiancas={"projeto": 0.42},
                         carimbo_encontrado=True, fez_segundo_passe=True,
                         confianca_antes_do_2o=0.41, ganho_de_resolucao=1.5)
        with TemporaryDirectory() as tmp:
            texto = planilha.escrever_relatorio(
                [boa, inutil], Path(tmp) / "r.txt", 0.10
            ).read_text()
            self.assertIn("Segundo passe", texto)
            self.assertIn("Pranchas que releram   2/2", texto)
            self.assertIn("Melhoraram de fato     1", texto)
            self.assertIn("1.9x", texto)  # (2.4 + 1.5) / 2

    def test_relatorio_sugere_baixar_o_limiar_quando_rende_pouco(self):
        inuteis = [
            Leitura(arquivo=f"{i}.jpg", valores={"projeto": "Y"},
                    confiancas={"projeto": 0.42}, carimbo_encontrado=True,
                    fez_segundo_passe=True, confianca_antes_do_2o=0.41,
                    ganho_de_resolucao=2.0)
            for i in range(5)
        ]
        with TemporaryDirectory() as tmp:
            texto = planilha.escrever_relatorio(inuteis, Path(tmp) / "r.txt").read_text()
            self.assertIn("rendendo pouco", texto)
            self.assertIn("confianca_minima_para_aceitar", texto)

    def test_sem_segundo_passe_o_relatorio_nao_fala_disso(self):
        leitura = Leitura(arquivo="a.jpg", valores={"projeto": "X"},
                          confiancas={"projeto": 0.9}, carimbo_encontrado=True)
        with TemporaryDirectory() as tmp:
            texto = planilha.escrever_relatorio([leitura], Path(tmp) / "r.txt").read_text()
            self.assertNotIn("Segundo passe", texto)
