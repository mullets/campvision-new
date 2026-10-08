"""Passada 3 em código (docs/metodo-de-leitura.md §4.1, §4.2 e §4.4): moda, outliers, ressalvas, sem chamar o modelo.

Os casos vêm da especificação: as datas "85" x "83" do McDonald's, o 'HOSWALDO' de OCR e a prancha de outro arquiteto
dentro de um grupo de 30 (Salvador Candia no Safra Mooca).
"""
import unittest

from nucleo import consenso as c
from nucleo import grupos
from nucleo.config import Config
from nucleo.esquema import Leitura


def folha(arquivo, conf=0.9, **valores):
    return Leitura(arquivo=arquivo, valores=dict(valores), confiancas={k: conf for k, v in valores.items() if v})


class ClienteQueNaoPodeSerChamado:
    def chamar(self, *a, **k):
        raise AssertionError("o modo 'codigo' não pode chamar o modelo")


class ConsensoDeCampo(unittest.TestCase):
    def test_caso_guia_85_contra_83(self):
        # 0103, 0111 e 0112 lidas como 1985; as outras sete da série são de 1983. Leitura mais provável: 83.
        votos = [(f"{n:04d}", "1985" if n in (103, 111, 112) else "1983", 0.9) for n in range(103, 113)]
        r = c.consenso_de_campo("ano", votos)
        self.assertEqual((r.valor, r.votos, r.leituras, r.empate), ("1983", 7, 10, False))
        self.assertEqual(sorted(a for a, _ in r.outliers), ["0103", "0111", "0112"])
        self.assertTrue(all(v == "1985" for _, v in r.outliers))

    def test_grafia_mais_completa_dentro_do_vencedor(self):
        votos = [("a", "Oswaldo Correa Gonçalves", .9)] * 3 + [("b", "OSWALDO CORREA GONCALVES", .9)] * 2
        r = c.consenso_de_campo("arquiteto", votos)
        self.assertEqual(r.valor, "Oswaldo Correa Gonçalves")     # mesma extensão: vence a mais frequente
        self.assertEqual(r.outliers, [])                          # só diferem em acento e caixa: é a MESMA leitura
        # Texto realmente diferente NÃO é a mesma leitura: aqui é empate 1 x 1 e vence a de maior confiança (com aviso de empate).
        r2 = c.consenso_de_campo("endereco", [("a", "R. Augusta", .9), ("b", "R. Augusta, 1200", .5)])
        self.assertEqual((r2.valor, r2.empate), ("R. Augusta", True))
        self.assertEqual(r2.outliers, [("b", "R. Augusta, 1200")])

    def test_variante_isolada_e_mais_longa_nao_sequestra_a_canonica(self):
        # 'HOSWALDO' (um H espúrio de OCR) venceria pela regra antiga 'a mais completa vence'. Pela moda, é outlier.
        votos = [(f"f{i}", "OSWALDO CORREA GONÇALVES", .9) for i in range(9)] + [("f9", "HOSWALDO CORREA GONÇALVES", .6)]
        r = c.consenso_de_campo("arquiteto", votos)
        self.assertEqual(r.valor, "OSWALDO CORREA GONÇALVES")
        self.assertEqual(r.outliers, [("f9", "HOSWALDO CORREA GONÇALVES")])

    def test_empate_e_desempatado_pela_confianca_e_avisa(self):
        r = c.consenso_de_campo("cidade", [("a", "Santos", .9), ("b", "Santos", .9), ("c", "Santo André", .5), ("d", "Santo André", .5)])
        self.assertEqual(r.valor, "Santos")
        self.assertTrue(r.empate)

    def test_campo_que_ninguem_leu_fica_none_nunca_inventado(self):
        self.assertIsNone(c.consenso_de_campo("uf", [("a", "", 0), ("b", None, 0), ("c", "  ", .9), ("d", "--", .9)]).valor)


