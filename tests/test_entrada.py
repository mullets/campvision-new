"""Recebimento: 100 - Scanners -> ACERVOS_CAMP. Nenhum teste toca a rede."""

from __future__ import annotations

import json
import shutil
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from PIL import Image

from nucleo import entrada, estrutura, fundos, vigia
from nucleo.config import Config
from nucleo.livro import Livro
from tests.test_nucleo import ClienteFalso, prancha_falsa, resposta_padrao

TEM_EXIFTOOL = shutil.which("exiftool") is not None


class PainelFalso:
    ligado = True
    url = "http://painel"
    ultimo_ok = ultimo_erro = ""

    def __init__(self, reservar_ok: bool = True):
        self.reservar_ok = reservar_ok
        self.reservas: dict[str, str] = {}
        self.avisos: list[tuple] = []
        self.heartbeats: list[dict] = []

    def reservar(self, fundo_codigo, titulo, chave_reserva, **extra):
        if not self.reservar_ok:
            return None
        if chave_reserva not in self.reservas:
            self.reservas[chave_reserva] = f"{fundo_codigo}-P{len(self.reservas) + 1:04d}"
        return self.reservas[chave_reserva]

    def aviso(self, codigo, pasta, status):
        self.avisos.append((codigo, pasta, status))
        return True

    def heartbeat(self, dados):
        self.heartbeats.append(dados)
        return True

    def contexto(self, fundo=""):
        return None

    def pendentes(self):
        return 0

    def reenviar(self):
        return 0


def exif_falso(itens, identidade):
    return len(itens), []


def conferir_falso(itens):
    return {p for p, _ in itens}


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        base = Path(self.tmp.name)
        self.scanners = base / "100 - Scanners"
        self.acervo = base / "ACERVOS_CAMP"
        self.estado = base / "estado"
        self.scanners.mkdir()
        self.acervo.mkdir()
        self.painel = PainelFalso()
        self.config = Config(
            espera_estabilidade_segundos=0, entrada_quieto_minutos=0, trabalhadores=1,
            consolidar_por_projeto=False, pasta_entrada=str(self.scanners),
            pasta_acervo_final=str(self.acervo), auto_atualizar=False,
        )

    def tearDown(self):
        self.tmp.cleanup()

    def recebedor(self):
        livro = Livro(self.acervo, self.estado / "reserva")
        return entrada.Recebedor(self.config, ClienteFalso([resposta_padrao()]), self.painel,
                                 livro, self.estado, fundos.Tabela(fundos.EMBUTIDA))

    def rodar(self, exif_real: bool = False):
        r = self.recebedor()
        resultados = []
        patches = [] if exif_real else [
            mock.patch.object(entrada.mod_metadados, "gravar_em_lote", side_effect=exif_falso),
            mock.patch.object(entrada, "conferir_exif", side_effect=conferir_falso),
        ]
        for p in patches:
            p.start()
        try:
            for unidade in r.pendentes():
                resultados.append(r.processar(unidade))
        finally:
            for p in patches:
                p.stop()
        return resultados

    def scan(self, *partes: str, n: int = 2, formatos=("tif", "jpg")) -> Path:
        pasta = self.scanners.joinpath(*partes)
        for fmt in formatos:
            sub = pasta / fmt.upper()
            sub.mkdir(parents=True, exist_ok=True)
            for i in range(n):
                img = Image.new("RGB", (900, 700), (255, 255 - (len(list(self.scanners.rglob("*"))) % 200), 255 - i * 7))
                img.save(sub / f"scan {i + 1:03d}.{fmt}", format="TIFF" if fmt == "dng" else None)
        return pasta


