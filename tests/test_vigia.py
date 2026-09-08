"""Testes do modo automático. Nenhum toca a rede."""

from __future__ import annotations

import json
import unittest
from datetime import date, datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory

from nucleo import atualizador, relatorio_diario, vigia
from nucleo.config import Config
from nucleo.eventos import Evento, ler, registrar
from tests.test_nucleo import ClienteFalso, prancha_falsa, resposta_padrao


def montar_projeto(raiz: Path, nome: str, status: str | None, n_imagens: int = 2) -> Path:
    pasta = raiz / nome
    (pasta / "JPG").mkdir(parents=True)
    for i in range(n_imagens):
        prancha_falsa(pasta / "JPG" / f"{nome}-{i:03d}.jpg", 900, 700)
    if status is not None:
        (pasta / "status.json").write_text(json.dumps({"status": status}), encoding="utf-8")
    return pasta


class TestVarredura(unittest.TestCase):
    def test_pega_so_o_que_esta_pronto(self):
        with TemporaryDirectory() as tmp:
            raiz = Path(tmp)
            montar_projeto(raiz, "OCG-Teatro-1968", "enviado_windows")
            montar_projeto(raiz, "SBU-Casa-1972", "campvision_concluido")
            montar_projeto(raiz, "PMR-Museu-1988", None)
            achados = vigia.varrer(raiz, Config(espera_estabilidade_segundos=0))
            # PMR não tinha status.json: o vigia cria um e ela entra na fila.
            self.assertEqual(
                sorted(p.nome for p in achados), ["OCG-Teatro-1968", "PMR-Museu-1988"]
            )

    def test_sem_exigir_status_pega_pasta_nao_catalogada(self):
        with TemporaryDirectory() as tmp:
            raiz = Path(tmp)
            montar_projeto(raiz, "PMR-Museu-1988", None)
            cfg = Config(espera_estabilidade_segundos=0, exigir_status_json=False)
            self.assertEqual(len(vigia.varrer(raiz, cfg)), 1)

    def test_usa_a_subpasta_jpg(self):
        with TemporaryDirectory() as tmp:
            raiz = Path(tmp)
            montar_projeto(raiz, "OCG-Teatro-1968", "enviado_windows")
            achado = vigia.varrer(raiz, Config(espera_estabilidade_segundos=0))[0]
            self.assertEqual(achado.pasta_imagens.name, "JPG")

    def test_pasta_sem_imagem_e_ignorada(self):
        with TemporaryDirectory() as tmp:
            raiz = Path(tmp)
            pasta = raiz / "vazio"
            pasta.mkdir()
            (pasta / "status.json").write_text('{"status": "enviado_windows"}')
            self.assertEqual(vigia.varrer(raiz, Config(espera_estabilidade_segundos=0)), [])

    def test_status_corrompido_nao_derruba_a_varredura(self):
        with TemporaryDirectory() as tmp:
            raiz = Path(tmp)
            pasta = montar_projeto(raiz, "OCG-Teatro-1968", "enviado_windows")
            (pasta / "status.json").write_text("{isso nao e json")
            self.assertEqual(vigia.varrer(raiz, Config(espera_estabilidade_segundos=0)), [])


class TestStatus(unittest.TestCase):
    def test_escrever_preserva_campos_das_outras_maquinas(self):
        with TemporaryDirectory() as tmp:
            caminho = Path(tmp) / "status.json"
            caminho.write_text(json.dumps({
                "status": "enviado_windows",
                "enviado_por": "windows-nextimage",
                "bytes": 12345,
            }), encoding="utf-8")
            vigia.escrever_status(caminho, "campvision_concluido", {"campvision2_pranchas": 7})
            dados = json.loads(caminho.read_text())
            self.assertEqual(dados["status"], "campvision_concluido")
            self.assertEqual(dados["enviado_por"], "windows-nextimage")
            self.assertEqual(dados["bytes"], 12345)
            self.assertEqual(dados["campvision2_pranchas"], 7)


