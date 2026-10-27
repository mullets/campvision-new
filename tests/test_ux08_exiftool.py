"""UX-08: aviso [Minor] do exiftool não pode parar o lote; "EXIF conferido em N de M" vai ao painel
com a lista dos arquivos que ficaram sem conferir e o motivo de cada um.

Card: F026-P0009 parou "com erro" por "IPTC:By-line exceeds length limit (truncated)" ([Minor]);
F016-P0024 mostrava só "EXIF conferido em 6 de 48 arquivos".
Causa achada: o `-m` ia antes do `-@ argfile` e, no exiftool, só vale para o 1º bloco do
`-execute`. Do 2º arquivo em diante o erro [minor] voltava a ser erro e o arquivo não era gravado.
"""

from __future__ import annotations

import json
import re
import shutil
import struct
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from PIL import Image

from nucleo import entrada, metadados
from tests.test_entrada import Base

TEM_EXIFTOOL = shutil.which("exiftool") is not None

# O que o painel (camp-painel, backend/app/avisos_cv.py, CAMP-122/UX-08) usa para reconhecer o aviso [Minor].
_EXT = r"(?:tiff?|jpe?g|png|dng|psd)"
ITEM_DO_PAINEL = re.compile(r"(?:(?P<arq>[^\s:;,]+\." + _EXT + r")\s*[:\-]?\s*)?Warning:\s*\[Minor\]\s*(?P<msg>[^;|\n]+?)\s*$", re.I)
AUTOR_LONGO = "Escritório Técnico de Arquitetura e Urbanismo Ltda."   # > 32 bytes (limite do IPTC:By-line)


def tif(caminho: Path, exif_aponta_para_ifd0: bool = False) -> Path:
    """TIF pequeno. Com `exif_aponta_para_ifd0`, fora do padrão: o exiftool dá
    "Error: [minor] ExifIFD pointer references previous IFD0 directory" (sem -m não grava)."""
    Image.new("RGB", (20, 20)).save(caminho)
    if exif_aponta_para_ifd0:
        b = bytearray(caminho.read_bytes())
        ifd = struct.unpack_from("<I", b, 4)[0]
        n = struct.unpack_from("<H", b, ifd)[0]
        struct.pack_into("<HHII", b, ifd + 2 + 12 * (n - 1), 0x8769, 4, 1, 8)
        caminho.write_bytes(bytes(b))
    return caminho


class TestMinorEmTodosOsArquivos(unittest.TestCase):
    def test_m_vai_em_common_args_para_valer_em_todos_os_blocos(self):
        chamadas = []

        def falso(cmd, **k):
            chamadas.append(cmd)
            return mock.Mock(stdout="    1 image files updated\n    1 image files updated", stderr="")

        with tempfile.TemporaryDirectory() as t, \
                mock.patch.object(metadados, "disponivel", return_value=True), \
                mock.patch.object(metadados.subprocess, "run", side_effect=falso):
            a, b = Path(t) / "a.tif", Path(t) / "b.tif"
            metadados.gravar_em_lote([(a, {}), (b, {})], metadados.Identidade())
        cmd = chamadas[0]
        self.assertIn("-common_args", cmd)
        self.assertEqual(cmd[cmd.index("-common_args") + 1:], ["-m"])

    def test_renomear_tambem_poe_m_em_todos_os_blocos(self):
        from nucleo import renomear
        fonte = Path(renomear.__file__).read_text(encoding="utf-8")
        self.assertIn('"-common_args", "-m"', fonte)
        self.assertNotIn('["exiftool", "-m", "-@"', fonte)

    @unittest.skipUnless(TEM_EXIFTOOL, "exiftool não instalado")
    def test_exiftool_de_verdade_grava_o_segundo_tif_fora_do_padrao(self):
        with tempfile.TemporaryDirectory() as t:
            pasta = Path(t) / "F026 - SBU" / "01 - Projetos" / "F026-P0009 - Casa"
            pasta.mkdir(parents=True)
            a = tif(pasta / "F026-P0009-1978-S01-D00001.tif")
            b = tif(pasta / "F026-P0009-1978-S01-D00002.tif", exif_aponta_para_ifd0=True)
            ident = metadados.Identidade(nome="CAMP", site="https://exemplo.org.br")
            gravados, avisos = metadados.gravar_em_lote(
                [(a, {"_codigo": a.stem, "Arquiteto": AUTOR_LONGO}), (b, {"_codigo": b.stem, "Arquiteto": AUTOR_LONGO})],
                ident)
            self.assertEqual(gravados, 2, avisos)
            self.assertEqual(entrada.conferir_exif([(a, a.stem), (b, b.stem)]), {a, b})
            _menores, erros, _gerais = metadados.separar_avisos(avisos)
            self.assertEqual(erros, {})
            self.assertFalse(any("exiftool relatou" in x for x in avisos), avisos)
            # o CV2 corta o IPTC no limite (nada de "exceeds length limit"); o nome inteiro fica no XMP
            self.assertFalse(any("exceeds length limit" in x for x in avisos), avisos)
            lido = json.loads(metadados.subprocess.run(
                ["exiftool", "-json", "-charset", "utf8", "-IPTC:By-line", "-XMP-dc:Creator", str(a)],
                capture_output=True, text=True).stdout)[0]
            self.assertLessEqual(len(str(lido["By-line"]).encode("utf-8")), 32)
            self.assertEqual(lido["Creator"], AUTOR_LONGO)