class TestContexto(Base):
    def ctx(self, *partes):
        pasta = self.scan(*partes, n=1)
        unidade = entrada.Unidade(pasta, pasta.relative_to(self.scanners))
        return entrada.contexto(unidade, fundos.Tabela(fundos.EMBUTIDA))

    def test_fundo_material_projeto(self):
        c = self.ctx("F026 - Sami Bussab", "01 - Desenhos e pranchas", "Edifício Tarumã 1972")
        self.assertEqual((c.fundo.codigo, c.serie, c.projeto, c.ano),
                         ("F026", "S01", "Edifício Tarumã", "1972"))

    def test_ordem_projeto_antes_do_material(self):
        c = self.ctx("SBU Sami Bussab", "Casa Morumbi", "Fotografias")
        self.assertEqual((c.fundo.codigo, c.serie, c.projeto), ("F026", "S03", "Casa Morumbi"))

    def test_padrao_da_estacao_windows(self):
        c = self.ctx("F010 - Francisco Segnini Jr", "FSJ-AAbreuChacaraAnaliaFranco-1989")
        self.assertEqual((c.fundo.codigo, c.projeto, c.ano), ("F010", "AAbreuChacaraAnaliaFranco", "1989"))
        self.assertEqual(c.serie, "S99")  # sem pasta de material: não identificado

    def test_codigo_e_nome_em_conflito_nao_chuta(self):
        c = self.ctx("F005 - Chu Ming", "Casa")  # o certo é F006
        self.assertIsNone(c.fundo)
        self.assertTrue(c.problemas)

    def test_manifesto_vence_o_caminho(self):
        pasta = self.scan("qualquer", "coisa", n=1)
        (pasta / "manifesto.json").write_text(json.dumps({
            "fundo_codigo": "F023", "nome": "Teatro de Santos", "ano": "1968",
            "tipo_material": "negativos", "operador": "Beatriz", "estacao": "Foto 1",
        }), encoding="utf-8")
        c = entrada.contexto(entrada.Unidade(pasta, pasta.relative_to(self.scanners)),
                             fundos.Tabela(fundos.EMBUTIDA))
        self.assertEqual((c.fundo.codigo, c.serie, c.projeto, c.operador),
                         ("F023", "S04", "Teatro de Santos", "Beatriz"))

    def test_pasta_de_teste_e_marcada(self):
        self.assertTrue(self.ctx("F026", "teste", "Casa").teste)


class TestDescoberta(Base):
    def test_unidade_e_a_pasta_acima_de_tif_jpg(self):
        self.scan("F026 - Sami Bussab", "01 - Desenhos e pranchas", "Tarumã")
        unidades = entrada.descobrir(self.scanners)
        self.assertEqual([str(u.relativo) for u in unidades],
                         [str(Path("F026 - Sami Bussab/01 - Desenhos e pranchas/Tarumã"))])

    def test_pasta_mexida_agora_espera(self):
        self.scan("F026", "Tarumã")
        self.config.entrada_quieto_minutos = 10
        [r] = self.rodar()
        self.assertEqual(r.status, entrada.AGUARDANDO)
        self.assertEqual(list(self.acervo.iterdir()), [])


