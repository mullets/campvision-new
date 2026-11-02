"""Contrato 2.6 (02/11/2026): o diário de eventos (custo, pranchas, falhas por projeto) vai para ACERVOS_CAMP/_campvision/eventos.jsonl,
onde o painel lê para mostrar o que o CAMP Vision fez por dia, o valor gasto e o gasto por fundo."""
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from nucleo import eventos


class TestEspelhoDoDiario(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.local = self.tmp / "estado" / "eventos.jsonl"
        self.acervo = self.tmp / "ACERVOS_CAMP"; self.acervo.mkdir()

    def test_copia_o_diario_inteiro_para_o_acervo(self):
        for n, custo in ((1, 0.69), (2, 0.0)):
            eventos.registrar(self.local, eventos.Evento(projeto=f"F001-P900{n} - Teste", pranchas=10, custo_usd=custo))
        self.assertTrue(eventos.espelhar(self.local, self.acervo))
        destino = self.acervo / "_campvision" / "eventos.jsonl"
        linhas = [json.loads(l) for l in destino.read_text(encoding="utf-8").splitlines()]
        self.assertEqual([l["custo_usd"] for l in linhas], [0.69, 0.0])
        self.assertEqual(set(linhas[0]), {"quando", "projeto", "pasta", "pranchas", "com_carimbo", "erros", "campos_a_revisar",
                                          "custo_usd", "duracao_segundos", "falha"})
        self.assertFalse((destino.parent / "eventos.jsonl.tmp").exists())

    def test_qnap_fora_nao_derruba(self):
        eventos.registrar(self.local, eventos.Evento(projeto="F001-P0001 - X"))
        self.assertFalse(eventos.espelhar(self.local, self.tmp / "nao_montado"))
        self.assertFalse(eventos.espelhar(self.local, ""))
        with mock.patch("nucleo.eventos.shutil.copyfile", side_effect=OSError("somente leitura")):
            self.assertFalse(eventos.espelhar(self.local, self.acervo))
        self.assertFalse((self.acervo / "_campvision" / "eventos.jsonl.tmp").exists())

    def test_vigia_espelha_ao_contabilizar(self):
        from nucleo import vigia
        v = vigia.Vigia.__new__(vigia.Vigia)
        v.caminho_eventos, v.config = self.local, mock.Mock(raiz_final=str(self.acervo))
        v.estado = vigia.EstadoVigia()
        v._contabilizar(eventos.Evento(projeto="F009-P0001 - Y", pranchas=3, custo_usd=0.12))
        linhas = (self.acervo / "_campvision" / "eventos.jsonl").read_text(encoding="utf-8").splitlines()
        self.assertEqual(json.loads(linhas[-1])["custo_usd"], 0.12)


if __name__ == "__main__":
    unittest.main()
