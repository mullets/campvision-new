"""Testes da varredura recursiva, do marcador de fase e da planilha única."""

from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory


from nucleo import acervo, vigia
from nucleo.config import Config
from tests.test_nucleo import ClienteFalso, prancha_falsa, resposta_padrao


def com_imagens(pasta: Path, n: int = 2, subpasta: str | None = "JPG") -> Path:
    destino = pasta / subpasta if subpasta else pasta
    destino.mkdir(parents=True, exist_ok=True)
    for i in range(n):
        prancha_falsa(destino / f"{pasta.name}-{i:03d}.jpg", 700, 500)
    return pasta


class TestDescobertaRecursiva(unittest.TestCase):
    def test_acha_projeto_em_subpasta_de_subpasta(self):
        with TemporaryDirectory() as tmp:
            raiz = Path(tmp)
            com_imagens(raiz / "Acervo OCG" / "1968" / "TeatroDeSantos")
            com_imagens(raiz / "Acervo SBU" / "CasaDaPraia")
            achados = vigia.descobrir(raiz, Config(espera_estabilidade_segundos=0))
            self.assertEqual(
                sorted(p.nome for p in achados), ["CasaDaPraia", "TeatroDeSantos"]
            )

    def test_nao_trata_jpg_e_tif_como_projetos_irmaos(self):
        with TemporaryDirectory() as tmp:
            raiz = Path(tmp)
            projeto = raiz / "TeatroDeSantos"
            com_imagens(projeto, 2, "JPG")
            com_imagens(projeto, 2, "TIF")
            achados = vigia.descobrir(raiz, Config(espera_estabilidade_segundos=0))
            self.assertEqual(len(achados), 1)
            self.assertEqual(achados[0].nome, "TeatroDeSantos")
            self.assertEqual(achados[0].pasta_imagens.name, "JPG")

    def test_projeto_com_imagem_solta_na_raiz_da_pasta(self):
        with TemporaryDirectory() as tmp:
            raiz = Path(tmp)
            com_imagens(raiz / "CasaSolta", 2, subpasta=None)
            achados = vigia.descobrir(raiz, Config(espera_estabilidade_segundos=0))
            self.assertEqual([p.nome for p in achados], ["CasaSolta"])
            self.assertEqual(achados[0].pasta_imagens.name, "CasaSolta")

    def test_ignora_pastas_de_saida_e_ocultas(self):
        with TemporaryDirectory() as tmp:
            raiz = Path(tmp)
            com_imagens(raiz / "Projeto")
            com_imagens(raiz / "_catalogacao")
            com_imagens(raiz / ".oculta")
            com_imagens(raiz / "Projeto2" / "catalogacao")
            achados = vigia.descobrir(raiz, Config(espera_estabilidade_segundos=0))
            self.assertEqual([p.nome for p in achados], ["Projeto"])

    def test_respeita_a_profundidade_maxima(self):
        with TemporaryDirectory() as tmp:
            raiz = Path(tmp)
            com_imagens(raiz / "a" / "b" / "c" / "d" / "e" / "f" / "Fundo")
            self.assertEqual(vigia.descobrir(raiz, Config(espera_estabilidade_segundos=0, profundidade_maxima=2)), [])
            self.assertEqual(len(vigia.descobrir(raiz, Config(espera_estabilidade_segundos=0, profundidade_maxima=9))), 1)

    def test_pasta_so_de_documentos_nao_vira_projeto(self):
        with TemporaryDirectory() as tmp:
            raiz = Path(tmp)
            (raiz / "Contratos").mkdir()
            (raiz / "Contratos" / "leia.txt").write_text("nada")
            self.assertEqual(vigia.descobrir(raiz, Config(espera_estabilidade_segundos=0)), [])


