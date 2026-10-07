"""Testes das pistas de pasta — projeto, ano, fundo e divergência."""

from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from nucleo import caminho
from nucleo.caminho import PistaDePasta, aplicar, combinam, extrair
from nucleo.config import Config
from nucleo.esquema import Leitura

RAIZ = Path("/acervos")


def pista_de(relativo: str) -> PistaDePasta:
    return extrair(RAIZ / relativo, RAIZ)


class TestExtracao(unittest.TestCase):
    def test_ano_em_pasta_propria(self):
        p = pista_de("Fundo OCG/1968/TeatroDeSantos")
        self.assertEqual(p.projeto, "Teatro De Santos")
        self.assertEqual(p.ano, "1968")
        self.assertEqual(p.fundo, "Fundo OCG")

    def test_ano_embutido_no_nome(self):
        p = pista_de("OCG-TeatroDeSantos-1968")
        self.assertEqual(p.projeto, "Teatro De Santos")
        self.assertEqual(p.ano, "1968")
        self.assertEqual(p.fundo, "OCG")

    def test_prefixo_de_fundo_vira_fundo_nao_projeto(self):
        p = pista_de("SBU_Eletropaulo_CARMONA")
        self.assertEqual(p.fundo, "SBU")
        self.assertNotIn("SBU", p.projeto)

    def test_pasta_estrutural_nao_vira_projeto(self):
        p = pista_de("Fundo OCG/1968/TeatroDeSantos/JPG")
        self.assertEqual(p.projeto, "Teatro De Santos")
        p2 = pista_de("OCG/CasaBaeta/TIF")
        self.assertEqual(p2.projeto, "Casa Baeta")

    def test_ano_de_pasta_vence_ano_embutido_acima(self):
        p = pista_de("Acervo 1990/1968/CasaBaeta")
        self.assertEqual(p.ano, "1968")

    def test_codigo_de_digitalizacao_e_descartado_do_nome(self):
        p = pista_de("DEST3524 CasaDaPraia 1972")
        self.assertEqual(p.projeto, "Casa Da Praia")
        self.assertEqual(p.ano, "1972")

    def test_palavras_de_estrutura_nao_viram_fundo(self):
        p = pista_de("Acervo/Projetos/1972/Casa da Praia")
        self.assertEqual(p.projeto, "Casa da Praia")
        self.assertEqual(p.ano, "1972")
        self.assertEqual(p.fundo, "")

    def test_ano_implausivel_e_ignorado(self):
        p = pista_de("Casa 0350")
        self.assertEqual(p.ano, "")

    def test_sem_raiz_usa_so_o_nome_da_pasta(self):
        p = extrair(Path("/qualquer/lugar/CasaBaeta-1956"))
        self.assertEqual(p.projeto, "Casa Baeta")
        self.assertEqual(p.ano, "1956")

    def test_como_campos_omite_vazio(self):
        self.assertEqual(PistaDePasta(projeto="X").como_campos(), {"projeto": "X"})
        self.assertEqual(PistaDePasta().como_campos(), {})


class TestCombinam(unittest.TestCase):
    def test_grafias_diferentes_da_mesma_coisa(self):
        self.assertTrue(combinam("TEATRO DE SANTOS", "Teatro De Santos"))
        self.assertTrue(combinam("CASA DA PRAIA", "Casa Praia"))
        self.assertTrue(combinam("RESIDÊNCIA BAETA", "Casa Baeta"))

    def test_coisas_realmente_diferentes(self):
        self.assertFalse(combinam("TEATRO DE SANTOS", "EDIFÍCIO COPAN"))

    def test_vazio_nao_e_divergencia(self):
        self.assertTrue(combinam("", "qualquer coisa"))
        self.assertTrue(combinam("qualquer coisa", ""))


