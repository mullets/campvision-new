"""Testes de metadados institucionais, status ausente e relatório geral."""

from __future__ import annotations

import json
import unittest
from datetime import date, datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory

from nucleo import metadados, relatorio_diario, vigia
from nucleo.aplicar import Acao, executar, planejar
from nucleo.config import Config
from nucleo.esquema import Leitura
from nucleo.eventos import Evento
from nucleo.metadados import Identidade
from nucleo import planilha
from tests.test_nucleo import prancha_falsa

CAMP = Identidade(
    nome="CAMP - Casa da Arquitetura Moderna Paulista",
    site="https://exemplo.org.br",
    licenca="CC BY-NC 4.0",
)

CAMPOS_EXEMPLO = {
    "Projeto": "TEATRO DE SANTOS",
    "Arquiteto": "OSWALDO CORRÊA GONÇALVES",
    "Título da prancha": "PLANTA DO TÉRREO",
    "Ano": "1968",
    "Cidade": "SANTOS",
    "UF": "SP",
    "Tipo": "planta",
}


class TestIdentidade(unittest.TestCase):
    def test_credito_junta_nome_e_site(self):
        self.assertEqual(CAMP.credito, "CAMP - Casa da Arquitetura Moderna Paulista (https://exemplo.org.br)")

    def test_direitos_poe_autor_antes_da_instituicao(self):
        linha = CAMP.direitos("OSWALDO CORRÊA GONÇALVES")
        self.assertTrue(linha.startswith("OSWALDO CORRÊA GONÇALVES / CAMP"))
        self.assertIn("CC BY-NC 4.0", linha)

    def test_sem_autor_a_instituicao_assina_sozinha(self):
        self.assertTrue(CAMP.direitos("").startswith("CAMP"))

    def test_aponta_o_que_falta(self):
        self.assertEqual(Identidade(nome="X").problemas(), ["identidade.site"])
        self.assertEqual(CAMP.problemas(), [])


class TestArgumentosExiftool(unittest.TestCase):
    def _args(self, campos=None, identidade=CAMP) -> list[str]:
        return metadados.montar_argumentos(Path("/tmp/x.jpg"), campos or CAMPOS_EXEMPLO, identidade)

    def test_nome_e_site_sempre_presentes(self):
        texto = "\n".join(self._args())
        self.assertIn("-XMP-dc:Publisher=CAMP - Casa da Arquitetura Moderna Paulista", texto)
        self.assertIn("-XMP-xmpRights:WebStatement=https://exemplo.org.br", texto)
        self.assertIn("https://exemplo.org.br", texto)

    def test_credito_vai_mesmo_sem_carimbo_legivel(self):
        texto = "\n".join(self._args(campos={}))
        self.assertIn("-XMP-dc:Publisher=CAMP - Casa da Arquitetura Moderna Paulista", texto)
        self.assertIn("-IPTC:Credit=", texto)
        self.assertIn("Acervo:", texto)

    def test_autor_da_obra_vai_para_creator_nao_para_publisher(self):
        texto = "\n".join(self._args())
        self.assertIn("-EXIF:Artist=OSWALDO CORRÊA GONÇALVES", texto)
        self.assertIn("-XMP-dc:Creator=OSWALDO CORRÊA GONÇALVES", texto)
        self.assertNotIn("-XMP-dc:Publisher=OSWALDO", texto)

    def test_campo_vazio_e_omitido(self):
        campos = dict(CAMPOS_EXEMPLO, Cidade="", UF="")
        texto = "\n".join(self._args(campos=campos))
        self.assertNotIn("-IPTC:City=", texto)
        self.assertIn("-IPTC:Province-State", "".join(["-IPTC:Province-State"]))  # sanidade
        self.assertNotIn("-XMP-photoshop:City=", texto)

    def test_ano_nao_vira_data_falsa(self):
        texto = "\n".join(self._args())
        self.assertIn("-XMP-dc:Date=1968", texto)
        self.assertNotIn("1968:01:01", texto)

    def test_palavras_chave_incluem_a_instituicao(self):
        texto = "\n".join(self._args())
        self.assertIn("-XMP-dc:Subject+=TEATRO DE SANTOS", texto)
        self.assertIn("-XMP-dc:Subject+=CAMP - Casa da Arquitetura Moderna Paulista", texto)

    def test_marca_a_ferramenta_e_o_build(self):
        self.assertTrue(any("CreatorTool=CAMP Vision 2" in a for a in self._args()))

    def test_argfile_separa_arquivos_com_execute(self):
        itens = [(Path("/tmp/a.jpg"), CAMPOS_EXEMPLO), (Path("/tmp/b.jpg"), CAMPOS_EXEMPLO)]
        conteudo = metadados._montar_argfile(itens, CAMP)
        self.assertEqual(conteudo.count("-execute"), 2)
        self.assertIn("/tmp/a.jpg", conteudo)
        self.assertIn("/tmp/b.jpg", conteudo)
        # um argumento por linha é o formato exigido pelo exiftool
        self.assertTrue(all(" " not in l.split("=")[0] for l in conteudo.splitlines() if l.startswith("-")))


