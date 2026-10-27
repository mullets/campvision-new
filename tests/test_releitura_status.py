"""CAMP-RELEITURA: releitura com tudo já no acervo reescreve o status.json da pasta final.

Caso real (27/10): F009-P0092 (complemento de 19/10) parado em "processando" há 8 dias, F026-P0009 e F016-P0024
parados em "erro". "Ler de novo" no painel (10 vezes) não mudava nada: o CV2 relia, mas o status.json da pasta do
projeto continuava no estado velho (o painel só sai dele pelo status.json), e o aviso ia com a subpasta
catalogacao/releituras/..., que o painel ignora. O mesmo lote de novo na entrada, com tudo já no acervo, virava
"pronto" só no estado LOCAL do CV2.
Nenhuma chamada real à API (ClienteFalso).
"""

from __future__ import annotations

import json
import threading
import types
import unittest
from pathlib import Path
from unittest import mock

from nucleo import entrada, fundos, vigia
from tests.test_entrada import Base, conferir_falso, exif_falso
from tests.test_nucleo import ClienteFalso, resposta_padrao

MOTIVO_VELHO = "EXIF conferido em 6 de 48 arquivo(s): IPTC:By-line exceeds length limit (truncated)"


class _Projeto(Base):
    """Um lote que terminou 'pronto' e depois teve a pasta final presa num estado velho."""

    def setUp(self):
        super().setUp()
        self.config.apagar_original_apos_pronto = False   # o lote continua na entrada (reenvio/retomada)
        self.scan("F026", "Fotos", "Tarumã", n=2, formatos=("jpg",))
        [r] = self.rodar()
        self.assertEqual(r.status, entrada.PRONTO, r.motivo)
        self.projeto = r.pasta_projeto
        self.cat = self.projeto / "catalogacao"
        self.status_bom = self.ler_status()
        self.painel.avisos.clear()

    def ler_status(self) -> dict:
        return json.loads((self.projeto / "status.json").read_text(encoding="utf-8"))

    def prender(self, situacao: str, motivo: str = "", **extra) -> None:
        """O que o corte do lote deixa na pasta final: status velho + (no erro) o bloqueio em erros.json."""
        st = dict(self.status_bom, status=situacao, motivo=motivo, **extra)
        (self.projeto / "status.json").write_text(json.dumps(st), encoding="utf-8")
        if motivo:
            erros = json.loads((self.cat / "erros.json").read_text(encoding="utf-8"))
            erros.append({"arquivo": "", "categoria": "metadado", "gravidade": "bloqueia",
                          "origem": "CAMP Vision", "detalhe": motivo})
            (self.cat / "erros.json").write_text(json.dumps(erros), encoding="utf-8")

    def rodar_de_novo(self):
        """O mesmo lote volta pela entrada (retomada depois do corte)."""
        for chave in entrada.Estado(self.estado).ultimos():
            entrada.Estado(self.estado).anotar(chave, "refazer")
        [r] = self.rodar()
        return r

    def relativo(self) -> str:
        return str(self.projeto.relative_to(self.acervo))


