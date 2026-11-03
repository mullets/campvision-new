"""UX-60: "resposta do modelo quebrada" não pode descartar todos os campos da folha.

Caso real: F001-P0033-1979-S01-D00001 com ~15 campos "valor descartado: resposta do modelo
quebrada". Agora: (1) conserta o JSON mal formado; (2) se ainda quebrado, UMA nova tentativa
(custo contado); (3) só então descarta, e registra de forma contável (erros.json + execucao).
Nenhuma chamada real: ClienteFalso.
"""

from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from nucleo.config import Config
from nucleo.entrada import RESPOSTA_QUEBRADA, erros_das_leituras, execucao
from nucleo.esquema import CAMPOS_DO_MODELO
from nucleo.reparo import json_reparado
from nucleo.visao import LeitorDeCarimbo
from tests.test_nucleo import ClienteFalso, campo, prancha_falsa, resposta_padrao

P = "<" + "antml:" + "parameter"
FECHA = "</" + "antml:" + "parameter>"
NOMES = [c.nome for c in CAMPOS_DO_MODELO]


def ler(respostas):
    cliente = ClienteFalso(respostas)
    with TemporaryDirectory() as tmp:
        caminho = prancha_falsa(Path(tmp) / "F001-P0033-1979-S01-D00001.jpg")
        l = LeitorDeCarimbo(Config(), cliente).ler(caminho)
    return l, cliente


def quebrada_em_tudo() -> dict:
    """Todos os campos com texto dos dois lados da marcação (ambíguo: não dá para consertar)."""
    return resposta_padrao(**{n: campo(f'{n.upper()} {P} name="valor">X', 0.8) for n in NOMES})


class TestJsonReparado(unittest.TestCase):
    def test_conserta_o_seguro(self):
        casos = {
            '```json\n{"valor": "SP", "confianca": 0.9}\n```': {"valor": "SP", "confianca": 0.9},
            'Aqui está: {"valor": "SP", "confianca": 0.9} espero ter ajudado': {"valor": "SP", "confianca": 0.9},
            '{"valor": "SP", "confianca": 0.9,}': {"valor": "SP", "confianca": 0.9},
            '{“valor”: “SP”, “confianca”: 0.9}': {"valor": "SP", "confianca": 0.9},
            '{"valor": "SÃO PAULO", "confianca": 0.9, "alternativas": ["SAO PA': {"valor": "SÃO PAULO", "confianca": 0.9,
                                                                                  "alternativas": ["SAO PA"]},
            '{"valor": "SP", "confianca": 0.': {"valor": "SP"},
        }
        for texto, esperado in casos.items():
            self.assertEqual(json_reparado(texto), esperado, texto)

    def test_aspas_tipograficas_dentro_do_valor_ficam(self):
        self.assertEqual(json_reparado('{"valor": "Escritório “Rino Levi”"}'),
                         {"valor": "Escritório “Rino Levi”"})

    def test_texto_sem_json_nao_vira_nada(self):
        for texto in ("SÃO PAULO", "", "1:50", None, 3):
            self.assertIsNone(json_reparado(texto))


class TestLeitorConserta(unittest.TestCase):
    def test_campos_em_texto_json_mal_formado_sao_lidos_sem_nova_chamada(self):
        torta = resposta_padrao(
            arquiteto='```json\n{"valor": "RUY OHTAKE", "confianca": 0.9,}\n```',
            cidade='{“valor”: “PIRACICABA”, “confianca”: 0.8}',
            uf='{"valor": "SP", "confianca": 0.9, "alternativas": ["S',
        )
        l, cliente = ler([torta])
        self.assertEqual(len(cliente.chamadas), 1)
        self.assertEqual((l.valores["arquiteto"], l.valores["cidade"], l.valores["uf"]),
                         ("RUY OHTAKE", "PIRACICABA", "SP"))
        self.assertEqual(l.resposta_quebrada, [])
        self.assertFalse(any("quebrada" in r for r in l.ressalvas), l.ressalvas)

    def test_parametros_colados_voltam_para_os_seus_campos(self):
        colado = (f'TEATRO MUNICIPAL{FECHA}\n{P} name="confianca">0.9{FECHA}\n'
                  f'{P} name="cidade">{{"valor": "PIRACICABA", "confianca": 0.8}}{FECHA}\n'
                  f'{P} name="uf">{{"valor": "SP", "confianca": 0.9}}')
        torta = resposta_padrao(projeto={"valor": colado, "confianca": 0.0})
        l, cliente = ler([torta])
        self.assertEqual(len(cliente.chamadas), 1)
        self.assertEqual(l.valores["projeto"], "TEATRO MUNICIPAL")
        self.assertEqual(l.confiancas["projeto"], 0.9)
        self.assertEqual((l.valores["cidade"], l.valores["uf"]), ("PIRACICABA", "SP"))
        self.assertEqual(l.resposta_quebrada, [])

    def test_resposta_inteira_em_texto_dentro_de_uma_chave(self):
        dentro = json.dumps(resposta_padrao(cidade=campo("PIRACICABA")), ensure_ascii=False)[:-1] + ",}"
        l, cliente = ler([{"resposta": "Segue o JSON:\n```json\n" + dentro + "\n```"}])
        self.assertEqual(len(cliente.chamadas), 1)
        self.assertEqual(l.valores["cidade"], "PIRACICABA")
        self.assertEqual(l.valores["projeto"], "CASA DA PRAIA")
        self.assertEqual(l.resposta_quebrada, [])


class TestNovaTentativa(unittest.TestCase):
    def test_quebrada_tenta_de_novo_e_fica_com_a_boa(self):
        boa = resposta_padrao(cidade=campo("PIRACICABA"), uf=campo("SP"))
        l, cliente = ler([quebrada_em_tudo(), boa])
        self.assertEqual(len(cliente.chamadas), 2)
        self.assertEqual((l.tokens_entrada, l.tokens_saida, l.passes), (2000, 200, 2))  # custo das duas
        self.assertEqual(l.novas_tentativas, 1)
        self.assertEqual((l.valores["projeto"], l.valores["cidade"]), ("CASA DA PRAIA", "PIRACICABA"))
        self.assertEqual(l.resposta_quebrada, [])
        self.assertFalse(any("quebrada" in r for r in l.ressalvas), l.ressalvas)

    def test_so_uma_nova_tentativa_e_depois_registra(self):
        l, cliente = ler([quebrada_em_tudo()])
        self.assertEqual(len(cliente.chamadas), 2)          # 1 nova tentativa, não mais
        self.assertEqual(l.resposta_quebrada, sorted(NOMES))
        erros = erros_das_leituras([l])
        contaveis = [e for e in erros if e["detalhe"].startswith(RESPOSTA_QUEBRADA + ":")]
        self.assertEqual(len(contaveis), 1)
        self.assertEqual(contaveis[0]["arquivo"], "F001-P0033-1979-S01-D00001")
        self.assertEqual((contaveis[0]["categoria"], contaveis[0]["gravidade"]), ("metadado", "corrigir"))
        ex = execucao(Config(), [l], 0.01, 1.0)
        self.assertEqual((ex["chamadas"], ex["novas_tentativas"], ex["folhas_resposta_quebrada"],
                          ex["campos_descartados"]), (2, 1, 1, len(NOMES)))

    def test_resposta_boa_nao_gasta_nova_chamada(self):
        l, cliente = ler([resposta_padrao()])
        self.assertEqual(len(cliente.chamadas), 1)
        self.assertEqual((l.novas_tentativas, l.resposta_quebrada), (0, []))


if __name__ == "__main__":
    unittest.main()