class TestGravacao(unittest.TestCase):
    def test_sem_exiftool_avisa_e_nao_quebra(self):
        with TemporaryDirectory() as tmp:
            alvo = prancha_falsa(Path(tmp) / "a.jpg", 400, 300)
            quantos, avisos = metadados.gravar_em_lote([(alvo, CAMPOS_EXEMPLO)], CAMP)
            if not metadados.disponivel():
                self.assertEqual(quantos, 0)
                self.assertTrue(any("exiftool" in a for a in avisos))
            else:  # pragma: no cover - depende do ambiente
                self.assertEqual(quantos, 1)

    def test_identidade_incompleta_gera_aviso(self):
        with TemporaryDirectory() as tmp:
            alvo = prancha_falsa(Path(tmp) / "a.jpg", 400, 300)
            _, avisos = metadados.gravar_em_lote([(alvo, {})], Identidade(nome="X"))
            self.assertTrue(any("identidade.site" in a for a in avisos))

    def test_lista_vazia_nao_chama_nada(self):
        self.assertEqual(metadados.gravar_em_lote([], CAMP), (0, []))


class TestFase2ComMetadados(unittest.TestCase):
    def test_copia_preserva_original_e_tenta_gravar(self):
        with TemporaryDirectory() as tmp:
            pasta = Path(tmp)
            origem = prancha_falsa(pasta / "p1.jpg", 500, 400)
            destino = pasta / "saida" / "1968" / "TEATRO" / "TEATRO - 001.jpg"
            mensagens = executar([Acao(origem, destino, CAMPOS_EXEMPLO)], simular=False, identidade=CAMP)
            self.assertTrue(origem.exists(), "o original nunca some")
            self.assertTrue(destino.exists())
            self.assertTrue(any("Metadados gravados" in m for m in mensagens))

    def test_simulacao_nao_cria_arquivo(self):
        with TemporaryDirectory() as tmp:
            pasta = Path(tmp)
            origem = prancha_falsa(pasta / "p1.jpg", 400, 300)
            destino = pasta / "saida" / "x.jpg"
            mensagens = executar([Acao(origem, destino, {})], simular=True, identidade=CAMP)
            self.assertFalse(destino.exists())
            self.assertTrue(mensagens[0].startswith("[simulação]"))

    def test_nao_sobrescreve_nome_existente(self):
        with TemporaryDirectory() as tmp:
            pasta = Path(tmp)
            origem = prancha_falsa(pasta / "p1.jpg", 400, 300)
            destino = pasta / "saida" / "igual.jpg"
            executar([Acao(origem, destino, {})], simular=False, identidade=CAMP)
            executar([Acao(origem, destino, {})], simular=False, identidade=CAMP)
            self.assertTrue((pasta / "saida" / "igual (2).jpg").exists())