class TestEntradaTudoNoAcervo(_Projeto):
    def test_processando_preso_vira_pronto_e_avisa_com_a_pasta_do_projeto(self):
        self.prender("processando")          # F009-P0092: lote cortado no meio do _concluir
        r = self.rodar_de_novo()
        self.assertEqual((r.status, r.copiados), (entrada.PRONTO, 0))
        st = self.ler_status()
        self.assertEqual(st["status"], "pronto", "status.json da pasta continuou no estado velho")
        self.assertEqual(st["motivo"], "")
        self.assertEqual((st["folhas"], st["imagens"]), (2, 2))
        self.assertIn(("F026-P0001", self.relativo(), "pronto"), self.painel.avisos)

    def test_erro_velho_vira_pronto_e_o_bloqueio_velho_sai_do_erros_json(self):
        self.prender("erro", MOTIVO_VELHO, exif_sem_conferir=[{"arquivo": "x.tif", "motivo": "m"}])
        self.rodar_de_novo()
        st = self.ler_status()
        self.assertEqual(st["status"], "pronto")
        self.assertNotIn("exif_sem_conferir", st)
        self.assertEqual(st["erros_bloqueantes"], 0)
        erros = json.loads((self.cat / "erros.json").read_text(encoding="utf-8"))
        self.assertFalse([e for e in erros if e.get("detalhe") == MOTIVO_VELHO])

    def test_sem_pacote_e_sem_leitura_nao_vira_pronto(self):
        # Nunca "pronto" sem pacote coerente: documento da pasta fora do pacote e sem leitura = erro com o motivo de agora.
        self.prender("processando")
        (self.cat / "pacote_tainacan.json").unlink()
        leituras = json.loads((self.cat / "leituras.json").read_text(encoding="utf-8"))
        (self.cat / "leituras.json").write_text(json.dumps(leituras[:1]), encoding="utf-8")
        self.rodar_de_novo()
        st = self.ler_status()
        self.assertEqual(st["status"], "erro")
        self.assertIn("sem leitura no pacote", st["motivo"])
        self.assertIn("Ler de novo", st["motivo"])
        self.assertFalse((self.cat / "pacote_tainacan.json").exists())

    def test_pacote_faltando_e_remontado_do_que_ja_foi_lido(self):
        self.prender("processando")
        (self.cat / "pacote_tainacan.json").unlink()
        self.rodar_de_novo()
        self.assertEqual(self.ler_status()["status"], "pronto")
        pacote = json.loads((self.cat / "pacote_tainacan.json").read_text(encoding="utf-8"))
        self.assertEqual(pacote["projeto_codigo"], "F026-P0001")
        self.assertEqual(len(pacote["documentos"]) + len(pacote["retirados"]), 2)

    def test_pasta_ja_pronta_nao_e_tocada(self):
        antes = (self.projeto / "status.json").read_text(encoding="utf-8")
        self.rodar_de_novo()
        self.assertEqual((self.projeto / "status.json").read_text(encoding="utf-8"), antes)
        self.assertEqual(self.painel.avisos, [])