class TestProcessamento(unittest.TestCase):
    def test_processa_e_avanca_o_semaforo(self):
        with TemporaryDirectory() as tmp:
            raiz = Path(tmp)
            montar_projeto(raiz, "OCG-Teatro-1968", "enviado_windows", 3)
            projeto = vigia.varrer(raiz, Config(espera_estabilidade_segundos=0))[0]
            evento = vigia.processar(projeto, Config(espera_estabilidade_segundos=0, trabalhadores=2), ClienteFalso([resposta_padrao()]))
            self.assertEqual(evento.pranchas, 3)
            self.assertEqual(evento.com_carimbo, 3)
            self.assertFalse(evento.falha)
            self.assertEqual(vigia.ler_status(projeto.caminho_status), "campvision_concluido")
            self.assertTrue((projeto.pasta / "catalogacao" / "catalogacao.csv").exists())

    def test_projeto_processado_sai_da_fila(self):
        with TemporaryDirectory() as tmp:
            raiz = Path(tmp)
            montar_projeto(raiz, "OCG-Teatro-1968", "enviado_windows", 2)
            cfg = Config(espera_estabilidade_segundos=0, trabalhadores=1)
            projeto = vigia.varrer(raiz, cfg)[0]
            vigia.processar(projeto, cfg, ClienteFalso([resposta_padrao()]))
            self.assertEqual(vigia.varrer(raiz, cfg), [], "não pode reprocessar")

    def test_status_criado_nao_reprocessa_no_ciclo_seguinte(self):
        with TemporaryDirectory() as tmp:
            raiz = Path(tmp)
            montar_projeto(raiz, "PMR-Museu-1988", None, 2)
            cfg = Config(espera_estabilidade_segundos=0, trabalhadores=1)
            projeto = vigia.varrer(raiz, cfg)[0]
            vigia.processar(projeto, cfg, ClienteFalso([resposta_padrao()]))
            self.assertEqual(vigia.varrer(raiz, cfg), [])

    def test_falha_de_um_projeto_nao_levanta_excecao(self):
        class ClienteQuebrado:
            def chamar(self, *_args, **_kwargs):
                raise RuntimeError("API fora do ar")

        with TemporaryDirectory() as tmp:
            raiz = Path(tmp)
            montar_projeto(raiz, "OCG-Teatro-1968", "enviado_windows", 2)
            projeto = vigia.varrer(raiz, Config(espera_estabilidade_segundos=0))[0]
            evento = vigia.processar(projeto, Config(espera_estabilidade_segundos=0, trabalhadores=1), ClienteQuebrado())
            # A leitura falha por prancha, o lote termina, mas nada é perdido.
            self.assertEqual(evento.erros, 2)
            self.assertEqual(vigia.ler_status(projeto.caminho_status), "campvision_concluido")

    def test_conta_campos_a_revisar(self):
        with TemporaryDirectory() as tmp:
            raiz = Path(tmp)
            montar_projeto(raiz, "OCG-Teatro-1968", "enviado_windows", 1)
            projeto = vigia.varrer(raiz, Config(espera_estabilidade_segundos=0))[0]
            fraca = resposta_padrao(folha={"valor": "03", "confianca": 0.2})
            cfg = Config(espera_estabilidade_segundos=0, trabalhadores=1, consolidar_por_projeto=False)
            evento = vigia.processar(projeto, cfg, ClienteFalso([fraca]))
            self.assertGreaterEqual(evento.campos_a_revisar, 1)


class TestLacoDoVigia(unittest.TestCase):
    def test_uma_rodada_processa_a_fila_toda(self):
        with TemporaryDirectory() as tmp:
            raiz = Path(tmp)
            estado = Path(tmp) / "estado"
            for nome in ("A-1968", "B-1972", "C-1980"):
                montar_projeto(raiz, nome, "enviado_windows", 2)
            cfg = Config(espera_estabilidade_segundos=0, trabalhadores=2, pasta_vigiada=str(raiz), auto_atualizar=False)
            v = vigia.Vigia(cfg, ClienteFalso([resposta_padrao()]), estado)
            self.assertEqual(v.uma_rodada(), 3)
            self.assertEqual(v.uma_rodada(), 0, "segunda passada não acha nada")
            eventos = ler(estado / "eventos.jsonl")
            self.assertEqual(len(eventos), 3)
            self.assertEqual(v.estado.pranchas_hoje, 6)