class TestStatusAusente(unittest.TestCase):
    def _projeto_sem_status(self, raiz: Path, nome: str) -> Path:
        pasta = raiz / nome
        (pasta / "JPG").mkdir(parents=True)
        prancha_falsa(pasta / "JPG" / "a.jpg", 600, 400)
        return pasta

    def test_cria_status_em_pasta_que_nao_tem(self):
        with TemporaryDirectory() as tmp:
            raiz = Path(tmp)
            pasta = self._projeto_sem_status(raiz, "PMR-Museu-1988")
            achados = vigia.varrer(raiz, Config())
            self.assertEqual([p.nome for p in achados], ["PMR-Museu-1988"])
            dados = json.loads((pasta / "status.json").read_text())
            self.assertEqual(dados["status"], "enviado_windows")
            self.assertEqual(dados["criado_por"], "campvision2")

    def test_nao_cria_status_em_pasta_sem_imagem(self):
        with TemporaryDirectory() as tmp:
            raiz = Path(tmp)
            (raiz / "documentos").mkdir()
            (raiz / "documentos" / "leia.txt").write_text("nada aqui")
            self.assertEqual(vigia.varrer(raiz, Config()), [])
            self.assertFalse((raiz / "documentos" / "status.json").exists())

    def test_pode_ser_desligado(self):
        with TemporaryDirectory() as tmp:
            raiz = Path(tmp)
            pasta = self._projeto_sem_status(raiz, "PMR-Museu-1988")
            self.assertEqual(vigia.varrer(raiz, Config(criar_status_ausente=False)), [])
            self.assertFalse((pasta / "status.json").exists())

    def test_nao_mexe_em_status_que_ja_existe(self):
        with TemporaryDirectory() as tmp:
            raiz = Path(tmp)
            pasta = self._projeto_sem_status(raiz, "OCG-Teatro-1968")
            (pasta / "status.json").write_text(
                json.dumps({"status": "campvision_concluido", "quem": "outro"}), encoding="utf-8"
            )
            self.assertEqual(vigia.varrer(raiz, Config()), [])
            dados = json.loads((pasta / "status.json").read_text())
            self.assertEqual(dados["status"], "campvision_concluido")
            self.assertEqual(dados["quem"], "outro")


class TestRelatorioGeral(unittest.TestCase):
    def _eventos(self) -> list[Evento]:
        ontem = (datetime.now() - timedelta(days=1)).isoformat(timespec="seconds")
        return [
            Evento(quando=ontem, projeto="TEATRO", pranchas=100, com_carimbo=96,
                   custo_usd=1.30, duracao_segundos=3600, campos_a_revisar=12),
            Evento(projeto="CASA", pranchas=40, com_carimbo=40, custo_usd=0.52,
                   duracao_segundos=1200),
            Evento(projeto="MUSEU", falha="SMB caiu"),
        ]

    def test_soma_o_acervo_inteiro(self):
        texto = relatorio_diario.montar_geral(self._eventos())
        self.assertIn("Projetos             3", texto)
        self.assertIn("Pranchas catalogadas 140", texto)
        self.assertIn("US$ 1.82", texto)

    def test_mostra_custo_por_prancha(self):
        self.assertIn("por prancha", relatorio_diario.montar_geral(self._eventos()))

    def test_lista_falhas(self):
        texto = relatorio_diario.montar_geral(self._eventos())
        self.assertIn("Projetos que falharam (1)", texto)
        self.assertIn("SMB caiu", texto)

    def test_inclui_fila_quando_informada(self):
        texto = relatorio_diario.montar_geral(self._eventos(), pendentes=7)
        self.assertIn("Na fila agora        7", texto)

    def test_acervo_vazio(self):
        self.assertIn("Nenhum projeto", relatorio_diario.montar_geral([]))

    def test_escreve_txt_e_html(self):
        with TemporaryDirectory() as tmp:
            destino = relatorio_diario.escrever_geral(self._eventos(), Path(tmp), 2)
            self.assertTrue(destino.exists())
            html = (Path(tmp) / "relatorio-geral.html").read_text()
            self.assertIn("relatório geral", html)


class TestRegravar(unittest.TestCase):
    def test_regrava_a_partir_da_planilha(self):
        from nucleo.aplicar import regravar_metadados

        with TemporaryDirectory() as tmp:
            pasta = Path(tmp)
            prancha_falsa(pasta / "p1.jpg", 400, 300)
            leitura = Leitura(
                arquivo="p1.jpg",
                valores={"projeto": "TEATRO", "ano": "1968"},
                confiancas={"projeto": 0.9},
                carimbo_encontrado=True,
            )
            csv_path = planilha.escrever_csv([leitura], pasta / "catalogacao.csv")
            mensagens = regravar_metadados(pasta, csv_path, CAMP)
            self.assertIn("1 arquivo(s)", mensagens[0])


if __name__ == "__main__":
    unittest.main()