class TestAplicar(unittest.TestCase):
    def _leitura(self, **valores) -> Leitura:
        return Leitura(
            arquivo="p1.jpg",
            valores=dict(valores),
            confiancas={k: 0.9 for k in valores},
            carimbo_encontrado=True,
        )

    def test_preenche_campo_que_o_carimbo_nao_deu(self):
        leitura = self._leitura(projeto="TEATRO DE SANTOS")
        preenchidos, _ = aplicar([leitura], pista_de("OCG/1968/TeatroDeSantos"))
        self.assertEqual(preenchidos, 1)
        self.assertEqual(leitura.valores["ano"], "1968")
        self.assertIn("ano", leitura.campos_da_pasta)
        self.assertEqual(leitura.confiancas["ano"], 0.5)

    def test_nunca_sobrescreve_o_que_foi_lido(self):
        leitura = self._leitura(projeto="TEATRO DE SANTOS", ano="1967")
        aplicar([leitura], pista_de("OCG/1968/TeatroDeSantos"))
        self.assertEqual(leitura.valores["ano"], "1967", "o carimbo sempre vence a pasta")
        self.assertNotIn("ano", leitura.campos_da_pasta)

    def test_acusa_divergencia_entre_carimbo_e_pasta(self):
        leitura = self._leitura(projeto="EDIFÍCIO COPAN")
        _, divergencias = aplicar([leitura], pista_de("OCG/1968/TeatroDeSantos"))
        self.assertEqual(divergencias, 1)
        self.assertIn("projeto", leitura.divergencias)

    def test_concordancia_nao_gera_divergencia(self):
        leitura = self._leitura(projeto="TEATRO DE SANTOS", ano="1968")
        _, divergencias = aplicar([leitura], pista_de("OCG/1968/TeatroDeSantos"))
        self.assertEqual(divergencias, 0)
        self.assertEqual(leitura.divergencias, [])

    def test_guarda_a_pista_mesmo_sem_preencher_nada(self):
        leitura = self._leitura(projeto="TEATRO DE SANTOS", ano="1968")
        aplicar([leitura], pista_de("Fundo OCG/1968/TeatroDeSantos"))
        self.assertEqual(leitura.pista_fundo, "Fundo OCG")
        self.assertEqual(leitura.pista_ano, "1968")

    def test_pode_conferir_sem_preencher(self):
        leitura = self._leitura(projeto="TEATRO DE SANTOS")
        preenchidos, _ = aplicar(
            [leitura], pista_de("OCG/1968/TeatroDeSantos"), preencher_faltantes=False
        )
        self.assertEqual(preenchidos, 0)
        self.assertEqual(leitura.valores.get("ano", ""), "")
        self.assertEqual(leitura.pista_ano, "1968", "a pista continua registrada")

    def test_prancha_sem_carimbo_fica_com_projeto_e_ano_da_pasta(self):
        vazia = Leitura(arquivo="ilegivel.jpg", valores={}, confiancas={})
        preenchidos, _ = aplicar([vazia], pista_de("OCG/1968/TeatroDeSantos"))
        self.assertEqual(preenchidos, 2)
        self.assertEqual(vazia.valores["projeto"], "Teatro De Santos")
        self.assertEqual(vazia.valores["ano"], "1968")
        self.assertEqual(sorted(vazia.campos_da_pasta), ["ano", "projeto"])


