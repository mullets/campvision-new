"""UX-43: o CAMP Vision reorganiza sozinho a pasta que o painel mandou para outro projeto (simula antes).

O painel (camp-painel destino_auto.py, CONTRATO_UNICO §5.2) lista em GET /api/campvision/destinos as
pastas cujo lote ele marcou "movendo para F0xx-P0yyy". O vigia, ocioso, roda o --reidentificar daquela
pasta: plano primeiro (simulação), confere com o pedido e só então aplica e avisa o painel.
Sem chamada real à API (ClienteFalso) nem ao painel (painel falso).
"""

from __future__ import annotations

import json
import unittest
from unittest import mock

from nucleo import entrada, estrutura, fundos, planilha, vigia
from nucleo import reidentificar as R
from nucleo.livro import Livro
from tests.test_entrada import Base, PainelFalso, conferir_falso, exif_falso
from tests.test_identidade import BASE, PainelComBase
from tests.test_nucleo import ClienteFalso, campo, resposta_padrao

PASTA = "Edifício Marambaia - 1970-1971"
MARAMBAIA = {"projeto": "Edifício Marambaia", "endereco": "R. Batataes 170"}


class PainelDestinos(PainelComBase):
    """Painel falso com a rota nova. `lista=None` = painel antigo (404)."""

    def __init__(self, base, lista=None, fecha_com_aviso=True):
        super().__init__(base)
        self.lista = lista
        self.fecha_com_aviso = fecha_com_aviso
        self.consultas = 0
        self.reidentificar: list[dict] = []

    def destinos(self):
        self.consultas += 1
        return None if self.lista is None else [dict(d) for d in self.lista]

    def aviso(self, codigo, pasta, status):
        super().aviso(codigo, pasta, status)
        if self.fecha_com_aviso and self.lista:     # o painel dá por concluído quando o material chega ao `para`
            self.lista = [d for d in self.lista if d["para"] != codigo]
        return True

    def reidentificar_lote(self, lote_id, destino, simular=True, motivo=""):
        self.reidentificar.append({"lote_id": lote_id, "destino": destino, "simular": simular})
        return 200, {"ok": True, "simulado": simular, "aplicado": not simular}


