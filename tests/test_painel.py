"""Cliente HTTP do painel contra um servidor local de mentira (127.0.0.1)."""

from __future__ import annotations

import json
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from tempfile import TemporaryDirectory

from nucleo import fundos
from nucleo.painel import Painel


class _Servidor:
    def __init__(self):
        self.pedidos: list[tuple] = []
        self.aceitar_aviso = True
        self.reserva = None          # None = 201 com um código novo; ou (status, corpo) para simular a pergunta do painel
        dono = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _r(self, codigo, obj):
                corpo = json.dumps(obj).encode()
                self.send_response(codigo)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(corpo)

            def do_GET(self):
                dono.pedidos.append(("GET", self.path, self.headers.get("X-Camp-Token")))
                self._r(200, {"fundos": [{"codigo": "F026", "sigla": "SBU", "nome": "Sami Bussab"}]})

            def do_POST(self):
                corpo = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                dono.pedidos.append(("POST", self.path, corpo))
                if self.path.endswith("/reservar"):
                    if dono.reserva:
                        self._r(*dono.reserva)
                    else:
                        self._r(201, {"codigo": "F026-P0042"})
                elif self.path.endswith("/aviso") and not dono.aceitar_aviso:
                    self._r(503, {})
                else:
                    self._r(202, {"ok": True})

        self.http = HTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=self.http.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.http.server_port}"


class TestPainel(unittest.TestCase):
    def setUp(self):
        self.srv = _Servidor()
        self.tmp = TemporaryDirectory()
        self.p = Painel(self.srv.url, "tok", Path(self.tmp.name))

    def tearDown(self):
        self.srv.http.shutdown()
        self.tmp.cleanup()

    def test_tabela_de_fundos_vem_do_painel_e_fica_em_cache(self):
        tabela = fundos.carregar(Path(self.tmp.name), self.p)
        self.assertEqual((tabela.origem, len(tabela)), ("painel", 1))
        self.assertEqual(self.srv.pedidos[0][2], "tok")
        fora = Painel("http://127.0.0.1:1", "", Path(self.tmp.name))
        self.assertEqual(fundos.carregar(Path(self.tmp.name), fora).origem, "cache")

    def test_reservar_heartbeat(self):
        self.assertEqual(self.p.reservar("F026", "Tarumã", "a" * 32, ano="1972"), "F026-P0042")
        corpo = self.srv.pedidos[-1][2]
        self.assertEqual((corpo["fundo_codigo"], corpo["chave_reserva"], corpo["ano"]),
                         ("F026", "a" * 32, "1972"))
        self.assertTrue(self.p.heartbeat({"estacao_id": "campvision2"}))

    def test_painel_pergunta_se_e_o_mesmo_projeto_o_cv2_espera_e_tenta_de_novo_com_a_mesma_chave(self):
        self.srv.reserva = (202, {"pendente": True, "decisao_id": 7, "mensagem": "Aguardando decisão no painel"})
        self.assertIsNone(self.p.reservar("F026", "Tarumã - Residência", "c" * 32))
        self.assertEqual(self.p.pendente_decisao, 7)
        self.assertIn("aguardando decisão", self.p.ultimo_erro)
        self.assertFalse(self.p.reserva_existente)
        self.assertIsNone(self.p.reservar("F026", "Tarumã - Residência", "c" * 32))          # ainda esperando
        self.assertEqual([p[2]["chave_reserva"] for p in self.srv.pedidos if p[0] == "POST"], ["c" * 32, "c" * 32])   # a MESMA chave
        # a pessoa decidiu "é o mesmo": o painel devolve o código de um projeto que JÁ existe
        self.srv.reserva = (200, {"codigo": "F026-P0001", "existente": True, "decisao_id": 7})
        self.assertEqual(self.p.reservar("F026", "Tarumã - Residência", "c" * 32), "F026-P0001")
        self.assertIsNone(self.p.pendente_decisao)
        self.assertTrue(self.p.reserva_existente)

    def test_reserva_comum_limpa_o_estado_da_decisao(self):
        self.srv.reserva = (202, {"pendente": True, "decisao_id": 3})
        self.p.reservar("F026", "x", "d" * 32)
        self.srv.reserva = None
        self.assertEqual(self.p.reservar("F026", "y", "e" * 32), "F026-P0042")
        self.assertEqual((self.p.pendente_decisao, self.p.reserva_existente), (None, False))

    def test_202_sem_pendente_continua_sendo_so_uma_resposta_sem_codigo(self):
        self.srv.reserva = (202, {"ok": True})
        self.assertIsNone(self.p.reservar("F026", "x", "f" * 32))
        self.assertIsNone(self.p.pendente_decisao)

    def test_aviso_perdido_e_reenviado(self):
        self.srv.aceitar_aviso = False
        self.assertFalse(self.p.aviso("F026-P0042", "x", "processando"))
        self.assertFalse(self.p.aviso("F026-P0042", "x", "pronto"))
        self.assertEqual(self.p.pendentes(), 1)  # só o mais recente do projeto
        self.srv.aceitar_aviso = True
        self.assertEqual(self.p.reenviar(), 1)
        self.assertEqual(self.p.pendentes(), 0)
        self.assertEqual(self.srv.pedidos[-1][2]["status"], "pronto")

    def test_painel_fora_nao_levanta(self):
        fora = Painel("http://127.0.0.1:1", "", None, timeout=1)
        self.assertFalse(fora.heartbeat({}))
        self.assertIsNone(fora.reservar("F026", "x", "b" * 32))
        self.assertTrue(fora.ultimo_erro)


if __name__ == "__main__":
    unittest.main()
