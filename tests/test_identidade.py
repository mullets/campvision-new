"""Auditoria F026 (13/10): identificação do lote pelo CARIMBO antes do código.

Gabarito tirado da auditoria folha a folha (claude/f026-auditoria-folha-a-folha-2026-10-13.md).
As regras são gerais (obra + local + identificadores × base do fundo); os nomes abaixo são só os
casos reais usados como teste de aceite.
"""

from __future__ import annotations

import json
import unittest
from unittest import mock

from nucleo import entrada, estrutura, identidade as I
from nucleo.esquema import Leitura
from nucleo.livro import Livro
from nucleo import fundos
from tests.test_entrada import Base, PainelFalso, conferir_falso, exif_falso
from tests.test_nucleo import ClienteFalso, campo, resposta_padrao

BASE = [{"codigo": c, "projeto": t} for c, t in [
    ("F026-P0002", "Palácio da Justiça, Sta. Catarina"), ("F026-P0003", "Clube Sírio, Av. Indianópolis"),
    ("F026-P0004", "Clube Sírio (S. Festas)"), ("F026-P0007", "IAMSPE, Av. Ibirapuera"),
    ("F026-P0010", "Cia. Sto Amaro"), ("F026-P0030", "Ed. de Apartamentos, Rua Mangueira"),
    ("F026-P0084", "Ed. Yakulti, Al. Santos"), ("F026-P0086", "Sabesp, Praça São Miguel"),
    ("F026-P0087", "Sabesp, Praça Vila Alpina"), ("F026-P0088", "Sabesp, Praça Vila Deodoro"),
    ("F026-P0089", "Sabesp, Praça Vila Formosa"), ("F026-P0092", "Jockey Club, Rua Ouvidor Peleja"),
    ("F026-P0095", "Ed. Marambaia, R. Batataes"), ("F026-P0111", "Comurb, Rua Pavan - Vila Sta. Catarina"),
    ("F026-P0113", "Miguel Buirham, Av. Morumbi"), ("F026-P0168", "Soc. Antioquina do Brasil, Rua Cubatão"),
    ("F026-P0180", "Comurb, Vila Sta. Catarina")]]


def folha(cod: str, transcricao: str = "", **valores) -> Leitura:
    return Leitura(arquivo=f"{cod}.jpg", valores=dict(valores), carimbo_encontrado=bool(valores),
                   transcricao_integral=transcricao)


def um_grupo(pasta: str = "", **valores) -> dict:
    a = I.analisar({"a": folha("a", **valores), "b": folha("b", **valores)})
    return I.casar_com_base(a.grupos[0], BASE, I.pista_da_pasta(pasta))


class TestGabaritoBase(unittest.TestCase):
    """T3: casar com a base do fundo antes de reservar."""

    def test_nome_da_pasta_ou_carimbo_acham_o_projeto_da_base(self):
        casos = [
            ("P0002 - Residência Miguel Burihan - 1975 - 1977", {"projeto": "Residência Sr. Miguel Burihan"}, "F026-P0113"),
            ("P0004 - Sociedade Antioquina do Brasil - 1970",
             {"projeto": "Sociedade Antioquina do Brasil", "endereco": "R. Cubatão 726"}, "F026-P0168"),
            ("P0010 - Edifício Marambaia - 1970 - 1971", {"projeto": "Edifício Marambaia", "endereco": "R. Batataes 170"},
             "F026-P0095"),
            ("T102_Edifício Jockey Club São Paulo", {"projeto": "Jockey Club de São Paulo"}, "F026-P0092"),
            ("T098_Edifício de apartamentos com garagem_Alameda Santos n771",
             {"projeto": "Edifício de apartamentos com garagem", "cliente": "Yakult S.A.", "endereco": "Alameda Santos 771"},
             "F026-P0084"),
            ("P0001 - predio de aptos", {"projeto": "Edifício de apartamentos",
                                         "endereco": "R. das Mangueiras esq. prolongamento da Av. Brasil, Vila Mariana"},
             "F026-P0030"),
            ("P0007 - Prédio de Apartamentos com Garagem",
             {"projeto": "Conjunto Residencial com Garagens", "cliente": "COMURB",
              "endereco": "R. Pavan esq. R. Dr. Djalma Pinheiro Franco"}, "F026-P0111"),
        ]
        for pasta, valores, certo in casos:
            with self.subTest(pasta=pasta):
                achado = um_grupo(pasta, **valores)
                self.assertEqual((achado.get("forte") or {}).get("codigo"), certo, achado)

    def test_numero_da_estacao_nunca_e_codigo(self):
        # P0002 da estação ≠ F026-P0002 (Palácio da Justiça) da base.
        achado = um_grupo("P0002 - Residência Miguel Burihan", projeto="Residência Sr. Miguel Burihan")
        self.assertNotEqual(achado["forte"]["codigo"], "F026-P0002")

    def test_dois_candidatos_pergunta_nunca_escolhe(self):
        achado = um_grupo(projeto="Conjunto Residencial com Garagens", cliente="COMURB",
                          endereco="R. das Laranjeiras esq. Dr. Djalma Pinheiro Franco")
        self.assertNotIn("forte", achado)
        self.assertEqual({c["codigo"] for c in achado["candidatos"]}, {"F026-P0111", "F026-P0180"})
        achado = um_grupo(projeto="Esporte Clube Sírio - Sede Social", endereco="Av. Indianópolis 1192")
        self.assertEqual({c["codigo"] for c in achado["candidatos"]}, {"F026-P0003", "F026-P0004"})

    def test_sem_projeto_na_base_nao_inventa(self):
        self.assertEqual(um_grupo("T099_Pousada Chalés do Lago", projeto="Pousada Chalés do Lago",
                                  cidade="Três Ranchos"), {})