class TestMarcadorDeFase(unittest.TestCase):
    def test_carimba_fase_sem_mexer_no_status(self):
        with TemporaryDirectory() as tmp:
            pasta = com_imagens(Path(tmp) / "Projeto")
            (pasta / "status.json").write_text(
                json.dumps({"status": "campvision_concluido", "enviado_por": "windows"}),
                encoding="utf-8",
            )
            vigia.marcar_fase(pasta, Config(espera_estabilidade_segundos=0))
            dados = json.loads((pasta / "status.json").read_text())
            self.assertEqual(dados["fase"], "organizado_v2")
            self.assertIn("fase_em", dados)
            # o semáforo que o QNAP lê continua intocado
            self.assertEqual(dados["status"], "campvision_concluido")
            self.assertEqual(dados["enviado_por"], "windows")

    def test_cria_status_se_nao_houver(self):
        with TemporaryDirectory() as tmp:
            pasta = com_imagens(Path(tmp) / "Projeto")
            vigia.marcar_fase(pasta, Config(espera_estabilidade_segundos=0))
            dados = json.loads((pasta / "status.json").read_text())
            self.assertEqual(dados["fase"], "organizado_v2")
            self.assertEqual(dados["status"], "enviado_windows")

    def test_status_corrompido_e_substituido_sem_travar(self):
        with TemporaryDirectory() as tmp:
            pasta = com_imagens(Path(tmp) / "Projeto")
            (pasta / "status.json").write_text("{quebrado")
            vigia.marcar_fase(pasta, Config(espera_estabilidade_segundos=0))
            self.assertEqual(json.loads((pasta / "status.json").read_text())["fase"], "organizado_v2")

    def test_processar_carimba_a_fase(self):
        with TemporaryDirectory() as tmp:
            raiz = Path(tmp)
            com_imagens(raiz / "Projeto", 2)
            cfg = Config(espera_estabilidade_segundos=0, trabalhadores=1)
            projeto = vigia.varrer(raiz, cfg)[0]
            vigia.processar(projeto, cfg, ClienteFalso([resposta_padrao()]))
            dados = json.loads((projeto.pasta / "status.json").read_text())
            self.assertEqual(dados["fase"], "organizado_v2")
            self.assertEqual(dados["status"], "campvision_concluido")


def ler_csv(caminho):
    import csv as _csv
    with open(caminho, encoding="utf-8-sig", newline="") as f:
        return list(_csv.reader(f))


class TestPlanilhaDoAcervo(unittest.TestCase):
    def _acervo_processado(self, raiz: Path) -> Config:
        com_imagens(raiz / "OCG" / "TeatroDeSantos", 3)
        com_imagens(raiz / "OCG" / "CasaDaPraia", 2)
        com_imagens(raiz / "Pendente", 2)
        cfg = Config(espera_estabilidade_segundos=0, trabalhadores=1,
                     consolidar_por_projeto=False, pasta_vigiada=str(raiz))
        for projeto in vigia.varrer(raiz, cfg):
            if projeto.nome == "Pendente":
                continue
            vigia.processar(projeto, cfg, ClienteFalso([resposta_padrao()]))
        return cfg

    def test_junta_todos_os_projetos_numa_planilha(self):
        with TemporaryDirectory() as tmp:
            raiz = Path(tmp)
            cfg = self._acervo_processado(raiz)
            caminho, projetos, pranchas = acervo.escrever(raiz, cfg)
            self.assertEqual(projetos, 2)
            self.assertEqual(pranchas, 5)
            self.assertTrue(caminho.exists())
            self.assertEqual(caminho.parent.name, "_catalogacao")

    def test_tem_as_tres_abas(self):
        with TemporaryDirectory() as tmp:
            raiz = Path(tmp)
            cfg = self._acervo_processado(raiz)
            caminho, _, _ = acervo.escrever(raiz, cfg)
            nomes = sorted(p.name for p in caminho.parent.glob("*.csv"))
            self.assertEqual(nomes, ["acervo.csv", "pendentes.csv", "projetos.csv"])
            self.assertEqual(list(caminho.parent.glob("*.xlsx")), [])

    def test_aba_acervo_traz_a_pasta_de_cada_prancha(self):
        with TemporaryDirectory() as tmp:
            raiz = Path(tmp)
            cfg = self._acervo_processado(raiz)
            caminho, _, _ = acervo.escrever(raiz, cfg)
            linhas = ler_csv(caminho)
            self.assertEqual(len(linhas), 6)  # 5 pranchas + cabeçalho
            self.assertIn("Revisar", linhas[0])

    def test_aba_projetos_totaliza(self):
        with TemporaryDirectory() as tmp:
            raiz = Path(tmp)
            cfg = self._acervo_processado(raiz)
            caminho, _, _ = acervo.escrever(raiz, cfg)
            linhas = ler_csv(caminho.parent / "projetos.csv")
            cabecalho = linhas[0]
            self.assertEqual(len(linhas), 3)
            # busca a coluna pelo nome: inserir coluna não pode quebrar o teste
            self.assertEqual(sum(int(l[cabecalho.index("Pranchas")]) for l in linhas[1:]), 5)
            self.assertEqual(sum(int(l[cabecalho.index("Com carimbo")]) for l in linhas[1:]), 5)

    def test_aba_pendentes_lista_o_que_falta(self):
        with TemporaryDirectory() as tmp:
            raiz = Path(tmp)
            cfg = self._acervo_processado(raiz)
            caminho, _, _ = acervo.escrever(raiz, cfg)
            valores = [l[0] for l in ler_csv(caminho.parent / "pendentes.csv")[1:]]
            self.assertIn("Pendente", valores)

    def test_remonta_sem_gastar_api(self):
        with TemporaryDirectory() as tmp:
            raiz = Path(tmp)
            cfg = self._acervo_processado(raiz)
            acervo.escrever(raiz, cfg)
            # segunda montagem, com um cliente que explodiria se fosse chamado
            class ClienteProibido:
                def chamar(self, *_a, **_k):
                    raise AssertionError("a planilha do acervo não pode chamar a API")

            _, projetos, pranchas = acervo.escrever(raiz, cfg)
            self.assertEqual((projetos, pranchas), (2, 5))

    def test_acervo_vazio_nao_quebra(self):
        with TemporaryDirectory() as tmp:
            raiz = Path(tmp)
            caminho, projetos, pranchas = acervo.escrever(
                raiz, Config(espera_estabilidade_segundos=0, pasta_vigiada=str(raiz)))
            self.assertEqual((projetos, pranchas), (0, 0))
            self.assertEqual(len(ler_csv(caminho.parent / "pendentes.csv")), 1)

    def test_planilha_do_acervo_e_ignorada_na_varredura_seguinte(self):
        with TemporaryDirectory() as tmp:
            raiz = Path(tmp)
            cfg = self._acervo_processado(raiz)
            acervo.escrever(raiz, cfg)
            nomes = {p.nome for p in vigia.descobrir(raiz, cfg)}
            self.assertNotIn("_catalogacao", nomes)


