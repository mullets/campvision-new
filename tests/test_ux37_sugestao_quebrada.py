"""UX-37: o CAMP Vision não grava sugestão quebrada (resto de <parameter>, 'null' como texto).

Caso real (F026-P0030, 27/10): o painel mostrou "sugestão: UF <parameter …>null". O painel passou a
ignorar (camp-painel `saidas_cv.valor_sugerido_valido`); aqui a origem não grava mais isso.
"""

from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace

from nucleo import planilha, projeto
from nucleo.config import Config
from nucleo.esquema import Leitura
from nucleo.saneamento import RESSALVA, sanear_valor
from nucleo.visao import LeitorDeCarimbo
from tests.test_nucleo import ClienteFalso, campo, prancha_falsa, resposta_padrao

# Montado aos pedaços para o texto do teste não virar marcação de verdade em lugar nenhum.
P = "<" + "antml:" + "parameter"
FECHA = "</" + "antml:" + "parameter>"


class TestSanearValor(unittest.TestCase):
    def test_residuo_vira_vazio(self):
        for bruto in ('UF <parameter name="valor">null', f'UF {P} name="valor">null',
                      f'{P} name="valor">null{FECHA}\n{P} name="confianca">0',
                      'texto <invoke name="x">resto', "UF " + "antml:" + "parameter"):
            self.assertEqual(sanear_valor(bruto), ("", True), bruto)

    def test_null_como_texto_vira_vazio(self):
        for bruto in ("null", " NULL ", "None", "undefined", "NaN", "n/a", "N/A", '"null"'):
            self.assertEqual(sanear_valor(bruto)[0], "", bruto)

    def test_recupera_so_o_inequivoco(self):
        self.assertEqual(sanear_valor("SP <parameter"), ("SP", True))
        self.assertEqual(sanear_valor(f'SP {P} name="confianca">0.9'), ("SP", True))
        self.assertEqual(sanear_valor(f"SP{FECHA}"), ("SP", True))
        self.assertEqual(sanear_valor(f'{P} name="valor">SÃO PAULO'), ("SÃO PAULO", True))

    def test_valor_bom_fica_igual(self):
        for bom in ("SP", "São Paulo", "1:50", "A < B", "Rua Nula, 10", "NONE-1"):
            self.assertEqual(sanear_valor(bom), (bom, False))


class TestLeituraNova(unittest.TestCase):
    def test_resposta_quebrada_nao_vira_valor_e_deixa_ressalva(self):
        torta = resposta_padrao(
            uf=campo(f'UF {P} name="valor">null', 0.8),
            cidade=campo("null", 0.7),
            cliente={"valor": "JOÃO", "confianca": 0.9, "alternativas": ["None", f"JOSÉ{FECHA}"]},
            escala=campo("undefined"),
            materiais_citados=["nanquim", "null", f'{P} name="x">'],
        )
        with TemporaryDirectory() as tmp:
            caminho = prancha_falsa(Path(tmp) / "p1.jpg")
            l = LeitorDeCarimbo(Config(), ClienteFalso([torta])).ler(caminho)
        self.assertEqual(l.erro, "")
        self.assertEqual(l.valores["uf"], "")
        self.assertEqual(l.confiancas["uf"], 0.0)
        self.assertEqual(l.valores["cidade"], "")
        self.assertEqual(l.valores["escala"], "")
        self.assertEqual(l.valores["projeto"], "CASA DA PRAIA")
        self.assertEqual(l.alternativas.get("cliente"), ["JOSÉ"])
        self.assertEqual(l.materiais_citados, ["nanquim"])
        self.assertIn(f"{RESSALVA} (uf)", l.ressalvas)
        texto = json.dumps(l.para_dict(), ensure_ascii=False)
        for lixo in ("parameter", "antml", '"null"', '"None"', "undefined"):
            self.assertNotIn(lixo, texto)


class TestLeituraGravada(unittest.TestCase):
    def _antiga(self) -> dict:
        l = Leitura(arquivo="F026-P0030-1980-S01-D00001.tif")
        l.valores.update({"projeto": "CASA", "cidade": "None", "uf": f'UF {P} name="valor">null'})
        l.confiancas.update({"projeto": 0.9, "cidade": 0.6, "uf": 0.8})
        l.lidos_originais = {"uf": 'UF <parameter name="valor">null'}
        l.alternativas = {"cidade": ["undefined", "SANTOS"]}
        return l.para_dict()

    def test_leituras_json_antigo_nao_volta_para_o_pacote(self):
        with TemporaryDirectory() as tmp:
            arq = Path(tmp) / "leituras.json"
            arq.write_text(json.dumps([self._antiga()], ensure_ascii=False), encoding="utf-8")
            bruto = arq.read_bytes()
            (l,) = planilha.ler_json(arq)
            self.assertEqual(arq.read_bytes(), bruto)          # o arquivo antigo não é regravado
        self.assertEqual((l.valores["cidade"], l.valores["uf"], l.valores["projeto"]), ("", "", "CASA"))
        self.assertEqual((l.confiancas["uf"], l.confiancas["cidade"], l.confiancas["projeto"]), (0.0, 0.0, 0.9))
        self.assertEqual(l.lidos_originais["uf"], "")
        self.assertEqual(l.alternativas["cidade"], ["SANTOS"])
        self.assertTrue(any(RESSALVA in r for r in l.ressalvas))
        canon, fontes, _ = projeto.fontes_e_lacunas(SimpleNamespace(ano="1980", projeto="Casa"), {"D1": l})
        self.assertEqual((canon["uf"], canon["cidade"]), ("", ""))
        self.assertNotIn("carimbo", fontes["uf"])

    def test_checkpoint_e_idempotente(self):
        uma = Leitura.de_dict(self._antiga())
        duas = Leitura.de_dict(json.loads(json.dumps(uma.para_dict())))
        self.assertEqual(uma.ressalvas, duas.ressalvas)
        self.assertEqual(uma.valores, duas.valores)

    def test_leitura_boa_carrega_igual(self):
        l = Leitura(arquivo="x.tif", valores={"uf": "SP", "cidade": "  São  Paulo "}, confiancas={"uf": 0.9})
        self.assertEqual(Leitura.de_dict(l.para_dict()).para_dict(), l.para_dict())


if __name__ == "__main__":
    unittest.main()