class TestLeituraReal(unittest.TestCase):
    """Saída real do --reidentificar F026 (14/10): o que estava errado."""

    def test_lixo_da_ferramenta_vira_vazio(self):
        self.assertEqual(I.limpar_valor('<parameter name="valor">null'), "")
        self.assertEqual(I.limpar_valor('<parameter name="valor">CONJUNTO RESIDENCIAL'), "CONJUNTO RESIDENCIAL")
        self.assertEqual(I.limpar_valor('<parameter name="valor">null</parameter>\n<parameter name="confianca">0'), "")

    def test_endereco_mal_lido_nao_separa_o_marambaia(self):
        ruas = ["RUA BATATAES, 170"] * 24 + ["RUA BAHIA", "RUA IMITATAS", "RUA BANDAIRAS", "RUA WASHTAS", "RUA BUZATINS"]
        leituras = {f"d{i:02d}": folha(f"d{i:02d}", projeto="EDIFICIO MARAMBAIA" if i % 5 else "EDIFICIO MARAMBIA",
                                       endereco=r) for i, r in enumerate(ruas)}
        a = I.analisar(leituras, base=BASE)
        self.assertEqual(len(a.grupos), 1)
        self.assertEqual(a.mesmo_endereco, [])
        self.assertEqual(I.casar_com_base(a.grupos[0], BASE)["forte"]["codigo"], "F026-P0095")

    def test_endereco_do_escritorio_no_carimbo_nao_impede(self):
        achado = um_grupo(projeto="RESIDENCIA - Sr. MIGUEL BURIHAN", endereco="RUA 13 DE MAIO, 1954 CONJ. 121")
        self.assertEqual(achado["forte"]["codigo"], "F026-P0113")
        achado = um_grupo(projeto="JOCKEY CLUB DE SÃO PAULO", endereco="RUA JOSÉ BONIFÁCIO")
        self.assertEqual(achado["forte"]["codigo"], "F026-P0092")


