"""Aviso [Minor] do exiftool (TIF fora do padrão) não pode impedir a gravação do EXIF.
Visto em F026-P0009: 'EXIF conferido em 67 de 68' parava um lote já conferido e aprovado."""
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from nucleo import metadados


class TestExiftoolMinor(unittest.TestCase):
    def test_gravacao_pede_ao_exiftool_para_ignorar_avisos_minor(self):
        chamadas = []

        def falso(cmd, **k):
            chamadas.append(cmd)
            return mock.Mock(stdout="    1 image files updated", stderr="")

        with tempfile.TemporaryDirectory() as t, \
                mock.patch.object(metadados, "disponivel", return_value=True), \
                mock.patch.object(metadados.subprocess, "run", side_effect=falso):
            tif = Path(t) / "F026-P0009-1978-S01-D00034.tif"
            tif.write_bytes(b"x")
            metadados.gravar_em_lote([(tif, {})], metadados.Identidade())
        self.assertTrue(chamadas)
        self.assertIn("-m", chamadas[0])
