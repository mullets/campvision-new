"""Lote do Mac: um projeto = um lote, só depois do status_mac.json, com o código do Mac (P0092, 19/10)."""

from __future__ import annotations

import json
import os
import time
import unittest

from nucleo import entrada, fundos
from tests.test_entrada import Base
from tests import test_identidade as ti
from tests.test_nucleo import ClienteFalso

TIFF = "01 - Arquivo Arquivístico (TIFF)"
JPG = "03 - Preview (JPG)"


class LoteMac(Base):
    def projeto_mac(self, com_json: bool = True, ja_existia: bool = False, n: int = 2):
        projeto = self.scanners / "F026 - SBU Sami Bussab" / "P0092 - Residência Sr. Luiz Marcelo Sales"
        partes = projeto.relative_to(self.scanners).parts
        self.scan(*partes, "01 - Desenhos e pranchas", TIFF, n=n, formatos=("tif",))
        self.scan(*partes, "01 - Desenhos e pranchas", JPG, n=n, formatos=("jpg",))
        if com_json:
            info = {"estacao": "mac-vuescan", "codigo_fundo": "F026", "numero_projeto": "P0092",
                    "projeto": "Residência Sr. Luiz Marcelo Sales", "series": ["01 - Desenhos e pranchas"],
                    "projeto_ja_existia": ja_existia}
            (projeto / "info_lote_20261019-115400_mac.json").write_text(json.dumps(info), encoding="utf-8")
            (projeto / "status_mac.json").write_text(json.dumps({"etapa": "enviado_mac"}), encoding="utf-8")
        return projeto


class TestUmProjetoUmLote(LoteMac):
    def test_durante_a_copia_as_subpastas_nao_viram_lotes(self):
        projeto = self.projeto_mac(com_json=False)   # Mac ainda copiando: sem JSON na pasta do projeto
        unidades = entrada.descobrir(self.scanners)
        self.assertEqual([u.pasta for u in unidades], [projeto])

    def test_depois_dos_json_continua_um_lote(self):
        projeto = self.projeto_mac()
        self.assertEqual([u.pasta for u in entrada.descobrir(self.scanners)], [projeto])

    def test_sem_status_mac_espera(self):
        projeto = self.projeto_mac(com_json=False)
        self.config.entrada_quieto_minutos = 0
        [u] = entrada.descobrir(self.scanners)
        self.assertEqual(entrada.pronta(u, self.config), (False, "aguardando o Mac terminar o envio (status_mac.json)"))

    def test_envio_novo_depois_do_status_mac_espera(self):
        projeto = self.projeto_mac()
        antigo = time.time() - 600
        os.utime(projeto / "status_mac.json", (antigo, antigo))   # status do envio anterior
        [u] = entrada.descobrir(self.scanners)
        self.assertEqual(entrada.pronta(u, self.config), (False, "envio novo do Mac em andamento"))

    def test_status_mac_por_ultimo_libera(self):
        self.projeto_mac()
        [u] = entrada.descobrir(self.scanners)
        self.assertEqual(entrada.pronta(u, self.config), (True, ""))

    def test_windows_nao_e_afetado(self):
        pasta = self.scan("F026", "Fotos", "Tarumã", n=1, formatos=("jpg",))
        [u] = entrada.descobrir(self.scanners)
        self.assertEqual(u.pasta, pasta)
        self.assertFalse(entrada._estruturado_como_mac(u.pasta))


class TestCodigoDoMacComVariosGrupos(Base):
    """Erro de OCR ('WIZ SALES' × 'LUIZ SALES') divide o lote em grupos: todos vão para o P do Mac."""

    rodar_com = ti.TestPontaAPontaIdentidade.rodar_com
    projetos = ti.TestPontaAPontaIdentidade.projetos

    def test_grupos_vao_para_o_codigo_do_mac_sem_perguntar(self):
        LoteMac.projeto_mac(self, n=6)
        luiz = {"projeto": "RESIDENCIA LUIZ SALES", "endereco": "RUA DR. ALBERTO FARIA"}
        wiz = {"projeto": "RESIDÊNCIA WIZ GALES", "endereco": "RUA DR. ALBERTO FARIA"}
        cliente = ClienteFalso(ti.respostas(luiz, luiz, luiz, wiz, wiz, wiz))
        base = [{"codigo": "F026-P0092", "projeto": "Residência Sr. Luiz Marcelo Sales"},
                {"codigo": "F026-P0019", "projeto": "Residência Luis Sales"},
                {"codigo": "F026-P0022", "projeto": "Residência Sales"}]
        painel = ti.PainelComBase(base)
        [r] = self.rodar_com(cliente, painel)
        self.assertEqual(r.status, entrada.PRONTO, r.motivo)
        self.assertEqual(list(self.projetos()), ["F026-P0092"])
        self.assertFalse([p for p in painel.pedidos if p.get("candidatos")])   # nenhuma decisão aberta


if __name__ == "__main__":
    unittest.main()