class TestAgrupamento(unittest.TestCase):
    """T2/T5/T6: agrupar pelo carimbo, não pela pasta."""

    def t100(self) -> dict:
        return {
            "d01": folha("d01", titulo_prancha="Capa com croquis"),
            "d03": folha("d03", projeto="PRAÇAS / SABESP - COGEP", endereco="RUA PARAMU · VILA ALPINA - S. PAULO"),
            "d04": folha("d04", projeto="Praças SABESP COGEP", endereco="Trav. Diogo Calado, São Miguel Paulista"),
            "d06": folha("d06"),
            "d08": folha("d08", projeto="Praças SABESP-COGEP programa", endereco="R. Paramu, Vila Alpina"),
            "d09": folha("d09", projeto="PRAÇAS SABESP", endereco="R. Cel. Rodrigues, Vila Formosa"),
            "d10": folha("d10", projeto="PRAÇAS SABESP", endereco="R. Cel. Rodrigues - Vila Formosa"),
            "d11": folha("d11", projeto="PRAÇAS SABESP", endereco="VILA DEODORO", data="AGOSTO 1974"),
            "d12": folha("d12", projeto="Piranga — programa — estudo preliminar áreas SABESP"),
            "d14": folha("d14"),
        }

    def test_pasta_com_cinco_obras_vira_cinco_grupos(self):
        # Sem a base, obra igual com endereço diferente fica junta (endereço mal lido não separa)…
        self.assertEqual(len(I.analisar(self.t100()).grupos[0].folhas), 6)
        # …com a base do fundo, cada praça tem projeto próprio e separa.
        a = I.analisar(self.t100(), base=BASE)
        grupos = {tuple(g.folhas): I.casar_com_base(g, BASE) for g in a.grupos}
        fortes = {k: v["forte"]["codigo"] for k, v in grupos.items() if "forte" in v}
        self.assertEqual(fortes, {("d03", "d08"): "F026-P0087", ("d09", "d10"): "F026-P0089",
                                  ("d04",): "F026-P0086", ("d11",): "F026-P0088"})
        self.assertIn(("d12",), grupos)                      # Piranga: grupo próprio, sem projeto forte
        self.assertEqual(a.sem_identificacao, ["d06", "d14"])  # não encaixa à força no maior
        self.assertEqual(list(a.capas), ["d01"])
        self.assertFalse(any("marua" in I.normalizar(g.titulo) for g in a.grupos))  # Maruá só no nome da pasta

    def test_lote_homogeneo_nao_tem_divergente(self):
        leituras = {f"d{i:02d}": folha(f"d{i:02d}", projeto="Edifício Marambaia" if i % 3 else "ED. MARAMBAIA",
                                       endereco="R. Batataes 170" if i % 2 else "") for i in range(1, 38)}
        leituras["d40"] = folha("d40", titulo_prancha="detalhe de esquadria")  # sem carimbo: é do lote
        a = I.analisar(leituras)
        self.assertEqual(len(a.grupos), 1)
        self.assertEqual(len(a.grupos[0].folhas), 37)

    def test_duas_series_e_dois_proprietarios_no_mesmo_endereco_sao_um_projeto(self):
        leituras = {
            **{f"a{i}": folha(f"a{i}", projeto="Substituição de projeto aprovado", cliente="J.A.M. Empreendimentos",
                             endereco="R. das Mangueiras esq. prolongamento da Av. Brasil", folha=f"{i}/4")
               for i in range(1, 5)},
            **{f"b{i}": folha(f"b{i}", projeto="Edifício de apartamentos", escritorio="Designio",
                             endereco="Rua das Mangueiras x Av. Brasil - Vila Mariana", folha=f"0{i}")
               for i in range(1, 8)},
            "c12": folha("c12", projeto="Edifício residencial", cliente="Espólio de João Pires Germano",
                         endereco="R. das Mangueiras esquina prolongamento Av. Brasil"),
        }
        a = I.analisar(leituras)
        self.assertEqual(len(a.grupos), 1, [g.titulo for g in a.grupos])

    def test_mesmo_contribuinte_junta(self):
        a = I.analisar({
            "x": folha("x", "contribuinte 089.149.0001", projeto="Conjunto Residencial com Garagem",
                       endereco="R. das Laranjeiras esq. Dr. Djalma Pinheiro Franco"),
            "y": folha("y", "Contribuinte nº 089.149.0001-5 terreno 2.500 m2", projeto="Prédio de apartamentos",
                       endereco="R. Pavan esq. R. Dr. Djalma Pinheiro Franco"),
        })
        self.assertEqual(len(a.grupos), 1)
        self.assertIn("contribuinte 0891490001", a.grupos[0].ids)

    def test_obras_diferentes_no_mesmo_endereco_viram_pergunta(self):
        f = lambda c, obra, esc, data: folha(c, projeto=obra, endereco="Av. Indianópolis 1192",  # noqa: E731
                                             cliente="Esporte Clube Sírio", escritorio=esc, data=data)
        a = I.analisar({
            "a1": f("a1", "Cobertura do Centro Esportivo", "Mannesmann MERO", "06/11/1979"),
            "a2": f("a2", "Cobertura do Centro Esportivo", "Mannesmann MERO", "06/11/1979"),
            "b1": f("b1", "Sede Social", "Procion Designio", "09/1992"),
            "b2": f("b2", "Sede Social", "Procion Designio", "09/1992"),
            "c1": f("c1", "Portaria", "Pedro Paulo de Melo Saraiva e Sami Bussab", ""),
            "c2": f("c2", "Portaria", "Pedro Paulo de Melo Saraiva e Sami Bussab", ""),
        })
        self.assertEqual(len(a.grupos), 3)
        self.assertEqual(len(a.mesmo_endereco), 1)
        self.assertEqual(sorted(a.mesmo_endereco[0]), [0, 1, 2])

    def test_capa_envelope_e_tira(self):
        self.assertTrue(I.motivo_capa(folha("e", titulo_prancha="ENVELOPE")))
        capa = Leitura(arquivo="c.jpg", transcricao_integral="T098", carimbo_encontrado=False)
        self.assertIn("código escrito", I.motivo_capa(capa))
        self.assertIn("tira", I.motivo_capa(Leitura(arquivo="t.jpg"), proporcao=4.2))
        self.assertEqual(I.motivo_capa(folha("p", projeto="Ed. Marambaia")), "")

    def test_carimbo_vai_com_o_que_foi_lido(self):
        l = folha("x", "PRAÇAS / SABESP-COGEP · RUA PARAMU · alvará 41.928", projeto="PRAÇAS / SABESP - COGEP",
                  endereco="RUA PARAMU · VILA ALPINA", escritorio="Sami Bussab, Satoru Nagai", data="AGOSTO 1974")
        l.campos_da_pasta = ["cliente"]
        l.valores["cliente"] = "Edifício Maruá"   # veio da pasta: não é o que o carimbo diz
        c = I.carimbo(l)
        self.assertEqual((c["obra"], c["escritorio"], c["alvara"], c["cliente"]),
                         ("PRAÇAS / SABESP - COGEP", "Sami Bussab, Satoru Nagai", "41928", ""))