class TestPontaAPonta(Base):
    def test_fluxo_completo(self):
        origem = self.scan("F026 - Sami Bussab", "01 - Desenhos e pranchas", "Edifício Tarumã 1972")
        (origem / "manifesto.json").write_text(json.dumps({
            "operador": "Beatriz", "operador_email": "beatriz@camp.arq.br",
            "estacao": "Contex 1", "tipo_estacao": "contex", "lote_id": "L1",
        }), encoding="utf-8")

        [r] = self.rodar()
        self.assertEqual(r.status, entrada.PRONTO, r.motivo)
        self.assertEqual(r.codigo, "F026-P0001")

        fundo = self.acervo / "F026 - SBU Sami Bussab"
        projeto = fundo / "01 - Projetos" / "F026-P0001 - Edifício Tarumã"
        serie = projeto / "01 - Desenhos e pranchas"
        # MODELO de pastas
        for nome in estrutura.PASTAS_DO_FUNDO:
            self.assertTrue((fundo / nome).is_dir(), nome)
        for nome in estrutura.SERIES.values():
            self.assertTrue((projeto / nome).is_dir(), nome)
        self.assertTrue((projeto / "README.md").exists())
        # Nomenclatura CAMP, TIF e JPG irmãos com o mesmo código
        nomes = sorted(p.name for p in serie.iterdir())
        self.assertEqual(nomes, [
            "F026-P0001-1972-S01-D00001.jpg", "F026-P0001-1972-S01-D00001.tif",
            "F026-P0001-1972-S01-D00002.jpg", "F026-P0001-1972-S01-D00002.tif",
        ])
        # Contrato com o painel
        info = json.loads((projeto / "info_projeto.json").read_text())
        self.assertEqual((info["codigo"], info["fundo_codigo"], info["folhas_esperadas"],
                          info["operador"], info["estacao"]),
                         ("F026-P0001", "F026", 2, "Beatriz", "Contex 1"))
        status = json.loads((projeto / "status.json").read_text())
        self.assertEqual((status["status"], status["codigo"], status["imagens"], status["folhas"]),
                         ("pronto", "F026-P0001", 4, 2))
        self.assertEqual([a[2] for a in self.painel.avisos][0], "processando")
        self.assertEqual(self.painel.avisos[-1][2], "pronto")
        cat = projeto / "catalogacao"
        for arquivo in ("catalogacao.csv", "contatos.jpg", "erros.json", "lotes/L1.json"):
            self.assertTrue((cat / arquivo).exists(), arquivo)
        self.assertEqual(list(projeto.rglob("*.xlsx")), [])
        # Original apagado, entrada limpa
        self.assertFalse(origem.exists())
        # Livro de registro conta a história de cada arquivo
        livro = Livro(self.acervo)
        historia = livro.historico("F026-P0001-1972-S01-D00001")
        acoes = [l["acao"] for l in historia]
        for acao in ("copiado", "lido", "exif_gravado", "apagado_original"):
            self.assertIn(acao, acoes)
        copia = next(l for l in historia if l["acao"] == "copiado")
        self.assertTrue(copia["nome_original"].startswith("scan 00"))
        self.assertEqual(len(copia["sha256"]), 64)
        self.assertTrue((self.acervo / "_campvision" / "registro").is_dir())

    def test_segundo_lote_continua_a_numeracao_e_nao_reserva_de_novo(self):
        self.scan("F026", "Fotos", "Tarumã", n=2, formatos=("jpg",))
        self.rodar()
        self.scan("F026", "Negativos", "Tarumã", n=1, formatos=("dng",))
        # DNG falso: um TIFF com extensão .dng abre pelo PIL
        [r] = self.rodar()
        self.assertEqual(r.status, entrada.PRONTO, r.motivo)
        self.assertEqual(len(self.painel.reservas), 1)
        projeto = next((self.acervo / "F026 - SBU Sami Bussab" / "01 - Projetos").iterdir())
        self.assertTrue((projeto / "04 - Negativos" / "F026-P0001-0000-S04-D00003.dng").exists())
        status = json.loads((projeto / "status.json").read_text())
        self.assertEqual(status["folhas"], 3)
        self.assertEqual(len(status["lotes_prontos"]), 2)

    def test_reenvio_do_mesmo_arquivo_nao_duplica(self):
        self.config.apagar_original_apos_pronto = False
        pasta = self.scan("F026", "Fotos", "Tarumã", n=2, formatos=("jpg",))
        self.rodar()
        (pasta / "JPG" / "scan 099.jpg").write_bytes((pasta / "JPG" / "scan 001.jpg").read_bytes())
        [r] = self.rodar()
        self.assertEqual(r.copiados, 0)  # mesmo conteúdo: já está no acervo
        projeto = next((self.acervo / "F026 - SBU Sami Bussab" / "01 - Projetos").iterdir())
        self.assertEqual(len(list((projeto / "03 - Fotografias").iterdir())), 2)

    def test_pdf_e_lido_e_vira_documento(self):
        pasta = self.scanners / "F026" / "Documentos" / "Tarumã"
        pasta.mkdir(parents=True)
        Image.new("RGB", (600, 800), "white").save(pasta / "memorial.pdf")
        [r] = self.rodar()
        self.assertEqual(r.status, entrada.PRONTO, r.motivo)
        projeto = r.pasta_projeto
        self.assertTrue((projeto / "02 - Documentos textuais" / "F026-P0001-0000-S02-D00001.pdf").exists())

    def test_formato_desconhecido_fica_na_entrada(self):
        pasta = self.scan("F026", "Fotos", "Tarumã", n=1, formatos=("jpg",))
        (pasta / "JPG" / "notas.docx").write_text("x")
        [r] = self.rodar()
        self.assertEqual(r.status, entrada.PRONTO)
        self.assertTrue((pasta / "JPG" / "notas.docx").exists())
        self.assertFalse((pasta / "JPG" / "scan 001.jpg").exists())
        erros = json.loads((r.pasta_projeto / "catalogacao" / "erros.json").read_text())
        self.assertTrue(any("não suportado" in e["detalhe"] for e in erros))