class TestIntegracaoComOVigia(unittest.TestCase):
    def test_pistas_chegam_na_planilha(self):
        from nucleo import vigia
        from tests.test_acervo import com_imagens
        from tests.test_nucleo import ClienteFalso, resposta_padrao

        with TemporaryDirectory() as tmp:
            raiz = Path(tmp)
            com_imagens(raiz / "Fundo OCG" / "1968" / "TeatroDeSantos", 2)
            cfg = Config(espera_estabilidade_segundos=0, trabalhadores=1,
                         consolidar_por_projeto=False, pasta_vigiada=str(raiz))
            projeto = vigia.varrer(raiz, cfg)[0]
            # a resposta falsa diz CASA DA PRAIA: diverge da pasta TeatroDeSantos
            vigia.processar(projeto, cfg, ClienteFalso([resposta_padrao()]))
            from tests.test_acervo import ler_csv

            linhas = ler_csv(projeto.pasta / "catalogacao" / "catalogacao.csv")
            cabecalho = linhas[0]
            self.assertIn("Fundo (pasta)", cabecalho)
            self.assertIn("carimbo ≠ pasta", linhas[1][cabecalho.index("Divergência")])
            self.assertEqual(linhas[1][cabecalho.index("Fundo (pasta)")], "Fundo OCG")
            self.assertEqual(linhas[1][cabecalho.index("Revisar")], "sim")

    def test_pode_ser_desligado(self):
        from nucleo import vigia
        from tests.test_acervo import com_imagens
        from tests.test_nucleo import ClienteFalso, resposta_padrao

        with TemporaryDirectory() as tmp:
            raiz = Path(tmp)
            com_imagens(raiz / "Fundo OCG" / "1968" / "TeatroDeSantos", 1)
            cfg = Config(espera_estabilidade_segundos=0, 
                trabalhadores=1, consolidar_por_projeto=False,
                usar_pasta_como_pista=False, pasta_vigiada=str(raiz),
            )
            projeto = vigia.varrer(raiz, cfg)[0]
            vigia.processar(projeto, cfg, ClienteFalso([resposta_padrao()]))
            leituras = __import__(
                "nucleo.planilha", fromlist=["ler_json"]
            ).ler_json(projeto.pasta / "catalogacao" / "leituras.json")
            self.assertEqual(leituras[0].pista_fundo, "")


class TestProveniencia(unittest.TestCase):
    def test_a_pasta_nunca_entra_no_prompt_do_modelo(self):
        """A instrução do leitor não pode conter nada de caminho de pasta."""
        from nucleo import visao

        texto = (visao.INSTRUCOES + visao.INSTRUCOES_RECORTE).lower()
        for proibido in ("pasta", "diretório", "caminho do arquivo", "nome do arquivo"):
            self.assertNotIn(proibido, texto)

    def test_leitor_recebe_so_a_imagem(self):
        from nucleo.visao import LeitorDeCarimbo
        from tests.test_nucleo import ClienteFalso, prancha_falsa, resposta_padrao

        with TemporaryDirectory() as tmp:
            alvo = prancha_falsa(Path(tmp) / "TeatroDeSantos-1968-003.jpg", 800, 600)
            cliente = ClienteFalso([resposta_padrao()])
            LeitorDeCarimbo(Config(espera_estabilidade_segundos=0), cliente).ler(alvo)
            enviado = str(cliente.chamadas[0]["mensagens"])
            self.assertNotIn("TeatroDeSantos", enviado, "o nome do arquivo não pode vazar")
            self.assertNotIn("1968", enviado)


if __name__ == "__main__":
    unittest.main()


class TestUrlSmb(unittest.TestCase):
    def test_traduz_a_url_do_servidor_da_camp(self):
        from nucleo.caminho import de_url_smb

        url = ("smb://Server-Camp._smb._tcp.local/Backup Servidor CAMP/"
               "Arquivos/99 - Saida Scanner Contex HD")
        self.assertEqual(
            de_url_smb(url, "/Volumes"),
            Path("/Volumes/Backup Servidor CAMP/Arquivos/99 - Saida Scanner Contex HD"),
        )

    def test_descarta_o_host_e_mantem_o_share(self):
        from nucleo.caminho import de_url_smb

        self.assertEqual(
            de_url_smb("smb://qualquer-host/Share/sub", "/Volumes"), Path("/Volumes/Share/sub")
        )
        # no Linux, com o share montado por fstab, a raiz é outra
        self.assertEqual(
            de_url_smb("smb://qualquer-host/Share/sub", "/mnt"), Path("/mnt/Share/sub")
        )

    def test_desfaz_percent_encoding(self):
        from nucleo.caminho import de_url_smb

        self.assertEqual(
            de_url_smb("smb://h/Backup%20Servidor%20CAMP/Arquivos", "/Volumes"),
            Path("/Volumes/Backup Servidor CAMP/Arquivos"),
        )

    def test_caminho_normal_passa_direto(self):
        from nucleo.caminho import de_url_smb

        self.assertEqual(de_url_smb("/Volumes/acervos"), Path("/Volumes/acervos"))

    def test_url_sem_share_nao_quebra(self):
        from nucleo.caminho import de_url_smb

        self.assertEqual(de_url_smb("smb://so-o-host", "/Volumes"), Path("/Volumes"))
        self.assertEqual(de_url_smb("smb://so-o-host", "/mnt"), Path("/mnt"))