# ---------------------------------------------------------------- ponta a ponta

class PainelComBase(PainelFalso):
    def __init__(self, base, decisoes=None):
        super().__init__()
        self.base = base
        self.decisoes = decisoes if decisoes is not None else {}
        self.pedidos: list[dict] = []
        self.ultima_reserva = {}

    def contexto(self, fundo=""):
        return {"projetos": list(self.base)}

    def reservar(self, fundo_codigo, titulo, chave_reserva, **extra):
        self.pedidos.append({"titulo": titulo, **extra})
        if extra.get("candidatos"):
            escolhido = self.decisoes.get(chave_reserva)
            if not escolhido:
                self.ultima_reserva = {"pendente": True, "existente": False, "mensagem": "aguardando decisão"}
                self.decisoes.setdefault("_abertas", []).append(chave_reserva)
                return None
            self.ultima_reserva = {"pendente": False, "existente": True}
            return escolhido
        self.ultima_reserva = {"pendente": False, "existente": False}
        return super().reservar(fundo_codigo, titulo, chave_reserva)


def respostas(*valores: dict) -> list[dict]:
    return [resposta_padrao(projeto=campo(v.get("projeto", "")), endereco=campo(v.get("endereco", "")),
                            cliente=campo(v.get("cliente", "")), titulo_prancha=campo(v.get("titulo_prancha", "")),
                            arquiteto=campo("Sami Bussab"), carimbo_encontrado=bool(v.get("projeto")))
            for v in valores]