class TestFalhasNaoApagam(Base):
    def test_sem_painel_projeto_novo_espera(self):
        self.painel.reservar_ok = False
        pasta = self.scan("F026", "Fotos", "Tarumã", n=1, formatos=("jpg",))
        [r] = self.rodar()
        self.assertEqual(r.status, entrada.AGUARDANDO)
        self.assertTrue((pasta / "JPG" / "scan 001.jpg").exists())
        self.assertEqual(list((self.acervo / "F026 - SBU Sami Bussab" / "01 - Projetos").iterdir()), [])

    def test_exif_nao_conferido_vira_erro_e_nao_apaga(self):
        pasta = self.scan("F026", "Fotos", "Tarumã", n=1, formatos=("jpg",))
        r = self.recebedor()
        with mock.patch.object(entrada.mod_metadados, "gravar_em_lote", return_value=(0, ["sem exiftool"])), \
                mock.patch.object(entrada, "conferir_exif", return_value=set()):
            res = r.processar(r.pendentes()[0])
        self.assertEqual(res.status, entrada.ERRO)
        self.assertTrue((pasta / "JPG" / "scan 001.jpg").exists())
        status = json.loads((res.pasta_projeto / "status.json").read_text())
        self.assertEqual(status["status"], "erro")
        # Erro não volta sozinho enquanto nada mudar na pasta
        self.assertEqual(self.recebedor().pendentes(), [])

    def test_fundo_desconhecido_nao_cria_nada(self):
        pasta = self.scan("F099 - Fulano", "Casa", n=1, formatos=("jpg",))
        [r] = self.rodar()
        self.assertEqual(r.status, entrada.ERRO)
        self.assertEqual(list(self.acervo.glob("F*")), [])
        self.assertTrue(pasta.exists())


@unittest.skipUnless(TEM_EXIFTOOL, "exiftool não instalado")
class TestExifDeVerdade(Base):
    def test_metadados_dentro_de_jpg_tif_e_pdf(self):
        import subprocess

        self.scan("F023 - OCG", "Desenhos", "Teatro de Santos 1968", n=1)
        pasta = self.scanners / "F023 - OCG" / "Desenhos" / "Teatro de Santos 1968"
        Image.new("RGB", (600, 800), "white").save(pasta / "memorial.pdf")
        [r] = self.rodar(exif_real=True)
        self.assertEqual(r.status, entrada.PRONTO, r.motivo)
        # EXIF muda o hash da cópia; o original mesmo assim tem que ser apagado
        self.assertFalse(pasta.exists(), "original deveria ter sido apagado")
        arquivos = sorted((r.pasta_projeto / "01 - Desenhos e pranchas").iterdir())
        self.assertEqual({a.suffix for a in arquivos}, {".jpg", ".tif", ".pdf"})
        for arquivo in arquivos:
            dados = json.loads(subprocess.run(
                ["exiftool", "-json", "-XMP-dc:Identifier", "-XMP-photoshop:Credit", str(arquivo)],
                capture_output=True, text=True).stdout)[0]
            self.assertEqual(dados["Identifier"], arquivo.stem)
            self.assertEqual(dados["Credit"],
                             "Acervo Oswaldo Corrêa Gonçalves/CAMP - Casa da Arquitetura Moderna Paulista")


class TestVigia(Base):
    def test_uma_rodada_e_heartbeat(self):
        self.scan("F026", "Fotos", "Tarumã", n=1, formatos=("jpg",))
        v = vigia.Vigia(self.config, ClienteFalso([resposta_padrao()]), self.estado, painel=self.painel)
        with mock.patch.object(entrada.mod_metadados, "gravar_em_lote", side_effect=exif_falso), \
                mock.patch.object(entrada, "conferir_exif", side_effect=conferir_falso), \
                mock.patch.object(entrada.mod_fundos, "carregar", return_value=fundos.Tabela(fundos.EMBUTIDA)):
            self.assertEqual(v.uma_rodada(), 1)
        v._talvez_heartbeat(forcar=True)
        hb = self.painel.heartbeats[-1]
        self.assertEqual((hb["estacao_id"], hb["app"]), ("campvision2", "CAMP Vision 2"))
        self.assertTrue(hb["montagens"]["acervo"])
        self.assertTrue((self.estado / "estado.json").exists())
        self.assertEqual(json.loads((self.estado / "estado.json").read_text())["projetos_hoje"], 1)

    def test_varredura_antiga_desligada_quando_ha_entrada(self):
        self.assertFalse(self.config.varre_pasta_vigiada)
        self.assertTrue(Config(pasta_vigiada="/x").varre_pasta_vigiada)


if __name__ == "__main__":
    unittest.main()
