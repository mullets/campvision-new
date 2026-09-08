"""Testes contra a estrutura real do acervo — a da captura de tela.

    BSG-EdificioPiracicaba-AnteProjeto-1979/   ← projeto: JPG/ + TIF/
    ├── info_projeto.json
    ├── status.json
    ├── JPG/
    └── TIF/

    F001 - ARM - Arnaldo Martino/              ← container (fundo)
    └── P0001 - I Simpósio ... - 1979/         ← projeto (marcador)
        ├── info_projeto.json
        ├── status.json
        ├── catalogacao/
        └── 01 - Desenhos e Pranchas/
            ├── 01 - Arquivo Arquivístico (TIFF)/
            └── 03 - Preview (JPG)/            ← é daqui que ele deve ler
"""

from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from nucleo import vigia
from nucleo.config import Config
from nucleo.vigia import CONTAINER, PROJETO, classificar
from tests.test_nucleo import ClienteFalso, prancha_falsa, resposta_padrao

CFG = Config(trabalhadores=1, consolidar_por_projeto=False)


def imagens(pasta: Path, n: int = 2, prefixo: str = "p") -> Path:
    pasta.mkdir(parents=True, exist_ok=True)
    for i in range(n):
        prancha_falsa(pasta / f"{prefixo}{i:03d}.jpg", 600, 400)
    return pasta


def marcar(pasta: Path, status: str = "enviado_windows", info: dict | None = None) -> Path:
    pasta.mkdir(parents=True, exist_ok=True)
    (pasta / "status.json").write_text(json.dumps({"status": status}), encoding="utf-8")
    if info is not None:
        (pasta / "info_projeto.json").write_text(
            json.dumps(info, ensure_ascii=False), encoding="utf-8"
        )
    return pasta


def montar_acervo(raiz: Path) -> None:
    """Reproduz a árvore da captura de tela."""
    # Projeto no formato antigo: JPG/ e TIF/ lado a lado
    antigo = marcar(raiz / "BSG-EdificioPiracicaba-AnteProjeto-1979", info={"ano": "1979"})
    imagens(antigo / "JPG", 3)
    imagens(antigo / "TIF", 3, prefixo="t")

    # Fundo com projetos codificados e imagens duas camadas abaixo
    fundo = raiz / "F001 - ARM - Arnaldo Martino"
    p1 = marcar(fundo / "P0001 - I Simposio Brasil Africa Ocidental - 1979")
    imagens(p1 / "01 - Desenhos e Pranchas" / "01 - Arquivo Arquivístico (TIFF)", 4, "t")
    imagens(p1 / "01 - Desenhos e Pranchas" / "03 - Preview (JPG)", 4)
    (p1 / "catalogacao").mkdir()

    p2 = marcar(fundo / "P0002 - MICE 80 - 1980")
    imagens(p2 / "01 - Desenhos e Pranchas" / "03 - Preview (JPG)", 2)

    fundo2 = raiz / "F002 - BSG - Barretto Segnini"
    p3 = marcar(fundo2 / "P0002 - paroquia Mae Salvador - 1973")
    imagens(p3 / "01 - Desenhos e Pranchas" / "03 - Preview (JPG)", 2)


class TestClassificacao(unittest.TestCase):
    def test_fundo_e_container_projeto_e_projeto(self):
        with TemporaryDirectory() as tmp:
            raiz = Path(tmp)
            montar_acervo(raiz)
            self.assertEqual(classificar(raiz / "F001 - ARM - Arnaldo Martino", CFG), CONTAINER)
            self.assertEqual(
                classificar(raiz / "F001 - ARM - Arnaldo Martino"
                            / "P0001 - I Simposio Brasil Africa Ocidental - 1979", CFG),
                PROJETO,
            )

    def test_projeto_sem_marcador_com_jpg_e_tif(self):
        """`Projeto/{JPG,TIF}` é UM projeto, não dois."""
        with TemporaryDirectory() as tmp:
            raiz = Path(tmp)
            projeto = raiz / "SemMarcador"
            imagens(projeto / "JPG", 2)
            imagens(projeto / "TIF", 2, "t")
            self.assertEqual(classificar(projeto, CFG), PROJETO)