class TestURLSMB(unittest.TestCase):
    def test_traduz_url_do_finder(self):
        alvo = caminho.de_url_smb(
            "smb://Server-Camp._smb._tcp.local/Backup Servidor CAMP/Arquivos/99 - Saida",
            "/Volumes",
        )
        self.assertEqual(str(alvo), "/Volumes/Backup Servidor CAMP/Arquivos/99 - Saida")

    def test_repara_caminho_estragado_por_versao_antiga(self):
        """O bug real: smb:// virou caminho relativo colado no cwd."""
        estragado = (
            "/Users/mulletsp/Downloads/campvision2/smb:/Server-Camp._smb._tcp.local/"
            "Backup Servidor CAMP/Arquivos/99 - Saida Scanner Contex HD"
        )
        self.assertEqual(
            str(caminho.de_url_smb(estragado, "/Volumes")),
            "/Volumes/Backup Servidor CAMP/Arquivos/99 - Saida Scanner Contex HD",
        )

    def test_percent_encoding(self):
        alvo = caminho.de_url_smb("smb://host/Backup%20Servidor%20CAMP/Arquivos", "/Volumes")
        self.assertEqual(str(alvo), "/Volumes/Backup Servidor CAMP/Arquivos")

    def test_caminho_comum_passa_direto(self):
        self.assertEqual(str(caminho.de_url_smb("/Volumes/acervos")), "/Volumes/acervos")

    def test_config_conserta_caminho_guardado(self):
        import json as _json

        with TemporaryDirectory() as tmp:
            arquivo = Path(tmp) / "config.json"
            arquivo.write_text(_json.dumps({
                "pasta_vigiada": "/Users/x/app/smb:/Server/Backup Servidor CAMP/Arquivos",
                "raiz_de_montagem": "/Volumes",
            }), encoding="utf-8")
            cfg = Config.carregar(arquivo)
            self.assertEqual(
                cfg.pasta_vigiada, "/Volumes/Backup Servidor CAMP/Arquivos"
            )

    def test_config_bom_nao_e_mexido(self):
        import json as _json

        with TemporaryDirectory() as tmp:
            arquivo = Path(tmp) / "config.json"
            arquivo.write_text(_json.dumps({"pasta_vigiada": "/Volumes/acervos"}), encoding="utf-8")
            self.assertEqual(Config.carregar(arquivo).pasta_vigiada, "/Volumes/acervos")


class TestDiagnostico(unittest.TestCase):
    def test_aponta_caminho_malformado(self):
        from nucleo.vigia import diagnosticar_pasta

        mensagem = diagnosticar_pasta(Path("/Users/x/app/smb:/Server/Share"))
        self.assertIn("malformado", mensagem)
        self.assertIn("--pasta", mensagem)

    def test_aponta_share_nao_montado(self):
        from nucleo.vigia import diagnosticar_pasta

        mensagem = diagnosticar_pasta(
            Path("/Volumes/Nao Montado XYZ/Arquivos"), Path("/Volumes")
        )
        self.assertIn("não está montado", mensagem)

    def test_diagnostico_no_linux_fala_de_mount(self):
        from nucleo.vigia import diagnosticar_pasta

        mensagem = diagnosticar_pasta(Path("/mnt/qnap-inexistente/acervos"), Path("/mnt"))
        self.assertIn("não está montado", mensagem)

    def test_caminho_ja_montado_passa_direto(self):
        self.assertEqual(str(caminho.de_url_smb("/mnt/qnap/acervos")), "/mnt/qnap/acervos")