class TestDestinosDoPainel(Base):
    def setUp(self):
        super().setUp()
        self.scan("F026", "01 - Desenhos e pranchas", PASTA, n=3, formatos=("jpg",))
        livro = Livro(self.acervo, self.estado / "reserva")
        r = entrada.Recebedor(self.config, ClienteFalso([resposta_padrao(arquiteto=campo("Sami Bussab"))]),
                              PainelFalso(), livro, self.estado, fundos.Tabela(fundos.EMBUTIDA))
        with mock.patch.object(entrada.mod_metadados, "gravar_em_lote", side_effect=exif_falso), \
                mock.patch.object(entrada, "conferir_exif", side_effect=conferir_falso):
            [res] = [r.processar(u) for u in r.pendentes()]
        self.assertEqual(res.status, entrada.PRONTO, res.motivo)
        self.pasta = res.pasta_projeto
        self.de = estrutura.codigo_da_pasta(self.pasta)
        self.assertEqual(self.de, "F026-P0001")

    # ------------------------------------------------------------ apoio
    def carimbos(self, *lidos: dict) -> None:
        """O que o carimbo de cada folha diz (leituras.json), como no TestReidentificar."""
        cat = self.pasta / "catalogacao"
        leituras = sorted(planilha.ler_json(cat / "leituras.json"), key=lambda l: l.arquivo)
        for l, v in zip(leituras, lidos):
            l.valores.update(v)
            l.lidos_originais = dict(l.valores)
        planilha.escrever_json(leituras, cat / "leituras.json")

    def item(self, para="F026-P0095", **extra) -> dict:
        return {"id": 3, "lote_id": 41, "pasta": f"/mnt/qnap/ACERVOS_CAMP/F026 - SBU Sami Bussab/01 - Projetos/{self.pasta.name}",
                "nome_pasta": PASTA, "de": self.de, "para": para, "para_titulo": "Marambaia", "criado": False,
                "desde": "2026-10-27 22:10:00", "comando": f"vigia.py --reidentificar {self.de}", **extra}

    def vigia(self, painel, **cfg):
        for k, v in cfg.items():
            setattr(self.config, k, v)
        v = vigia.Vigia(self.config, ClienteFalso([resposta_padrao()]), self.estado, painel=painel)
        v.estado.situacao = "vigiando"
        return v

    def folhas(self, prefixo: str) -> list[str]:
        base = self.acervo / "F026 - SBU Sami Bussab" / "01 - Projetos"
        return sorted(a.stem for p in base.iterdir() if p.name.startswith(prefixo)
                      for a in p.rglob("*.jpg") if "catalogacao" not in a.parts)

    def livro(self) -> list[dict]:
        return list(Livro(self.acervo).todas())

    # ------------------------------------------------------------ casos
    def test_item_valido_simula_aplica_e_avisa(self):
        self.carimbos(MARAMBAIA, MARAMBAIA, MARAMBAIA)
        painel = PainelDestinos(BASE, [self.item()])
        v = self.vigia(painel)
        antes = self.folhas(self.de)
        self.assertEqual(len(antes), 3)
        with mock.patch("nucleo.reidentificar.planejar", wraps=R.planejar) as plan, \
                mock.patch("nucleo.reidentificar.aplicar", wraps=R.aplicar) as apl:
            self.assertEqual(v.uma_rodada(), 1)
            self.assertEqual((plan.call_count, apl.call_count), (1, 1))        # simulou ANTES de aplicar
        self.assertEqual(self.folhas(self.de), [])
        self.assertEqual(len(self.folhas("F026-P0095")), 3)                      # nada perdido, tudo no destino
        self.assertTrue(all(f.startswith("F026-P0095-") for f in self.folhas("F026-P0095")))
        self.assertIn(("F026-P0095", "pronto"), {(a[0], a[2]) for a in painel.avisos})
        self.assertEqual(painel.reidentificar, [])                                # o aviso bastou: não mexe no lote
        destino = next(p for p in self.pasta.parent.iterdir() if p.name.startswith("F026-P0095"))
        pacote = json.loads((destino / "catalogacao" / "pacote_tainacan.json").read_text())
        self.assertEqual(pacote["projeto_codigo"], "F026-P0095")
        # sem rota de admin (nem com sessão): só o roteiro
        chamadas = json.loads((self.pasta / "catalogacao" / "reidentificacao_painel.json").read_text())
        self.assertTrue(all("UX-43" in c["resultado"] for c in chamadas))
        self.assertTrue(any(l["acao"] == "refeito" and "UX-43" in l["detalhe"] for l in self.livro()))
        self.assertEqual(v.estado.situacao, "vigiando")

    def test_painel_ainda_lista_fecha_pelo_lote_simulando_antes(self):
        self.carimbos(MARAMBAIA, MARAMBAIA, MARAMBAIA)
        painel = PainelDestinos(BASE, [self.item()], fecha_com_aviso=False)
        self.assertEqual(self.vigia(painel).uma_rodada(), 1)
        self.assertEqual(painel.reidentificar, [{"lote_id": 41, "destino": "F026-P0095", "simular": True},
                                                {"lote_id": 41, "destino": "F026-P0095", "simular": False}])
        # o painel continua listando: NÃO repete (lembra o item)
        self.assertEqual(self.vigia(painel).uma_rodada(), 0)
        self.assertEqual(len(painel.reidentificar), 2)

    def test_simulacao_que_nao_bate_nao_aplica_registra_e_nao_repete(self):
        # o carimbo diz Jockey Club (F026-P0092), o painel pediu Marambaia (F026-P0095)
        jockey = {"projeto": "Jockey Club de São Paulo"}
        self.carimbos(jockey, jockey, jockey)
        painel = PainelDestinos(BASE, [self.item()])
        v = self.vigia(painel)
        with mock.patch("nucleo.reidentificar.aplicar") as apl:
            self.assertEqual(v.uma_rodada(), 0)
            apl.assert_not_called()
        self.assertEqual(len(self.folhas(self.de)), 3)                           # nada saiu do lugar
        self.assertEqual(self.folhas("F026-P0095"), [])
        erro = [l for l in self.livro() if l["acao"] == "erro" and "UX-43" in l["detalhe"]]
        self.assertEqual(len(erro), 1)
        self.assertIn("F026-P0092", erro[0]["detalhe"])
        status = json.loads((self.pasta / "status.json").read_text())
        self.assertEqual(status["status"], "pronto")
        self.assertIn("não reorganizou para F026-P0095", status["motivo"])        # campo que o painel já mostra
        self.assertIn((self.de, "pronto"), {(a[0], a[2]) for a in painel.avisos})
        # próximas rodadas: o mesmo item não é tentado de novo
        with mock.patch("nucleo.reidentificar.planejar") as plan:
            self.assertEqual(self.vigia(painel).uma_rodada(), 0)
            self.assertEqual(self.vigia(painel).uma_rodada(), 0)
            plan.assert_not_called()
        self.assertEqual(len([l for l in self.livro() if l["acao"] == "erro" and "UX-43" in l["detalhe"]]), 1)
        # o painel mudou o item (agora para o Jockey): tenta de novo e aplica
        painel.lista = [self.item(para="F026-P0092")]
        self.assertEqual(self.vigia(painel).uma_rodada(), 1)
        self.assertEqual(len(self.folhas("F026-P0092")), 3)

    def test_plano_parcial_nao_aplica(self):
        # duas folhas do Marambaia e uma do Jockey: levar só parte da pasta não é o que o painel pediu
        self.carimbos(MARAMBAIA, MARAMBAIA, {"projeto": "Jockey Club de São Paulo"})
        painel = PainelDestinos(BASE, [self.item()])
        with mock.patch("nucleo.reidentificar.aplicar") as apl:
            self.assertEqual(self.vigia(painel).uma_rodada(), 0)
            apl.assert_not_called()
        self.assertEqual(len(self.folhas(self.de)), 3)

    def test_destino_fora_da_lista_do_painel_ou_de_outro_fundo_nao_aplica(self):
        self.carimbos(MARAMBAIA, MARAMBAIA, MARAMBAIA)
        sem_p0095 = [p for p in BASE if p["codigo"] != "F026-P0095"]
        painel = PainelDestinos(sem_p0095, [self.item(), self.item(id=4, para="F002-P0095")])
        with mock.patch("nucleo.reidentificar.aplicar") as apl:
            self.assertEqual(self.vigia(painel).uma_rodada(), 0)
            apl.assert_not_called()
        detalhes = " | ".join(l["detalhe"] for l in self.livro() if l["acao"] == "erro")
        self.assertIn("não está na lista de projetos do painel", detalhes)
        self.assertIn("outro fundo", detalhes)

    def test_ocupado_nao_roda(self):
        self.carimbos(MARAMBAIA, MARAMBAIA, MARAMBAIA)
        painel = PainelDestinos(BASE, [self.item()])
        v = self.vigia(painel)
        v.estado.situacao = "processando"
        self.assertEqual(v.atender_destinos(), 0)
        self.assertEqual(painel.consultas, 0)
        self.assertEqual(len(self.folhas(self.de)), 3)

    def test_interruptor_desligado_nao_roda(self):
        self.carimbos(MARAMBAIA, MARAMBAIA, MARAMBAIA)
        painel = PainelDestinos(BASE, [self.item()])
        self.assertEqual(self.vigia(painel, destinos_do_painel=False).uma_rodada(), 0)
        self.assertEqual(painel.consultas, 0)
        self.assertEqual(len(self.folhas(self.de)), 3)

    def test_painel_antigo_sem_a_rota_ignora_em_silencio(self):
        painel = PainelDestinos(BASE, None)
        with self.assertNoLogs("cv2", level="WARNING"):
            self.assertEqual(self.vigia(painel).uma_rodada(), 0)
        self.assertEqual(painel.consultas, 1)
        self.assertFalse(any(l["acao"] == "erro" for l in self.livro()))

    def test_teto_por_rodada(self):
        self.carimbos(MARAMBAIA, MARAMBAIA, MARAMBAIA)
        lista = [self.item(id=i, para="F002-P0001") for i in range(1, 6)]   # todos recusados (outro fundo)
        painel = PainelDestinos(BASE, lista)
        self.vigia(painel, destinos_por_rodada=2).uma_rodada()
        erros = [l for l in self.livro() if l["acao"] == "erro" and "UX-43" in l["detalhe"]]
        self.assertEqual(len(erros), 2)

    def test_cliente_http_404_e_lista(self):
        from nucleo.painel import Painel

        p = Painel("http://painel", "tok")
        with mock.patch.object(p, "_chamar", return_value=(404, {"detail": "Not Found"})):
            self.assertIsNone(p.destinos())
        with mock.patch.object(p, "_chamar", return_value=(200, {"destinos": [self.item(), "lixo"]})) as c:
            self.assertEqual([d["id"] for d in p.destinos()], [3])
            c.assert_called_with("GET", "/api/campvision/destinos")


if __name__ == "__main__":
    unittest.main()