class TestDescobertaNoAcervoReal(unittest.TestCase):
    def _achados(self, raiz: Path) -> dict[str, vigia.Projeto]:
        return {p.nome: p for p in vigia.descobrir(raiz, CFG)}

    def test_acha_os_quatro_projetos_e_nenhum_a_mais(self):
        with TemporaryDirectory() as tmp:
            raiz = Path(tmp)
            montar_acervo(raiz)
            achados = self._achados(raiz)
            self.assertEqual(len(achados), 4, f"achou: {sorted(achados)}")
            for nome in achados:
                self.assertNotIn("Preview", nome)
                self.assertNotIn("TIFF", nome)
                self.assertNotIn("Desenhos", nome)

    def test_fundo_nao_vira_projeto(self):
        with TemporaryDirectory() as tmp:
            raiz = Path(tmp)
            montar_acervo(raiz)
            self.assertNotIn("F001 - ARM - Arnaldo Martino", self._achados(raiz))

    def test_prefere_o_preview_jpg_e_ignora_o_tiff(self):
        with TemporaryDirectory() as tmp:
            raiz = Path(tmp)
            montar_acervo(raiz)
            projeto = self._achados(raiz)["P0001 - I Simposio Brasil Africa Ocidental - 1979"]
            self.assertEqual(len(projeto.pastas_imagens), 1)
            self.assertIn("Preview", projeto.pastas_imagens[0].name)
            self.assertEqual(len(projeto.arquivos(CFG)), 4)

    def test_projeto_antigo_prefere_jpg_a_tif(self):
        with TemporaryDirectory() as tmp:
            raiz = Path(tmp)
            montar_acervo(raiz)
            projeto = self._achados(raiz)["BSG-EdificioPiracicaba-AnteProjeto-1979"]
            self.assertEqual([p.name for p in projeto.pastas_imagens], ["JPG"])

    def test_so_tiff_disponivel_le_o_tiff(self):
        with TemporaryDirectory() as tmp:
            raiz = Path(tmp)
            projeto = marcar(raiz / "P0009 - So Matriz - 1970")
            imagens(projeto / "01 - Desenhos" / "01 - Arquivo Arquivístico (TIFF)", 3, "t")
            achado = vigia.descobrir(raiz, CFG)[0]
            self.assertIn("TIFF", achado.pastas_imagens[0].name)
            self.assertEqual(len(achado.arquivos(CFG)), 3)

    def test_imagens_em_dois_ramos_de_preview_sao_somadas(self):
        with TemporaryDirectory() as tmp:
            raiz = Path(tmp)
            projeto = marcar(raiz / "P0010 - Dois Ramos - 1975")
            imagens(projeto / "01 - Desenhos e Pranchas" / "03 - Preview (JPG)", 2, "a")
            imagens(projeto / "02 - Documentos" / "03 - Preview (JPG)", 3, "b")
            achado = vigia.descobrir(raiz, CFG)[0]
            self.assertEqual(len(achado.pastas_imagens), 2)
            self.assertEqual(len(achado.arquivos(CFG)), 5)

    def test_catalogacao_nao_e_lida_de_volta(self):
        with TemporaryDirectory() as tmp:
            raiz = Path(tmp)
            projeto = marcar(raiz / "P0011 - Com Saida - 1980")
            imagens(projeto / "JPG", 2)
            imagens(projeto / "catalogacao", 5, "lixo")
            achado = vigia.descobrir(raiz, CFG)[0]
            self.assertEqual(len(achado.arquivos(CFG)), 2)

    def test_projeto_marcado_e_vazio_e_pulado_sem_erro(self):
        with TemporaryDirectory() as tmp:
            raiz = Path(tmp)
            marcar(raiz / "P0012 - Vazio - 1990")
            self.assertEqual(vigia.descobrir(raiz, CFG), [])


