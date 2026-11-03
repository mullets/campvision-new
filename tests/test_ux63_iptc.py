"""UX-63 (parte do CAMP Vision): avisos/erros do exiftool ao gravar o metadado.

- "Warning: [Minor] IPTC:ObjectName exceeds length limit (truncated)" (e By-line, Credit): o
  CV2 corta no limite do IPTC antes de gravar, então o aviso não nasce. Com `-m` o exiftool
  nem corta (grava fora do padrão), por isso o corte tem de ser nosso.
- Valor lido com quebra de linha (título em duas linhas) partia o argfile do exiftool
  (um argumento por linha): "Error: File not found - <resto do título>" e o campo cortado.
- [Minor] continua sendo aviso, nunca erro (separar_avisos).
"""

from __future__ import annotations

import json
import shutil
import subprocess
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from PIL import Image

from nucleo import metadados

IDENTIDADE = metadados.Identidade(nome="CAMP — Centro de Arquitetura e Memória Paulista",
                                  site="https://camp.arq.br", licenca="CC BY-NC 4.0")
CAMPOS = {
    "Arquiteto": "Escritório Técnico\nJúlio Neves Arquitetos Associados Ltda",
    "Título da prancha": "Planta do pavimento térreo\r\ne cobertura — situação e implantação geral do conjunto esportivo",
    "Projeto": "Teatro Municipal Doutor Losso Netto de Piracicaba — reforma e ampliação do conjunto",
    "Cidade": "São José do Rio Preto e região metropolitana",
    "UF": "São Paulo\testado",
    "_codigo": "F001-P0033-1979-S01-D00001",
}


class TestArgumentos(unittest.TestCase):
    def test_valor_com_quebra_de_linha_vira_uma_linha(self):
        args = metadados.montar_argumentos(Path("/x/a.jpg"), CAMPOS, IDENTIDADE)
        self.assertFalse([a for a in args if any(c in a for c in "\r\n\t")], args)
        self.assertIn("-XMP-dc:Creator=Escritório Técnico Júlio Neves Arquitetos Associados Ltda", args)

    def test_todo_iptc_cabe_no_limite(self):
        for arg in metadados.montar_argumentos(Path("/x/a.jpg"), CAMPOS, IDENTIDADE):
            if arg.startswith("-IPTC:") and "=" in arg:
                tag, valor = arg[1:].split("=", 1)
                tag = tag.rstrip("+")
                limite = metadados.LIMITES_IPTC.get(tag.split(":", 1)[1])
                self.assertIsNotNone(limite, f"{tag} sem limite conhecido")
                self.assertLessEqual(len(valor.encode("utf-8")), limite, arg)

    def test_limites_do_exiftool(self):
        esperado = {"ObjectName": 64, "By-line": 32, "Credit": 32, "Headline": 256, "Caption-Abstract": 2000,
                    "City": 32, "Province-State": 32, "Country-PrimaryLocationName": 64, "Keywords": 64}
        for tag, n in esperado.items():
            self.assertEqual(metadados.LIMITES_IPTC[tag], n, tag)

    def test_minor_e_aviso_nao_erro(self):
        menores, erros, _ = metadados.separar_avisos(
            ["a.jpg Warning: [Minor] IPTC:ObjectName exceeds length limit (truncated)"])
        self.assertEqual((len(menores), erros), (1, {}))


@unittest.skipUnless(shutil.which("exiftool"), "exiftool ausente")
class TestExiftoolDeVerdade(unittest.TestCase):
    def test_grava_sem_aviso_nem_erro_e_dentro_do_limite(self):
        with TemporaryDirectory() as t:
            jpg, tif = Path(t) / "a.jpg", Path(t) / "b.tif"
            Image.new("RGB", (40, 40), "white").save(jpg)
            Image.new("RGB", (40, 40), "white").save(tif)
            gravados, avisos = metadados.gravar_em_lote([(jpg, CAMPOS), (tif, CAMPOS)], IDENTIDADE)
            self.assertEqual((gravados, avisos), (2, []))
            lido = json.loads(subprocess.run(
                ["exiftool", "-json", "-charset", "iptc=UTF8", "-IPTC:all", "-XMP-dc:Title", str(jpg)],
                capture_output=True, text=True).stdout)[0]
        self.assertEqual(lido["Title"], "Planta do pavimento térreo e cobertura — situação e implantação "
                                        "geral do conjunto esportivo")
        for tag in ("ObjectName", "By-line", "Credit", "City", "Province-State"):
            self.assertLessEqual(len(str(lido[tag]).encode("utf-8")), metadados.LIMITES_IPTC[tag], tag)


if __name__ == "__main__":
    unittest.main()
