"""Imagem corrompida no lote e nova tentativa sem refazer o hash (Golombeck2, 15/10)."""

from __future__ import annotations

import errno
import unittest
from unittest import mock

from nucleo import entrada, vigia
from tests.test_entrada import Base

QUEBRADA = OSError("broken data stream when writing image file")


class TestImagemCorrompida(Base):
    def test_imagem_quebrada_vira_erro_do_lote_com_o_nome_e_sem_api(self):
        self.scan("F026", "Fotos", "Tarumã", n=3, formatos=("jpg",))
        original = entrada.formatos.imagem_de_leitura

        def quebra_a_segunda(caminho, destino, *a, **k):
            if caminho.stem.endswith("D00002"):
                raise QUEBRADA
            return original(caminho, destino, *a, **k)

        r = self.recebedor()
        with mock.patch.object(entrada.formatos, "imagem_de_leitura", side_effect=quebra_a_segunda):
            res = r.processar(r.pendentes()[0])
        self.assertEqual(res.status, entrada.ERRO)
        self.assertIn("corrompido", res.motivo)
        self.assertIn("scan 002.jpg", res.motivo)          # o nome que o operador conhece
        self.assertEqual(r.cliente.chamadas, [])            # parou antes de gastar API
        self.assertEqual(self.recebedor().pendentes(), [])  # não fica em loop

    def test_erro_de_disco_de_verdade_continua_interrompendo_a_rodada(self):
        self.scan("F026", "Fotos", "Tarumã", n=1, formatos=("jpg",))
        r = self.recebedor()
        with mock.patch.object(entrada.formatos, "imagem_de_leitura",
                               side_effect=OSError(errno.EIO, "Input/output error")):
            with self.assertRaises(OSError):
                r.processar(r.pendentes()[0])


class TestHashGuardado(Base):
    def test_nova_tentativa_nao_relê_o_original(self):
        self.scan("F026", "Fotos", "Tarumã", n=2, formatos=("jpg",))
        r = self.recebedor()
        with mock.patch.object(entrada.formatos, "imagem_de_leitura", side_effect=QUEBRADA):
            r.processar(r.pendentes()[0])                   # 1ª vez: calcula e guarda
        lidos = []
        real = entrada.sha256

        def contar(caminho):
            lidos.append(caminho)
            return real(caminho)

        unidade = entrada.descobrir(self.scanners)[0]
        with mock.patch.object(entrada, "sha256", side_effect=contar), \
                mock.patch.object(entrada.formatos, "imagem_de_leitura", side_effect=QUEBRADA):
            self.recebedor().processar(unidade)             # 2ª vez: mesmo tamanho e data
        self.assertFalse([p for p in lidos if self.scanners in p.parents])


class TestVigiaNaoConfundeComQnap(Base):
    def test_oserror_sem_errno_marca_erro_e_segue(self):
        self.scan("F026", "Fotos", "Tarumã", n=1, formatos=("jpg",))
        v = vigia.Vigia(self.config, mock.Mock(), self.estado)
        v._recebedor_obj = self.recebedor()
        with mock.patch.object(entrada.Recebedor, "processar", side_effect=QUEBRADA):
            v.receber_lotes()
        self.assertNotEqual(v.estado.situacao, "QNAP com problema")
        ultimo = entrada.Estado(self.estado).ultimos()
        self.assertEqual(next(iter(ultimo.values()))["status"], entrada.ERRO)


if __name__ == "__main__":
    unittest.main()