class TestProcessamentoNoAcervoReal(unittest.TestCase):
    def test_le_o_projeto_inteiro_e_grava_na_raiz_dele(self):
        with TemporaryDirectory() as tmp:
            raiz = Path(tmp)
            montar_acervo(raiz)
            projeto = {p.nome: p for p in vigia.varrer(raiz, CFG)}[
                "P0001 - I Simposio Brasil Africa Ocidental - 1979"
            ]
            evento = vigia.processar(projeto, CFG, ClienteFalso([resposta_padrao()]))
            self.assertEqual(evento.pranchas, 4, "só as 4 do preview, não as 4 do TIFF")
            # checkpoint e planilha na RAIZ do projeto, não dentro do Preview
            self.assertTrue((projeto.pasta / "campvision2_checkpoint.jsonl").exists())
            self.assertTrue((projeto.pasta / "catalogacao" / "catalogacao.xlsx").exists())
            self.assertFalse((projeto.pastas_imagens[0] / "campvision2_checkpoint.jsonl").exists())

    def test_status_existente_e_respeitado(self):
        with TemporaryDirectory() as tmp:
            raiz = Path(tmp)
            projeto = marcar(raiz / "P0013 - Ja Feito - 1970", status="campvision_concluido")
            imagens(projeto / "JPG", 2)
            self.assertEqual(vigia.varrer(raiz, CFG), [], "não pode reprocessar")
            self.assertEqual(len(vigia.descobrir(raiz, CFG)), 1, "mas continua visível")


if __name__ == "__main__":
    unittest.main()


class TestInfoProjeto(unittest.TestCase):
    def test_le_chaves_com_nomes_variados(self):
        from nucleo import info_projeto

        with TemporaryDirectory() as tmp:
            pasta = Path(tmp)
            (pasta / "info_projeto.json").write_text(json.dumps({
                "nome_do_projeto": "Edifício Piracicaba",
                "autoria": "Barretto Segnini",
                "data_projeto": "1979-04-03",
                "municipio": "São Paulo",
                "campo_que_nao_conheco": "seja o que for",
            }, ensure_ascii=False), encoding="utf-8")
            campos = info_projeto.ler(pasta)
            self.assertEqual(campos["projeto"], "Edifício Piracicaba")
            self.assertEqual(campos["arquiteto"], "Barretto Segnini")
            self.assertEqual(campos["ano"], "1979", "extrai o ano de uma data completa")
            self.assertEqual(campos["cidade"], "São Paulo")
            self.assertNotIn("campo_que_nao_conheco", campos)

    def test_json_aninhado_e_achatado(self):
        from nucleo import info_projeto

        with TemporaryDirectory() as tmp:
            pasta = Path(tmp)
            (pasta / "info_projeto.json").write_text(json.dumps({
                "projeto": {"titulo": "MICE 80", "ano": "1980"},
                "palavras_chave": ["planta", "corte"],
            }, ensure_ascii=False), encoding="utf-8")
            campos = info_projeto.ler(pasta)
            self.assertEqual(campos["projeto"], "MICE 80")
            self.assertEqual(campos["ano"], "1980")

    def test_arquivo_corrompido_nao_quebra(self):
        from nucleo import info_projeto

        with TemporaryDirectory() as tmp:
            pasta = Path(tmp)
            (pasta / "info_projeto.json").write_text("{quebrado", encoding="utf-8")
            self.assertEqual(info_projeto.ler(pasta), {})

    def test_info_vence_a_pasta_mas_perde_do_carimbo(self):
        from nucleo import vigia as mod

        with TemporaryDirectory() as tmp:
            raiz = Path(tmp)
            projeto = marcar(
                raiz / "P0001 - Nome Da Pasta - 1999",
                info={"nome": "NOME DO INFO", "ano": "1979"},
            )
            imagens(projeto / "JPG", 1)
            achado = mod.varrer(raiz, CFG)[0]
            # o carimbo falso diz CASA DA PRAIA e não traz ano
            mod.processar(achado, CFG, ClienteFalso([
                dict(resposta_padrao(), ano={"valor": "", "confianca": 0.0})
            ]))
            from nucleo.planilha import ler_json

            leitura = ler_json(achado.pasta / "catalogacao" / "leituras.json")[0]
            self.assertEqual(leitura.valores["projeto"], "CASA DA PRAIA",
                             "carimbo vence tudo")
            self.assertEqual(leitura.valores["ano"], "1979",
                             "sem carimbo, o info vence o 1999 da pasta")