class TestSaidaDoExiftool(unittest.TestCase):
    def gravar(self, saida: str, nomes=("F026-P0009-1978-S01-D00034.tif", "F026-P0009-1978-S01-D00035.tif")):
        with tempfile.TemporaryDirectory() as t, \
                mock.patch.object(metadados, "disponivel", return_value=True), \
                mock.patch.object(metadados.subprocess, "run",
                                  return_value=mock.Mock(stdout="", stderr=saida.format(p=t))):
            itens = [(Path(t) / "01 - Projetos" / n, {}) for n in nomes]
            return metadados.gravar_em_lote(itens, metadados.Identidade(nome="CAMP", site="x"))

    def test_minor_vira_aviso_por_arquivo_no_formato_do_painel_e_nao_falha(self):
        gravados, avisos = self.gravar(
            "Warning: [Minor] IPTC:By-line exceeds length limit (truncated) - {p}/01 - Projetos/F026-P0009-1978-S01-D00034.tif\n"
            "    1 image files updated\n    1 image files updated\n")
        self.assertEqual(gravados, 2)
        self.assertEqual(avisos, ["F026-P0009-1978-S01-D00034.tif Warning: [Minor] IPTC:By-line exceeds length limit (truncated)"])
        m = ITEM_DO_PAINEL.match(avisos[0])
        self.assertEqual((m["arq"], m["msg"]), ("F026-P0009-1978-S01-D00034.tif", "IPTC:By-line exceeds length limit (truncated)"))

    def test_erro_de_verdade_fica_por_arquivo_e_o_resumo_nao_mistura_aviso(self):
        _g, avisos = self.gravar(
            "Warning: [Minor] IPTC:By-line exceeds length limit (truncated) - {p}/01 - Projetos/F026-P0009-1978-S01-D00034.tif\n"
            "Error: Bad format (0) for IFD0 entry 9 - {p}/01 - Projetos/F026-P0009-1978-S01-D00035.tif\n"
            "    1 image files updated\n    0 image files updated\n    1 files weren't updated due to errors\n")
        menores, erros, gerais = metadados.separar_avisos(avisos)
        self.assertEqual([m["arquivo"] for m in menores], ["F026-P0009-1978-S01-D00034.tif"])
        self.assertEqual(erros, {"F026-P0009-1978-S01-D00035.tif": "Bad format (0) for IFD0 entry 9"})
        self.assertTrue(gerais[-1].startswith("exiftool relatou 1 problema(s)"), gerais)
        self.assertNotIn("Minor", gerais[-1])