class TestPedidoDeReleitura(_Projeto):
    def pedir(self, conferir=conferir_falso, gravar=exif_falso):
        concluidos = []
        self.painel.pedidos_releitura = lambda est: [{"id": 7, "escopo": "projeto", "projeto_codigo": "F026-P0001",
                                                      "item_codigo": None, "motivo": "Ler de novo"}]
        self.painel.releitura_iniciada = lambda *a: True
        self.painel.releitura_concluida = lambda pid, resumo, ok=True, mensagem="": concluidos.append((pid, ok, mensagem))
        v = vigia.Vigia(self.config, ClienteFalso([resposta_padrao()]), self.estado, painel=self.painel)
        with mock.patch.object(entrada.mod_metadados, "gravar_em_lote", side_effect=gravar), \
                mock.patch.object(entrada, "conferir_exif", side_effect=conferir), \
                mock.patch.object(entrada.mod_fundos, "carregar", return_value=fundos.Tabela(fundos.EMBUTIDA)):
            self.assertEqual(v.atender_releituras(), 1)
        return concluidos

    def test_ler_de_novo_tira_a_pasta_do_erro_velho(self):
        self.prender("erro", MOTIVO_VELHO)       # F026-P0009 / F016-P0024
        [(pid, ok, mensagem)] = self.pedir()
        self.assertTrue(ok)
        st = self.ler_status()
        self.assertEqual(st["status"], "pronto", "a releitura não reescreveu o status.json da pasta")
        self.assertIn("erro -> pronto", mensagem)
        avisos = [a for a in self.painel.avisos if a[0] == "F026-P0001"]
        self.assertTrue(avisos)
        self.assertTrue(all(a[1] == self.relativo() for a in avisos),
                        f"aviso tem de ir com a pasta do projeto, não com a da releitura: {avisos}")
        self.assertEqual(avisos[-1][2], "pronto")

    def test_ler_de_novo_com_processando_preso(self):
        self.prender("processando")              # F009-P0092
        self.pedir()
        self.assertEqual(self.ler_status()["status"], "pronto")

    def test_exif_pela_metade_e_regravado_antes_do_pronto(self):
        self.prender("erro", MOTIVO_VELHO)
        alvos = sorted(str(p.relative_to(self.acervo)) for p in self.projeto.rglob("*.jpg")
                       if "catalogacao" not in p.parts)
        entrada.marcar_exif_pendente(self.cat, "L-velho", [self.acervo / a for a in alvos], self.acervo)
        regravados = []
        certos: set = set()

        def conferir(itens):
            return {p for p, _ in itens if p in certos}

        def gravar(itens, identidade):
            regravados.extend(p for p, _ in itens)
            certos.update(p for p, _ in itens)
            return len(itens), []

        self.pedir(conferir, gravar)
        self.assertEqual(len(regravados), 2)
        self.assertEqual(self.ler_status()["status"], "pronto")
        self.assertFalse((self.cat / entrada.EXIF_PENDENTE).exists())

    def test_exif_que_nao_grava_fica_em_erro_com_o_motivo_de_agora(self):
        self.prender("erro", MOTIVO_VELHO)
        alvos = [p for p in self.projeto.rglob("*.jpg") if "catalogacao" not in p.parts]
        entrada.marcar_exif_pendente(self.cat, "L-velho", alvos, self.acervo)
        [(_pid, _ok, mensagem)] = self.pedir(lambda itens: set(), lambda itens, ident: (0, ["exiftool ausente"]))
        st = self.ler_status()
        self.assertEqual(st["status"], "erro")
        self.assertTrue(st["motivo"].startswith("EXIF conferido em 0 de 2 arquivo(s) já no acervo"), st["motivo"])
        self.assertEqual(len(st["exif_sem_conferir"]), 2)
        self.assertIn("situação da pasta: erro -> erro", mensagem)

    def test_pasta_pronta_recebe_aviso_com_a_pasta_do_projeto(self):
        self.pedir()
        self.assertEqual(self.ler_status()["status"], "pronto")
        self.assertEqual(self.painel.avisos[-1], ("F026-P0001", self.relativo(), "pronto"))


class TestPedidoSemPainelCompleto(unittest.TestCase):
    def test_falha_ao_acertar_nao_impede_responder_o_pedido(self):
        respostas = []
        painel = types.SimpleNamespace(
            ligado=True, pedidos_releitura=lambda est: [{"id": 3, "escopo": "projeto", "projeto_codigo": "F026-P0001"}],
            releitura_iniciada=lambda *a: True, aviso=lambda *a: True,
            releitura_concluida=lambda pid, resumo, ok=True, mensagem="": respostas.append(ok))
        eu = types.SimpleNamespace(
            painel=painel, config=types.SimpleNamespace(raiz_final="/nao-existe", estacao_id="cv"),
            cancelar=threading.Event(), estado=vigia.EstadoVigia(), pasta_estado=Path("/y"), livro=None, cliente=None)
        eu._acertar_status_da_releitura = lambda codigo: vigia.Vigia._acertar_status_da_releitura(eu, codigo)
        resumo = {"relidos": 1, "falhas": [], "regravados": 1, "com_mudanca": 0, "campos_preenchidos": 0,
                  "custo_usd": 0.0, "pasta": ""}
        from nucleo import releitura
        with mock.patch.object(releitura, "reler", return_value=resumo):
            self.assertEqual(vigia.Vigia.atender_releituras(eu), 1)
        self.assertEqual(respostas, [True])


if __name__ == "__main__":
    unittest.main()