class TestMutirao(unittest.TestCase):
    """Passar uma vez no acervo inteiro, inclusive no que tem status antigo."""

    def _acervo(self, raiz: Path) -> None:
        montar_acervo(raiz)
        # status antigos, de outros fluxos
        marcar(raiz / "BSG-EdificioPiracicaba-AnteProjeto-1979", status="digitalizado")
        marcar(
            raiz / "F001 - ARM - Arnaldo Martino" / "P0002 - MICE 80 - 1980",
            status="sincronizado",
        )

    def test_fila_normal_ignora_status_desconhecido(self):
        with TemporaryDirectory() as tmp:
            raiz = Path(tmp)
            self._acervo(raiz)
            nomes = {p.nome for p in vigia.varrer(raiz, CFG)}
            self.assertNotIn("BSG-EdificioPiracicaba-AnteProjeto-1979", nomes)
            self.assertNotIn("P0002 - MICE 80 - 1980", nomes)

    def test_mutirao_pega_todos_independente_do_status(self):
        with TemporaryDirectory() as tmp:
            raiz = Path(tmp)
            self._acervo(raiz)
            cfg = Config(trabalhadores=1, consolidar_por_projeto=False,
                         processar_tudo_sem_fase=True)
            self.assertEqual(len(vigia.varrer(raiz, cfg)), 4)

    def test_mutirao_nao_repete_o_que_ja_tem_a_fase(self):
        with TemporaryDirectory() as tmp:
            raiz = Path(tmp)
            self._acervo(raiz)
            cfg = Config(trabalhadores=1, consolidar_por_projeto=False,
                         processar_tudo_sem_fase=True, pasta_vigiada=str(raiz))
            v = vigia.Vigia(cfg, ClienteFalso([resposta_padrao()]), raiz / "_estado")
            self.assertEqual(v.uma_rodada(), 4)
            self.assertEqual(v.uma_rodada(), 0, "segunda passada não refaz nada")
            self.assertEqual(vigia.varrer(raiz, cfg), [])

    def test_mutirao_preserva_o_status_antigo_no_arquivo(self):
        with TemporaryDirectory() as tmp:
            raiz = Path(tmp)
            self._acervo(raiz)
            cfg = Config(trabalhadores=1, consolidar_por_projeto=False,
                         processar_tudo_sem_fase=True)
            projeto = {p.nome: p for p in vigia.varrer(raiz, cfg)}[
                "BSG-EdificioPiracicaba-AnteProjeto-1979"
            ]
            vigia.processar(projeto, cfg, ClienteFalso([resposta_padrao()]))
            dados = json.loads((projeto.pasta / "status.json").read_text())
            self.assertEqual(dados["fase"], "organizado_v2")
            self.assertEqual(dados["status"], "campvision_concluido")

    def test_estimativa_nao_chama_a_api(self):
        from nucleo import acervo as mod_acervo

        with TemporaryDirectory() as tmp:
            raiz = Path(tmp)
            self._acervo(raiz)
            cfg = Config(trabalhadores=4, processar_tudo_sem_fase=True)
            e = mod_acervo.estimar(raiz, cfg)
            self.assertEqual(e["projetos"], 4)
            self.assertEqual(e["pranchas"], 11)  # 3 + 4 + 2 + 2
            self.assertGreater(e["custo_max"], e["custo_min"])
            self.assertGreater(e["horas"], 0)

    def test_estimativa_zerada_quando_tudo_ja_tem_fase(self):
        from nucleo import acervo as mod_acervo

        with TemporaryDirectory() as tmp:
            raiz = Path(tmp)
            self._acervo(raiz)
            cfg = Config(trabalhadores=1, processar_tudo_sem_fase=True)
            for projeto in vigia.descobrir(raiz, cfg):
                vigia.marcar_fase(projeto.pasta, cfg)
            e = mod_acervo.estimar(raiz, cfg)
            self.assertEqual((e["projetos"], e["pranchas"]), (0, 0))