class TestPontaAPontaIdentidade(Base):
    def rodar_com(self, cliente, painel):
        self.painel = painel
        livro = Livro(self.acervo, self.estado / "reserva")
        r = entrada.Recebedor(self.config, cliente, painel, livro, self.estado, fundos.Tabela(fundos.EMBUTIDA))
        with mock.patch.object(entrada.mod_metadados, "gravar_em_lote", side_effect=exif_falso), \
                mock.patch.object(entrada, "conferir_exif", side_effect=conferir_falso):
            return [r.processar(u) for u in r.pendentes()]

    def projetos(self) -> dict[str, list[str]]:
        base = self.acervo / "F026 - SBU Sami Bussab" / "01 - Projetos"
        return {estrutura.codigo_da_pasta(p): sorted(a.stem for a in p.rglob("*.jpg") if "catalogacao" not in a.parts)
                for p in base.iterdir() if p.is_dir()}

    def test_pasta_com_varias_obras_vai_para_os_codigos_certos(self):
        self.scan("F026", "01 - Desenhos e pranchas", "T100_Praças SABESP_COGESP, … _ Edifício Maruá",
                  n=8, formatos=("jpg",))
        cliente = ClienteFalso(respostas(
            {"titulo_prancha": "Capa com croquis"},
            {"projeto": "PRAÇAS / SABESP - COGEP", "endereco": "RUA PARAMU · VILA ALPINA - S. PAULO"},
            {"projeto": "Praças SABESP COGEP", "endereco": "Trav. Diogo Calado, São Miguel Paulista"},
            {"projeto": "Praças SABESP-COGEP programa", "endereco": "R. Paramu, Vila Alpina"},
            {"projeto": "PRAÇAS SABESP", "endereco": "R. Cel. Rodrigues, Vila Formosa"},
            {"projeto": "PRAÇAS SABESP", "endereco": "R. Cel. Rodrigues - Vila Formosa"},
            {"projeto": "PRAÇAS SABESP", "endereco": "VILA DEODORO"},
            {"projeto": "Piranga — programa — estudo preliminar áreas SABESP"},
        ))
        [r] = self.rodar_com(cliente, PainelComBase(BASE))
        self.assertEqual(r.status, entrada.PRONTO, r.motivo)
        projetos = self.projetos()
        self.assertEqual(sorted(projetos), ["F026-P0086", "F026-P0087", "F026-P0088", "F026-P0089"])
        self.assertEqual([len(projetos[c]) for c in sorted(projetos)], [1, 4, 1, 2])  # P0087 leva capa + Piranga
        self.assertEqual(len(self.painel.reservas), 0)                                # nada reservado à toa
        self.assertFalse(any(estrutura.e_provisorio(a[0]) for a in self.painel.avisos))
        self.assertEqual({a[0] for a in self.painel.avisos if a[2] == "pronto"}, set(projetos))
        # P0087: Vila Alpina publica; capa e a folha da Piranga ficam fora, com o que o carimbo diz.
        pasta = next(p for p in (self.acervo / "F026 - SBU Sami Bussab" / "01 - Projetos").iterdir()
                     if p.name.startswith("F026-P0087"))
        pacote = json.loads((pasta / "catalogacao" / "pacote_tainacan.json").read_text())
        self.assertEqual(len(pacote["documentos"]), 2)
        self.assertTrue(all(not d["projeto_divergente"] for d in pacote["documentos"]))
        motivos = " | ".join(b for d in pacote["retirados"] for b in d["bloqueios"])
        self.assertIn("não é prancha", motivos)
        self.assertIn("Piranga", motivos)
        self.assertEqual(pacote["documentos"][0]["carimbo"]["obra"], "PRAÇAS / SABESP - COGEP")
        self.assertIn("transcricao_integral", pacote["documentos"][0]["lido"])
        # A pasta provisória sumiu; o rastreio dela ficou no projeto principal.
        self.assertFalse(any((self.acervo / "F026 - SBU Sami Bussab" / estrutura.PASTA_IDENTIFICACAO).iterdir()))
        self.assertTrue(list((pasta / "catalogacao" / "identificacao").rglob("identificacao.txt")))
        # Livro: a história do documento final começa no "copiado".
        livro = Livro(self.acervo)
        doc = projetos["F026-P0086"][0].replace("-1968-", "-0000-")   # antes do ano lido renomear
        self.assertIn("copiado", [l["acao"] for l in livro.historico(doc)])

    def test_dois_candidatos_espera_a_pessoa_e_nao_le_de_novo(self):
        self.scan("F026", "01 - Desenhos e pranchas", "P0005 - Conjunto Residêncial com Garagem", n=1,
                  formatos=("jpg",))
        cliente = ClienteFalso(respostas({"projeto": "Conjunto Residencial com Garagens", "cliente": "COMURB",
                                          "endereco": "R. das Laranjeiras esq. Dr. Djalma Pinheiro Franco"}))
        decisoes: dict = {}
        painel = PainelComBase(BASE, decisoes)
        [r] = self.rodar_com(cliente, painel)
        self.assertEqual(r.status, entrada.AGUARDANDO)
        self.assertIn("F026-P0111 × F026-P0180", r.motivo)
        self.assertEqual(self.projetos(), {})                  # nada em "01 - Projetos" ainda
        self.assertEqual({c["codigo"] for c in painel.pedidos[-1]["candidatos"]}, {"F026-P0111", "F026-P0180"})
        chamadas = len(cliente.chamadas)
        # A pessoa escolhe P0111 no painel: a próxima rodada termina, sem nova leitura.
        decisoes[decisoes["_abertas"][0]] = "F026-P0111"
        [r] = self.rodar_com(cliente, painel)
        self.assertEqual(r.status, entrada.PRONTO, r.motivo)
        self.assertEqual(list(self.projetos()), ["F026-P0111"])
        self.assertEqual(len(cliente.chamadas), chamadas)