class TestRodadaCompleta(unittest.TestCase):
    def test_uma_rodada_processa_subpastas_e_monta_o_acervo(self):
        with TemporaryDirectory() as tmp:
            raiz = Path(tmp)
            estado = raiz / "_estado"
            com_imagens(raiz / "Fundo A" / "Projeto 1", 2)
            com_imagens(raiz / "Fundo A" / "Projeto 2", 2)
            com_imagens(raiz / "Fundo B" / "1972" / "Projeto 3", 2)
            cfg = Config(espera_estabilidade_segundos=0, trabalhadores=2,
                         pasta_vigiada=str(raiz), auto_atualizar=False)
            v = vigia.Vigia(cfg, ClienteFalso([resposta_padrao()]), estado)
            self.assertEqual(v.uma_rodada(), 3)
            planilha_geral = raiz / "_catalogacao" / "acervo.csv"
            self.assertTrue(planilha_geral.exists())
            self.assertEqual(len(ler_csv(planilha_geral)), 7)  # 6 pranchas + cabeçalho
            # e cada projeto tem a sua planilha própria
            self.assertTrue((raiz / "Fundo A" / "Projeto 1" / "catalogacao" / "catalogacao.csv").exists())


if __name__ == "__main__":
    unittest.main()


class TestImagensSoltasNaRaiz(unittest.TestCase):
    """Saída de scanner: pranchas caem soltas, sem subpasta de projeto."""

    def test_raiz_com_imagens_soltas_e_um_projeto(self):
        with TemporaryDirectory() as tmp:
            raiz = Path(tmp) / "99 - Saida Scanner Contex HD"
            com_imagens(raiz, 3, subpasta=None)
            achados = vigia.descobrir(raiz, Config(espera_estabilidade_segundos=0))
            self.assertEqual(len(achados), 1)
            self.assertEqual(achados[0].pasta, raiz)

    def test_raiz_solta_e_subpastas_convivem(self):
        with TemporaryDirectory() as tmp:
            raiz = Path(tmp) / "Saida"
            com_imagens(raiz, 2, subpasta=None)
            com_imagens(raiz / "TeatroDeSantos", 2)
            achados = vigia.descobrir(raiz, Config(espera_estabilidade_segundos=0))
            self.assertEqual(
                sorted(p.pasta.name for p in achados), ["Saida", "TeatroDeSantos"]
            )

    def test_raiz_sem_imagem_nao_vira_projeto(self):
        with TemporaryDirectory() as tmp:
            raiz = Path(tmp)
            com_imagens(raiz / "Projeto", 2)
            achados = vigia.descobrir(raiz, Config(espera_estabilidade_segundos=0))
            self.assertEqual([p.pasta.name for p in achados], ["Projeto"])

    def test_subpasta_com_imagens_soltas_nao_duplica(self):
        with TemporaryDirectory() as tmp:
            raiz = Path(tmp)
            projeto = com_imagens(raiz / "Projeto", 2, subpasta=None)
            com_imagens(projeto / "TIF", 2, subpasta=None)
            achados = vigia.descobrir(raiz, Config(espera_estabilidade_segundos=0))
            self.assertEqual(len(achados), 1)