class TestNaoPerderPrancha(unittest.TestCase):
    """Caso real: pastas de conteúdo com nome fora do padrão eram descartadas."""

    def test_pasta_de_conteudo_com_nome_estranho_e_lida(self):
        with TemporaryDirectory() as tmp:
            raiz = Path(tmp)
            projeto = marcar(raiz / "P0002 - paroquia Mae Salvador - 1973")
            base = projeto / "01 - Desenhos e Pranchas"
            imagens(base / "03 - Preview (JPG)", 2, "prev")
            imagens(base / "IGREJA PARÓQUIA MÃE DO SALVADOR", 3, "igr")
            imagens(base / "Sem titulo", 2, "sem")
            achado = vigia.descobrir(raiz, CFG)[0]
            nomes = {p.name for p in achado.pastas_imagens}
            self.assertIn("IGREJA PARÓQUIA MÃE DO SALVADOR", nomes)
            self.assertIn("Sem titulo", nomes)
            self.assertEqual(len(achado.arquivos(CFG)), 7, "nenhuma prancha some")

    def test_matriz_tiff_continua_ignorada(self):
        with TemporaryDirectory() as tmp:
            raiz = Path(tmp)
            projeto = marcar(raiz / "P0001 - Com Matriz - 1979")
            base = projeto / "01 - Desenhos e Pranchas"
            imagens(base / "03 - Preview (JPG)", 3, "prev")
            imagens(base / "01 - Arquivo Arquivístico (TIFF)", 3, "tif")
            achado = vigia.descobrir(raiz, CFG)[0]
            self.assertEqual([p.name for p in achado.pastas_imagens], ["03 - Preview (JPG)"])

    def test_pasta_de_nome_neutro_cheia_de_tif_e_tratada_como_matriz(self):
        """O nome não diz nada, mas o conteúdo diz: são .tif, é matriz."""
        with TemporaryDirectory() as tmp:
            raiz = Path(tmp)
            projeto = marcar(raiz / "P0003 - Neutro - 1980")
            imagens(projeto / "Originais", 2, "jpg_")
            pasta_tif = projeto / "Digitalizacao"
            pasta_tif.mkdir(parents=True)
            for i in range(3):
                prancha_falsa(pasta_tif / f"m{i}.jpg", 400, 300).rename(
                    pasta_tif / f"m{i}.tif"
                )
            achado = vigia.descobrir(raiz, CFG)[0]
            self.assertEqual([p.name for p in achado.pastas_imagens], ["Originais"])

    def test_so_matriz_disponivel_ainda_e_lida(self):
        with TemporaryDirectory() as tmp:
            raiz = Path(tmp)
            projeto = marcar(raiz / "P0004 - So Matriz - 1985")
            imagens(projeto / "01 - Arquivo Arquivístico (TIFF)", 3, "t")
            achado = vigia.descobrir(raiz, CFG)[0]
            self.assertEqual(len(achado.arquivos(CFG)), 3)

    def test_jpg_e_tif_lado_a_lado_no_formato_antigo(self):
        with TemporaryDirectory() as tmp:
            raiz = Path(tmp)
            projeto = marcar(raiz / "OCG-ForumDuartina-1961")
            imagens(projeto / "JPG", 4)
            imagens(projeto / "TIF", 4, "t")
            achado = vigia.descobrir(raiz, CFG)[0]
            self.assertEqual([p.name for p in achado.pastas_imagens], ["JPG"])