class TestReidentificar(TestPontaAPontaIdentidade):
    """O que já entrou errado (lotes de 08/10): plano e conserto pelo carimbo, sem API."""

    def test_projeto_misturado_vai_para_os_codigos_certos(self):
        from nucleo import planilha, reidentificar

        self.scan("F026", "01 - Desenhos e pranchas", "Tarumã", n=4, formatos=("jpg",))
        [r] = self.rodar_com(ClienteFalso([resposta_padrao(arquiteto=campo("Sami Bussab"))]), PainelFalso())
        self.assertEqual(r.status, entrada.PRONTO, r.motivo)
        pasta = r.pasta_projeto
        # Simula o estado errado: quatro folhas de três obras no mesmo código.
        cat = pasta / "catalogacao"
        leituras = planilha.ler_json(cat / "leituras.json")
        lidos = [{"projeto": "Praças SABESP", "endereco": "R. Cel. Rodrigues, Vila Formosa"},
                 {"projeto": "Praças SABESP", "endereco": "R. Cel. Rodrigues - Vila Formosa"},
                 {"projeto": "PRAÇAS / SABESP - COGEP", "endereco": "RUA PARAMU · VILA ALPINA"},
                 {"projeto": "PRAÇAS SABESP", "endereco": "R. Paramu - Vila Alpina"}]
        for l, v in zip(sorted(leituras, key=lambda l: l.arquivo), lidos):
            l.valores.update(v)
            l.lidos_originais = dict(l.valores)
        planilha.escrever_json(leituras, cat / "leituras.json")
        painel = PainelComBase(BASE)
        plano = reidentificar.planejar(pasta, BASE, self.estado)
        self.assertEqual(sorted(g["destino"] for g in plano["grupos"]), ["F026-P0087", "F026-P0089"])
        self.assertIn("F026-P0087", reidentificar.texto(plano))
        antes = sorted(p.name for p in pasta.rglob("*.jpg") if "catalogacao" not in p.parts)
        tocados = reidentificar.aplicar(plano, self.acervo, self.estado, BASE, Livro(self.acervo), painel)
        self.assertEqual(set(tocados), {"F026-P0001", "F026-P0087", "F026-P0089"})
        projetos = self.projetos()
        self.assertEqual((len(projetos["F026-P0087"]), len(projetos["F026-P0089"]), len(projetos["F026-P0001"])),
                         (2, 2, 0))
        self.assertEqual(len(antes), 4)
        destino = next(p for p in pasta.parent.iterdir() if p.name.startswith("F026-P0089"))
        pacote = json.loads((destino / "catalogacao" / "pacote_tainacan.json").read_text())
        self.assertEqual(len(pacote["documentos"]), 2)
        self.assertEqual(pacote["projeto_codigo"], "F026-P0089")
        self.assertIn(("F026-P0089", "pronto"), {(a[0], a[2]) for a in painel.avisos})


if __name__ == "__main__":
    unittest.main()