class TestLote(Base):
    def lote(self):
        return self.scan("F026 - SBU Sami Bussab", "01 - Desenhos e pranchas", "Casa Nova 1980", n=2, formatos=("jpg",))

    def processar(self, gravar, conferir):
        r = self.recebedor()
        with mock.patch.object(entrada.mod_metadados, "gravar_em_lote", side_effect=gravar), \
                mock.patch.object(entrada, "conferir_exif", side_effect=conferir):
            return [r.processar(u) for u in r.pendentes()]

    def projeto(self) -> Path:
        return next((self.acervo / "F026 - SBU Sami Bussab" / "01 - Projetos").iterdir())

    def test_aviso_minor_nao_para_o_lote_e_vira_aviso_em_erros_json(self):
        self.lote()

        def gravar(itens, ident):
            return len(itens), [f"{itens[0][0].name} Warning: [Minor] IPTC:By-line exceeds length limit (truncated)"]

        [res] = self.processar(gravar, lambda itens: {p for p, _ in itens})
        self.assertEqual(res.status, entrada.PRONTO, res.motivo)
        erros = json.loads((res.pasta_projeto / "catalogacao" / "erros.json").read_text())
        avisos = [e for e in erros if "[Minor]" in e["detalhe"]]
        self.assertEqual(len(avisos), 1, erros)
        self.assertEqual(avisos[0]["gravidade"], "aviso")
        self.assertEqual(avisos[0]["categoria"], "metadado")
        self.assertTrue(entrada.estrutura.CODIGO_DOCUMENTO.match(avisos[0]["arquivo"]), avisos[0])
        self.assertTrue(ITEM_DO_PAINEL.match(avisos[0]["detalhe"]), avisos[0])
        status = json.loads((res.pasta_projeto / "status.json").read_text())
        self.assertEqual((status["status"], status["erros_bloqueantes"]), ("pronto", 0))
        self.assertNotIn("exif_sem_conferir", status)

    def test_exif_parcial_manda_a_lista_dos_arquivos_e_o_motivo(self):
        self.lote()
        self.config.entrada_exigir_exif = True
        visto = {}

        def gravar(itens, ident):
            visto["segundo"] = itens[1][0].name
            return 1, [f"{itens[1][0].name} Error: Bad format (0) for IFD0 entry 9",
                       "exiftool relatou 1 problema(s): x"]

        [res] = self.processar(gravar, lambda itens: {itens[0][0]})
        self.assertEqual(res.status, entrada.ERRO)
        self.assertTrue(res.motivo.startswith("EXIF conferido em 1 de 2"), res.motivo)
        status = json.loads((self.projeto() / "status.json").read_text())
        self.assertEqual(status["status"], "erro")
        self.assertEqual(status["exif_sem_conferir"],
                         [{"arquivo": visto["segundo"], "motivo": "o exiftool não gravou: Bad format (0) for IFD0 entry 9"}])
        lote = json.loads(next((self.projeto() / "catalogacao" / "lotes").glob("*.json")).read_text())
        self.assertEqual(lote["exif"]["sem_conferir"], status["exif_sem_conferir"])

        # Próximo status do projeto (processando/pronto): a lista some (era só daquele erro)
        self.recebedor()._status(self.projeto(), "F026-P0001", entrada.PROCESSANDO, mock.Mock(lote_id="outro"))
        status = json.loads((self.projeto() / "status.json").read_text())
        self.assertEqual(status["status"], "processando")
        self.assertNotIn("exif_sem_conferir", status)

    def test_sem_motivo_do_exiftool_diz_que_o_codigo_lido_nao_confere(self):
        faltam = entrada.motivos_sem_exif([Path("/x/F026-P0001-1980-S01-D00002.tif")], ["Identidade incompleta (identidade.site)"])
        self.assertEqual(faltam[0]["arquivo"], "F026-P0001-1980-S01-D00002.tif")
        self.assertIn("XMP-dc:Identifier", faltam[0]["motivo"])

    def test_exigir_exif_desligado_poe_a_lista_no_aceite_no_formato_que_o_painel_le(self):
        self.lote()
        self.config.entrada_exigir_exif = False
        [res] = self.processar(lambda itens, ident: (1, []), lambda itens: {itens[0][0]})
        self.assertEqual(res.status, entrada.PRONTO, res.motivo)
        aceite = res.lote["aceite"]
        self.assertIs(aceite["exif_conferido"], False)            # continua booleano
        self.assertIn("exif_conferido", aceite["pendencias"])
        item = aceite["exif_sem_conferir"]
        self.assertEqual((item["ok"], item["contagem"], len(item["arquivos"])), (False, 1, 1))
        self.assertEqual(set(item["arquivos"][0]), {"arquivo", "motivo"})


if __name__ == "__main__":
    unittest.main()