class TestEventos(unittest.TestCase):
    def test_filtra_por_dia(self):
        with TemporaryDirectory() as tmp:
            caminho = Path(tmp) / "eventos.jsonl"
            ontem = (datetime.now() - timedelta(days=1)).isoformat(timespec="seconds")
            registrar(caminho, Evento(projeto="hoje", pranchas=5))
            registrar(caminho, Evento(quando=ontem, projeto="ontem", pranchas=9))
            self.assertEqual(len(ler(caminho)), 2)
            de_hoje = ler(caminho, date.today())
            self.assertEqual([e.projeto for e in de_hoje], ["hoje"])

    def test_linha_corrompida_e_pulada(self):
        with TemporaryDirectory() as tmp:
            caminho = Path(tmp) / "eventos.jsonl"
            registrar(caminho, Evento(projeto="bom"))
            with caminho.open("a") as f:
                f.write("{quebrado\n")
            self.assertEqual(len(ler(caminho)), 1)


class TestRelatorioDiario(unittest.TestCase):
    def test_resume_o_dia(self):
        eventos = [
            Evento(projeto="A", pranchas=10, com_carimbo=9, campos_a_revisar=3, custo_usd=0.12),
            Evento(projeto="B", pranchas=5, com_carimbo=5, custo_usd=0.06),
        ]
        texto = relatorio_diario.montar_texto(eventos, date(2026, 9, 4))
        self.assertIn("Projetos processados: 2", texto)
        self.assertIn("Pranchas lidas:       15", texto)
        self.assertIn("US$ 0.18", texto)

    def test_destaca_projeto_que_falhou(self):
        eventos = [Evento(projeto="A", falha="pasta sumiu no meio do lote")]
        texto = relatorio_diario.montar_texto(eventos, date(2026, 9, 4))
        self.assertIn("FALHOU", texto)
        self.assertIn("não avançaram de status", texto)

    def test_dia_sem_nada(self):
        self.assertIn("Nenhum projeto", relatorio_diario.montar_texto([], date(2026, 9, 4)))

    def test_escreve_txt_e_html(self):
        with TemporaryDirectory() as tmp:
            destino = relatorio_diario.escrever(
                [Evento(projeto="A", pranchas=3)], Path(tmp), date(2026, 9, 4)
            )
            self.assertTrue(destino.exists())
            self.assertTrue((Path(tmp) / "relatorio-2026-09-04.html").exists())

    def test_email_desligado_nao_tenta_enviar(self):
        erro = relatorio_diario.enviar_email(Config(espera_estabilidade_segundos=0), "texto", date(2026, 9, 4))
        self.assertIn("desligado", erro)

    def test_email_incompleto_avisa_o_que_falta(self):
        cfg = Config(espera_estabilidade_segundos=0, email_ativo=True, email_para="a@b.c")
        erro = relatorio_diario.enviar_email(cfg, "texto", date(2026, 9, 4))
        self.assertIn("incompleta", erro)


class TestAtualizador(unittest.TestCase):
    def test_pasta_sem_git_nao_e_repositorio(self):
        with TemporaryDirectory() as tmp:
            self.assertFalse(atualizador.e_repositorio(Path(tmp)))
            atualizou, mensagem = atualizador.atualizar(Path(tmp))
            self.assertFalse(atualizou)
            self.assertIn("repositório", mensagem)


class TestProximaHora(unittest.TestCase):
    def test_hora_ja_passada_vai_para_amanha(self):
        agora = datetime(2026, 9, 4, 19, 30)
        alvo = vigia._proxima_hora("18:00", agora)
        self.assertEqual(alvo, datetime(2026, 9, 5, 18, 0))

    def test_hora_futura_e_hoje(self):
        agora = datetime(2026, 9, 4, 9, 0)
        self.assertEqual(vigia._proxima_hora("18:00", agora), datetime(2026, 9, 4, 18, 0))

    def test_hora_invalida_cai_para_18h(self):
        agora = datetime(2026, 9, 4, 9, 0)
        self.assertEqual(vigia._proxima_hora("abacaxi", agora), datetime(2026, 9, 4, 18, 0))


if __name__ == "__main__":
    unittest.main()