class Aplicar(unittest.TestCase):
    def grupo(self):
        return [folha(f"{i:02d}", projeto="Teatro de Santos", arquiteto="José Carlos Bellucci", ano="1983", cidade="Santos")
                for i in range(1, 7)]

    def test_projeto_vai_para_todas_guardando_o_lido(self):
        ls = self.grupo()
        ls[2].valores["cidade"] = "Santo"          # erro de leitura isolado
        c.aplicar(ls, c.consenso_do_grupo(ls))
        self.assertTrue(all(l.valores["cidade"] == "Santos" for l in ls))
        self.assertEqual(ls[2].lidos_originais["cidade"], "Santo")           # nunca sobrescrever em silêncio
        self.assertEqual(ls[2].outliers, {"cidade": "Santo"})
        self.assertTrue(ls[2].ressalvas and "conferir no original" in ls[2].ressalvas[0])
        self.assertEqual(ls[0].outliers, {})

    def test_ano_de_cada_prancha_fica_intacto_e_o_do_grupo_vai_em_ano_do_projeto(self):
        ls = self.grupo()
        ls[0].valores["ano"] = ls[1].valores["ano"] = "1985"
        ls[2].valores["ano"] = "1985"
        ls[3].valores["ano"] = "1985"
        ls[4].valores["ano"] = ls[5].valores["ano"] = "1983"      # 4 x 2 para 1985: o consenso é 1985
        c.aplicar(ls, c.consenso_do_grupo(ls))
        self.assertTrue(all(l.ano_do_projeto == "1985" for l in ls))
        self.assertEqual([l.valores["ano"] for l in ls], ["1985"] * 4 + ["1983"] * 2)       # cada prancha preserva o seu
        self.assertEqual(ls[4].outliers, {"ano": "1983"})

    def test_prancha_de_outro_arquiteto_num_grupo_de_30_nao_e_corrigida_pelo_consenso(self):
        ls = [folha(f"{i:02d}", projeto="Safra Mooca", arquiteto="José Carlos Bellucci") for i in range(29)]
        ls.append(folha("29", projeto="Safra Mooca", arquiteto="Salvador Candia"))
        r = c.consenso_do_grupo(ls)
        c.aplicar(ls, r)
        self.assertEqual(r["arquiteto"].valor, "José Carlos Bellucci")
        self.assertEqual(ls[29].valores["arquiteto"], "Salvador Candia")                    # NÃO foi corrigida
        self.assertEqual(ls[29].outliers, {"arquiteto": "Salvador Candia"})                 # e está marcada
        self.assertEqual(ls[0].valores["arquiteto"], "José Carlos Bellucci")

    def test_aplicar_duas_vezes_da_o_mesmo_resultado(self):
        ls = self.grupo(); ls[1].valores["cidade"] = "Santo"
        r = c.consenso_do_grupo(ls)
        c.aplicar(ls, r)
        antes = [l.para_dict() for l in ls]
        c.aplicar(ls, c.consenso_do_grupo(ls))
        self.assertEqual([l.para_dict() for l in ls], antes)

    def test_serializacao_leva_outliers_e_ressalvas(self):
        ls = self.grupo(); ls[0].valores["cidade"] = "Santo"
        c.aplicar(ls, c.consenso_do_grupo(ls))
        d = ls[0].para_dict()
        self.assertEqual(d["outliers"], {"cidade": "Santo"})
        volta = Leitura.de_dict(d)
        self.assertEqual((volta.outliers, volta.ressalvas), (ls[0].outliers, ls[0].ressalvas))


class ModoCodigoEmGrupos(unittest.TestCase):
    def test_modo_codigo_nao_chama_o_modelo(self):
        ls = [folha(f"{i}", projeto="Teatro de Santos", cliente="Prefeitura", ano="1968") for i in range(4)]
        ls[3].valores["cliente"] = "Prefeitur"
        canon, t_in, t_out = grupos.consolidar(ls, ClienteQueNaoPodeSerChamado(), modo="codigo")
        self.assertEqual((t_in, t_out), (0, 0))
        self.assertEqual(canon["Teatro de Santos"]["cliente"], "Prefeitura")
        self.assertEqual(canon["Teatro de Santos"]["ano_do_projeto"], "1968")
        self.assertTrue(all(l.grupo == "Teatro de Santos" for l in ls))
        self.assertEqual(ls[3].lidos_originais["cliente"], "Prefeitur")

    def test_padrao_continua_sendo_o_modelo(self):
        self.assertEqual(Config().consolidacao, "modelo")


if __name__ == "__main__":
    unittest.main()