class TestRefazer(unittest.TestCase):
    def test_limpar_fase_devolve_a_fila_sem_perder_o_status(self):
        with TemporaryDirectory() as tmp:
            raiz = Path(tmp)
            projeto = marcar(raiz / "P0001 - Teste - 1970", status="digitalizado")
            imagens(projeto / "JPG", 2)
            cfg = Config(trabalhadores=1, consolidar_por_projeto=False,
                         processar_tudo_sem_fase=True)
            achado = vigia.varrer(raiz, cfg)[0]
            vigia.processar(achado, cfg, ClienteFalso([resposta_padrao()]))
            self.assertEqual(vigia.varrer(raiz, cfg), [])

            self.assertTrue(vigia.limpar_fase(projeto))
            self.assertEqual(len(vigia.varrer(raiz, cfg)), 1, "voltou para a fila")
            dados = json.loads((projeto / "status.json").read_text())
            self.assertNotIn("fase", dados)
            self.assertEqual(dados["status"], "campvision_concluido", "status preservado")

    def test_refazer_nao_repaga_prancha_ja_lida(self):
        with TemporaryDirectory() as tmp:
            raiz = Path(tmp)
            projeto = marcar(raiz / "P0002 - Teste - 1971")
            imagens(projeto / "JPG", 3)
            cfg = Config(trabalhadores=1, consolidar_por_projeto=False,
                         processar_tudo_sem_fase=True)
            cliente = ClienteFalso([resposta_padrao()])
            vigia.processar(vigia.varrer(raiz, cfg)[0], cfg, cliente)
            chamadas_primeira = len(cliente.chamadas)

            vigia.limpar_fase(projeto)
            imagens(projeto / "Sem titulo", 2, "novo")  # pasta que faltava
            cliente2 = ClienteFalso([resposta_padrao()])
            evento = vigia.processar(vigia.varrer(raiz, cfg)[0], cfg, cliente2)
            self.assertEqual(len(cliente2.chamadas), 2, "só as 2 novas foram à API")
            self.assertEqual(evento.pranchas, 5, "mas a planilha traz as 5")
            self.assertGreaterEqual(chamadas_primeira, 3)

    def test_limpar_fase_em_projeto_sem_fase_nao_faz_nada(self):
        with TemporaryDirectory() as tmp:
            raiz = Path(tmp)
            projeto = marcar(raiz / "P0003 - Sem Fase - 1972")
            self.assertFalse(vigia.limpar_fase(projeto))


class TestEstimativaCalibrada(unittest.TestCase):
    def test_usa_o_custo_medido_quando_ha_historico(self):
        from nucleo.acervo import _custo_medio_por_prancha
        from nucleo.eventos import Evento

        historico = [
            Evento(projeto="A", pranchas=40, custo_usd=0.80),
            Evento(projeto="B", pranchas=60, custo_usd=1.20),
        ]
        self.assertAlmostEqual(_custo_medio_por_prancha(historico), 0.02)

    def test_amostra_pequena_nao_e_usada(self):
        from nucleo.acervo import _custo_medio_por_prancha
        from nucleo.eventos import Evento

        self.assertEqual(
            _custo_medio_por_prancha([Evento(projeto="A", pranchas=3, custo_usd=0.06)]), 0.0
        )

    def test_projeto_que_falhou_nao_entra_na_media(self):
        from nucleo.acervo import _custo_medio_por_prancha
        from nucleo.eventos import Evento

        historico = [
            Evento(projeto="A", pranchas=40, custo_usd=0.80),
            Evento(projeto="B", pranchas=99, custo_usd=0.0, falha="SMB caiu"),
        ]
        self.assertAlmostEqual(_custo_medio_por_prancha(historico), 0.02)

    def test_estimativa_diz_de_onde_veio_o_numero(self):
        from nucleo import acervo as mod_acervo
        from nucleo.eventos import Evento

        with TemporaryDirectory() as tmp:
            raiz = Path(tmp)
            projeto = marcar(raiz / "P0001 - Novo - 1970")
            imagens(projeto / "JPG", 10)
            cfg = Config(processar_tudo_sem_fase=True)
            sem = mod_acervo.estimar(raiz, cfg)
            self.assertIn("sem histórico", sem["origem"])
            com = mod_acervo.estimar(
                raiz, cfg, [Evento(projeto="X", pranchas=50, custo_usd=1.00)]
            )
            self.assertIn("medido", com["origem"])
            self.assertAlmostEqual(com["custo_min"], 10 * 0.02 * 0.8, places=3)
