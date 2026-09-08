"""Testes do modo contínuo: prancha chegando enquanto o vigia roda."""

from __future__ import annotations

import os
import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from nucleo import vigia
from nucleo.config import Config
from tests.test_acervo_real import imagens, marcar
from tests.test_nucleo import ClienteFalso, prancha_falsa, resposta_padrao

AGORA = Config(espera_estabilidade_segundos=0, trabalhadores=1,
               consolidar_por_projeto=False)


def envelhecer(pasta: Path, segundos: int = 300) -> None:
    """Faz os arquivos parecerem antigos, como se a cópia tivesse terminado."""
    antigo = time.time() - segundos
    for arquivo in pasta.rglob("*.jpg"):
        os.utime(arquivo, (antigo, antigo))


class TestEstabilidadeDeArquivo(unittest.TestCase):
    def test_arquivo_recem_copiado_espera_a_proxima_varredura(self):
        with TemporaryDirectory() as tmp:
            raiz = Path(tmp)
            projeto = marcar(raiz / "P0001 - Chegando - 1970")
            imagens(projeto / "JPG", 3)  # acabaram de nascer
            cfg = Config(espera_estabilidade_segundos=45, trabalhadores=1)
            achado = vigia.descobrir(raiz, cfg)[0]
            self.assertEqual(achado.arquivos(cfg, so_estaveis=True), [],
                             "scanner pode ainda estar copiando")
            self.assertEqual(len(achado.arquivos(cfg)), 3)

    def test_arquivo_estabilizado_entra(self):
        with TemporaryDirectory() as tmp:
            raiz = Path(tmp)
            projeto = marcar(raiz / "P0001 - Pronto - 1970")
            imagens(projeto / "JPG", 3)
            envelhecer(projeto)
            cfg = Config(espera_estabilidade_segundos=45, trabalhadores=1)
            achado = vigia.descobrir(raiz, cfg)[0]
            self.assertEqual(len(achado.arquivos(cfg, so_estaveis=True)), 3)

    def test_le_so_as_estaveis_e_deixa_as_novas_para_depois(self):
        with TemporaryDirectory() as tmp:
            raiz = Path(tmp)
            projeto = marcar(raiz / "P0001 - Misto - 1970")
            imagens(projeto / "JPG", 4, "velha")
            envelhecer(projeto)
            imagens(projeto / "JPG", 2, "nova")  # chegando agora
            cfg = Config(espera_estabilidade_segundos=45, trabalhadores=1,
                         consolidar_por_projeto=False)
            achado = vigia.varrer(raiz, cfg)[0]
            evento = vigia.processar(achado, cfg, ClienteFalso([resposta_padrao()]))
            self.assertEqual(evento.pranchas, 4, "só as que terminaram de copiar")


class TestPranchaNova(unittest.TestCase):
    def _processar(self, raiz: Path, cfg: Config) -> None:
        for projeto in vigia.varrer(raiz, cfg):
            vigia.processar(projeto, cfg, ClienteFalso([resposta_padrao()]))

    def test_projeto_concluido_que_ganha_prancha_volta_a_fila(self):
        with TemporaryDirectory() as tmp:
            raiz = Path(tmp)
            projeto = marcar(raiz / "P0001 - Crescendo - 1970")
            imagens(projeto / "JPG", 3)
            self._processar(raiz, AGORA)
            self.assertEqual(vigia.varrer(raiz, AGORA), [], "nada novo, nada a fazer")

            imagens(projeto / "JPG", 2, "nova")
            fila = vigia.varrer(raiz, AGORA)
            self.assertEqual(len(fila), 1, "prancha nova recoloca o projeto na fila")

    def test_so_as_novas_vao_para_a_api(self):
        with TemporaryDirectory() as tmp:
            raiz = Path(tmp)
            projeto = marcar(raiz / "P0002 - Crescendo - 1971")
            imagens(projeto / "JPG", 5)
            self._processar(raiz, AGORA)

            imagens(projeto / "JPG", 2, "nova")
            cliente = ClienteFalso([resposta_padrao()])
            evento = vigia.processar(vigia.varrer(raiz, AGORA)[0], AGORA, cliente)
            self.assertEqual(len(cliente.chamadas), 2, "as 5 antigas não são repagas")
            self.assertEqual(evento.pranchas, 7, "mas a catalogação traz as 7")

    def test_pode_ser_desligado(self):
        with TemporaryDirectory() as tmp:
            raiz = Path(tmp)
            projeto = marcar(raiz / "P0003 - Congelado - 1972")
            imagens(projeto / "JPG", 2)
            cfg = Config(espera_estabilidade_segundos=0, trabalhadores=1,
                         consolidar_por_projeto=False,
                         reprocessar_se_houver_novas=False)
            self._processar(raiz, cfg)
            imagens(projeto / "JPG", 2, "nova")
            self.assertEqual(vigia.varrer(raiz, cfg), [])

    def test_projeto_nunca_lido_nao_conta_como_tendo_novas(self):
        with TemporaryDirectory() as tmp:
            raiz = Path(tmp)
            projeto = marcar(raiz / "P0004 - Virgem - 1973")
            imagens(projeto / "JPG", 2)
            achado = vigia.descobrir(raiz, AGORA)[0]
            self.assertFalse(achado.tem_pranchas_novas(AGORA))


class TestSaidaSoCSV(unittest.TestCase):
    def test_padrao_escreve_csv_e_nao_xlsx(self):
        with TemporaryDirectory() as tmp:
            raiz = Path(tmp)
            projeto = marcar(raiz / "P0001 - Saida - 1970")
            imagens(projeto / "JPG", 2)
            achado = vigia.varrer(raiz, AGORA)[0]
            vigia.processar(achado, AGORA, ClienteFalso([resposta_padrao()]))
            saida = projeto / "catalogacao"
            self.assertTrue((saida / "catalogacao.csv").exists())
            self.assertFalse((saida / "catalogacao.xlsx").exists())
            self.assertTrue((saida / "leituras.json").exists(),
                            "o JSON é sempre escrito: a raiz é remontada dele")

    def test_pode_desligar_a_saida_por_projeto(self):
        with TemporaryDirectory() as tmp:
            raiz = Path(tmp)
            projeto = marcar(raiz / "P0002 - Sem Saida - 1970")
            imagens(projeto / "JPG", 2)
            cfg = Config(espera_estabilidade_segundos=0, trabalhadores=1,
                         consolidar_por_projeto=False, escrever_por_projeto=False)
            vigia.processar(vigia.varrer(raiz, cfg)[0], cfg,
                            ClienteFalso([resposta_padrao()]))
            saida = projeto / "catalogacao"
            self.assertFalse((saida / "catalogacao.csv").exists())
            self.assertTrue((saida / "leituras.json").exists())

    def test_acervo_da_raiz_sai_em_csv(self):
        from nucleo import acervo

        with TemporaryDirectory() as tmp:
            raiz = Path(tmp)
            projeto = marcar(raiz / "P0001 - Raiz - 1970")
            imagens(projeto / "JPG", 2)
            cfg = Config(espera_estabilidade_segundos=0, trabalhadores=1,
                         consolidar_por_projeto=False, pasta_vigiada=str(raiz))
            vigia.processar(vigia.varrer(raiz, cfg)[0], cfg,
                            ClienteFalso([resposta_padrao()]))
            caminho, _, pranchas = acervo.escrever(raiz, cfg)
            self.assertEqual(caminho.suffix, ".csv")
            self.assertEqual(pranchas, 2)
            self.assertFalse((raiz / "_catalogacao" / "acervo.xlsx").exists())


if __name__ == "__main__":
    unittest.main()
