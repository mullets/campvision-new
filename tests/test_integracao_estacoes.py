"""Integração com as estações (contrato C1): JSONs gravados COMO as estações gravam.

As estruturas abaixo copiam o que sistema_windows.py (`_escrever_info_local`,
`_escrever_status_enviado`) e nucleo.py do Mac (`escrever_info_lote`) escrevem.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import unittest
from datetime import datetime
from pathlib import Path
from unittest import mock

from nucleo import entrada, fundos, vigia
from nucleo.livro import Livro
from tests.test_entrada import Base, conferir_falso, exif_falso
from tests.test_nucleo import ClienteFalso, resposta_padrao


def windows_info(**mudar) -> dict:
    """sistema_windows.py:2006-2030 (info_projeto.json da estação Windows)."""
    info = {
        "codigo_fundo": "F026", "fundo": "SBU Sami Bussab", "prefixo": "SBU",
        "numero_projeto": "P0001", "projeto": "Edifício Tarumã", "cidade": "São Paulo",
        "ano": "1972", "identificacao_original": "P0001 - Edifício Tarumã - 1972",
        "operador": "Beatriz", "operadores": ["Beatriz"], "estacao_id": "contex1",
        "serie": "01 - Desenhos e pranchas", "quantidade_pranchas": 2,
        "iniciado_em": "2026-10-14T09:00:00", "finalizado_em": "2026-10-14T11:30:00",
    }
    info.update(mudar)
    return info


def windows_status_antigo() -> dict:
    """sistema_windows.py:2202-2211: só `etapa`, sem `status`."""
    return {"etapa": "enviado_windows", "atualizado_em": datetime.now().isoformat(),
            "atualizado_por": "windows"}


def mac_info_lote(**mudar) -> dict:
    """mac nucleo.py:650-681 (info_lote_*_mac.json)."""
    info = {
        "estacao": "mac-vuescan", "codigo_fundo": "F026", "fundo": "SBU Sami Bussab",
        "prefixo": "SBU", "numero_projeto": "P0060", "projeto": "Edifício Tarumã",
        "cidade": "", "ano": "1972", "identificacao_original": "P0060 - Edifício Tarumã - 1972",
        "operador": "Rafa", "operadores": ["Rafa"], "estacao_id": "vuescan1",
        "estacao_tipo": "vuescan", "series": ["03 - Fotografias"], "quantidade_documentos": 2,
        "projeto_ja_existia": False, "iniciado_em": "2026-10-14T09:00:00",
        "finalizado_em": "2026-10-14T16:14:53",
    }
    info.update(mudar)
    return info


def escrever(pasta: Path, nome: str, dados) -> None:
    (pasta / nome).write_text(json.dumps(dados, indent=2, ensure_ascii=False), encoding="utf-8")


def tabela():
    return fundos.Tabela(fundos.EMBUTIDA)


class TestWindowsSoEtapa(Base):
    def windows(self) -> Path:
        pasta = self.scan("F026 - SBU Sami Bussab", "SBU-EdificioTaruma-1972", n=2)
        escrever(pasta, "info_projeto.json", windows_info())
        escrever(pasta, "status.json", windows_status_antigo())
        return pasta

    def test_etapa_sozinha_marca_o_fim_do_lote(self):
        pasta = self.windows()
        self.config.espera_estabilidade_segundos = 3600
        self.config.entrada_quieto_minutos = 0
        unidade = entrada.Unidade(pasta, pasta.relative_to(self.scanners))
        # arquivos recém-criados: só a marca (esperar 1 h) prova que `etapa` foi reconhecida
        ok, motivo = entrada.pronta(unidade, self.config)
        self.assertFalse(ok)
        self.assertEqual(motivo, "arquivos ainda chegando")
        # sem marca, a espera é a do "quieto" (0 min) e passa:
        (pasta / "status.json").unlink()
        self.assertTrue(entrada.pronta(unidade, self.config)[0])

    def test_ler_status_aceita_status_ou_etapa(self):
        pasta = self.windows()
        self.assertEqual(vigia.ler_status(pasta / "status.json"), "enviado_windows")
        escrever(pasta, "status.json", {"status": "enviado_windows", "etapa": "enviado_windows"})
        self.assertEqual(vigia.ler_status(pasta / "status.json"), "enviado_windows")

    def test_contexto_nao_confunde_numero_com_nome_nem_fundo_com_escritorio(self):
        pasta = self.windows()
        c = entrada.contexto(entrada.Unidade(pasta, pasta.relative_to(self.scanners)), tabela())
        self.assertEqual((c.fundo.codigo, c.projeto, c.ano), ("F026", "Edifício Tarumã", "1972"))
        self.assertEqual((c.estacao, c.tipo_estacao, c.operador), ("contex1", "", "Beatriz"))
        self.assertEqual(c.enviado_em, "2026-10-14T11:30:00")
        self.assertEqual((c.codigo, c.p_estacao), ("", "P0001"))  # P da estação é pista

    def test_info_traduzido(self):
        from nucleo import info_projeto
        self.assertEqual(info_projeto.mapear("numero_projeto"), "numero_projeto")
        self.assertEqual(info_projeto.mapear("codigo_fundo"), "fundo_codigo")
        pasta = self.windows()
        t = info_projeto.ler(pasta)
        self.assertEqual(t["projeto"], "Edifício Tarumã")
        self.assertNotIn("escritorio", {k for k in t if k == "escritorio" and t[k] == "F026"})
        self.assertEqual((t["estacao"], t["enviado_em"]), ("contex1", "2026-10-14T11:30:00"))

    def test_codigo_completo_do_info_e_lido(self):
        pasta = self.windows()
        escrever(pasta, "info_projeto.json", windows_info(codigo="F026-P0007", fundo_codigo="F026",
                                                          estacao="Contex 1", tipo_estacao="contex"))
        c = entrada.contexto(entrada.Unidade(pasta, pasta.relative_to(self.scanners)), tabela())
        self.assertEqual((c.codigo, c.estacao, c.tipo_estacao), ("F026-P0007", "Contex 1", "contex"))

    def test_lote_windows_so_com_etapa_processa_e_limpa_a_pasta(self):
        pasta = self.windows()
        [r] = self.rodar()
        self.assertEqual(r.status, entrada.PRONTO, r.motivo)
        self.assertFalse(pasta.exists())


class TestMac(Base):
    def mac(self) -> Path:
        projeto = self.scanners / "F026 - SBU Sami Bussab" / "P0060 - Edifício Tarumã - 1972"
        self.scan(*projeto.relative_to(self.scanners).parts, "03 - Fotografias",
                  "01 - Arquivo Arquivístico (TIFF)", n=2, formatos=("jpg",))
        escrever(projeto, "info_lote_20261014-161453_mac.json", mac_info_lote())
        escrever(projeto, "info_projeto.json", {k: mac_info_lote()[k] for k in (
            "codigo_fundo", "fundo", "prefixo", "numero_projeto", "projeto", "cidade", "ano",
            "identificacao_original", "iniciado_em")})
        escrever(projeto, "status_mac.json", {"status": "enviado_mac", "etapa": "enviado_mac"})
        return projeto

    def test_info_lote_e_marcador_e_vai_para_dados(self):
        projeto = self.mac()
        unidade = entrada.Unidade(projeto, projeto.relative_to(self.scanners))
        self.assertTrue(all(a.suffix == ".jpg" for a in unidade.arquivos()))
        c = entrada.contexto(unidade, tabela())
        self.assertEqual((c.operador, c.estacao, c.tipo_estacao), ("Rafa", "vuescan1", "vuescan"))
        self.assertEqual(c.serie, "S03")

    def test_mac_so_etapa(self):
        projeto = self.mac()
        escrever(projeto, "status_mac.json", {"etapa": "enviado_mac"})
        self.config.espera_estabilidade_segundos = 3600
        self.assertEqual(entrada.pronta(entrada.Unidade(projeto, projeto.relative_to(self.scanners)),
                                        self.config), (False, "arquivos ainda chegando"))
        self.assertTrue(entrada._marca_de_fim(projeto))

    def test_lote_mac_sem_aviso_de_formato_e_pasta_limpa(self):
        projeto = self.mac()
        [r] = self.rodar()
        self.assertEqual(r.status, entrada.PRONTO, r.motivo)
        self.assertEqual(r.lote["nao_suportados"], [])
        self.assertFalse(any("formato não suportado" in e["detalhe"] for e in r.lote["erros"]))
        self.assertFalse(projeto.exists())
        self.assertEqual(r.lote["contexto"]["operador"], "Rafa")


class TestLoteMistoDoMac(Base):
    def lote(self) -> Path:
        projeto = self.scanners / "F026 - SBU Sami Bussab" / "P0060 - Edifício Tarumã - 1972"
        for material in ("03 - Fotografias", "04 - Negativos", "05 - Slides"):
            self.scan(*projeto.relative_to(self.scanners).parts, material,
                      "01 - Arquivo Arquivístico (TIFF)", n=2, formatos=("jpg",))
        escrever(projeto, "info_lote_20261014-161453_mac.json", mac_info_lote(
            series=["03 - Fotografias", "04 - Negativos", "05 - Slides"], quantidade_documentos=6,
            projeto_ja_existia=True))
        escrever(projeto, "status_mac.json", {"status": "enviado_mac", "etapa": "enviado_mac"})
        return projeto

    def test_contexto_reconhece_varias_series(self):
        projeto = self.lote()
        c = entrada.contexto(entrada.Unidade(projeto, projeto.relative_to(self.scanners)), tabela())
        self.assertEqual((c.series, c.serie_deduzida), (["S03", "S04", "S05"], False))
        self.assertEqual(c.codigo, "F026-P0060")

    def test_serie_por_arquivo_e_d_continuo(self):
        projeto = self.lote()
        [r] = self.rodar()
        self.assertEqual(r.status, entrada.PRONTO, r.motivo)
        nomes = sorted(p.name for p in r.pasta_projeto.rglob("F026-P0060-*") if p.suffix == ".jpg")
        self.assertEqual(len(nomes), 6, nomes)
        # 2 negativos, 2 slides e 2 fotos — cada um na série da sua pasta
        por_serie = {}
        for nome in nomes:
            por_serie.setdefault(nome.split("-")[3], []).append(int(nome.split("-")[4][1:6]))
        self.assertEqual({k: len(v) for k, v in por_serie.items()}, {"S03": 2, "S04": 2, "S05": 2})
        # D contínuo no projeto: 1..6, sem repetir entre séries
        self.assertEqual(sorted(d for v in por_serie.values() for d in v), [1, 2, 3, 4, 5, 6])
        for serie, pasta in (("S03", "03 - Fotografias"), ("S04", "04 - Negativos"), ("S05", "05 - Slides")):
            achados = list((r.pasta_projeto / pasta).rglob("*.jpg"))
            self.assertEqual(len(achados), 2, pasta)
            self.assertTrue(all(f"-{serie}-" in a.name for a in achados))
        self.assertFalse(projeto.exists())


class TestExifRetomavel(Base):
    """CAMP-010: lote cortado no meio do EXIF volta sem API e só regrava o que faltou."""

    MARCA = b"\nXMP-CV2-OK"

    def fake_exif(self, falhar_no_segundo=False):
        gravados = []

        def gravar(itens, identidade):
            for i, (d, _c) in enumerate(itens):
                if falhar_no_segundo and i == 1:
                    raise OSError("QNAP caiu no meio do EXIF")
                with open(d, "ab") as f:  # muda o hash, como o exiftool muda
                    f.write(self.MARCA)
                gravados.append(d.name)
            return len(itens), []

        def conferir(itens):
            return {p for p, _ in itens if p.read_bytes().endswith(self.MARCA)}
        return gravar, conferir, gravados

    def processar(self, r, gravar, conferir):
        with mock.patch.object(entrada.mod_metadados, "gravar_em_lote", side_effect=gravar), \
                mock.patch.object(entrada, "conferir_exif", side_effect=conferir):
            return [r.processar(u) for u in r.pendentes()]

    def test_corte_no_exif_retoma_sem_api_e_regrava_so_o_que_faltou(self):
        origem = self.scan("F026 - SBU Sami Bussab", "01 - Desenhos e pranchas", "Casa Nova 1980",
                           n=3, formatos=("jpg",))
        r = self.recebedor()
        gravar, conferir, gravados = self.fake_exif(falhar_no_segundo=True)
        with self.assertRaises(OSError):
            self.processar(r, gravar, conferir)
        self.assertEqual(len(gravados), 1)  # só o primeiro ficou com EXIF
        chamadas_api = len(r.cliente.chamadas)
        self.assertTrue(origem.exists())  # nada apagado da entrada
        projeto = next((self.acervo / "F026 - SBU Sami Bussab" / "01 - Projetos").iterdir())
        self.assertTrue((projeto / "catalogacao" / "exif_pendente.json").exists())
        self.assertEqual(entrada.Estado(self.estado).ultimos()[
            next(iter(entrada.Estado(self.estado).ultimos()))]["status"], entrada.PASSO_EXIF)
        with mock.patch.object(entrada, "conferir_exif", side_effect=conferir):
            self.assertEqual(len(entrada.exif_incompleto(projeto, self.acervo)), 2)

        # --diagnostico lista o projeto com EXIF incompleto
        import contextlib
        import io
        import vigia as comando
        sem_entrada = dataclasses.replace(self.config, pasta_entrada="")
        saida = io.StringIO()
        with mock.patch.object(entrada, "conferir_exif", side_effect=conferir), contextlib.redirect_stdout(saida):
            comando._comando_diagnostico(sem_entrada, "")
        self.assertIn("EXIF INCOMPLETO em 1 projeto", saida.getvalue())
        self.assertIn("F026-P0001: 2 arquivo(s)", saida.getvalue())

        gravar2, conferir2, gravados2 = self.fake_exif()
        r2 = self.recebedor()
        r2.cliente = r.cliente
        [res] = self.processar(r2, gravar2, conferir2)
        self.assertEqual(res.status, entrada.PRONTO, res.motivo)
        self.assertEqual(len(r.cliente.chamadas), chamadas_api)  # sem gastar API
        self.assertEqual(len(gravados2), 2)  # só os 2 que faltavam
        self.assertEqual(sorted(gravados2), sorted(set(gravados2)))
        self.assertFalse(set(gravados2) & set(gravados))
        self.assertFalse((projeto / "catalogacao" / "exif_pendente.json").exists())
        with mock.patch.object(entrada, "conferir_exif", side_effect=conferir2):
            self.assertEqual(entrada.exif_incompleto(projeto, self.acervo), [])
        self.assertFalse(origem.exists())
        todos = sorted(p for p in projeto.rglob("*.jpg"))
        self.assertEqual(len(todos), 3)  # nada duplicado
        self.assertTrue(all(p.read_bytes().endswith(self.MARCA) for p in todos))

    def test_arquivos_ja_no_acervo_sem_exif_sao_regravados(self):
        self.scan("F026 - SBU Sami Bussab", "01 - Desenhos e pranchas", "Casa Nova 1980", n=2, formatos=("jpg",))
        # 1ª passada: EXIF "não gravado" (exiftool ausente) -> erro, mas arquivos ficam no acervo
        gravar0 = lambda itens, ident: (0, ["sem exiftool"])  # noqa: E731
        self.config.entrada_exigir_exif = True
        [r0] = self.processar(self.recebedor(), gravar0, lambda itens: set())
        self.assertEqual(r0.status, entrada.ERRO)
        for chave in entrada.Estado(self.estado).ultimos():
            entrada.Estado(self.estado).anotar(chave, "refazer")  # como --refazer-lote
        gravar, conferir, gravados = self.fake_exif()
        [res] = self.processar(self.recebedor(), gravar, conferir)
        self.assertEqual(res.status, entrada.PRONTO, res.motivo)
        self.assertEqual(len(gravados), 2)


class TestLimiteDeCusto(Base):
    def lote(self, n=4):
        return self.scan("F026 - SBU Sami Bussab", "01 - Desenhos e pranchas", "Casa Nova 1980",
                         n=n, formatos=("jpg",))

    def test_estourou_o_teto_do_lote_vira_erro_e_para_de_gastar(self):
        origem = self.lote(n=6)
        self.config.limite_custo_lote_usd = 0.0001  # a 1ª chamada (1000 tokens) já passa
        r = self.recebedor()
        with mock.patch.object(entrada.mod_metadados, "gravar_em_lote", side_effect=exif_falso), \
                mock.patch.object(entrada, "conferir_exif", side_effect=conferir_falso):
            [res] = [r.processar(u) for u in r.pendentes()]
        self.assertEqual(res.status, entrada.ERRO)
        self.assertTrue(res.motivo.startswith("limite de custo"), res.motivo)
        self.assertLess(len(r.cliente.chamadas), 6)
        self.assertTrue(origem.exists())  # nada apagado

    def test_teto_do_dia_ja_gasto_nem_comeca(self):
        self.lote(n=2)
        self.config.limite_custo_dia_usd = 1.0
        v = vigia.Vigia(self.config, ClienteFalso([resposta_padrao()]), self.estado, painel=self.painel)
        v.estado.custo_hoje = 1.5
        with mock.patch.object(entrada.mod_metadados, "gravar_em_lote", side_effect=exif_falso), \
                mock.patch.object(entrada, "conferir_exif", side_effect=conferir_falso), \
                mock.patch.object(entrada.mod_fundos, "carregar", return_value=fundos.Tabela(fundos.EMBUTIDA)):
            v.uma_rodada()
        self.assertEqual(len(v.cliente.chamadas), 0)
        self.assertIn("limite de custo", v.estado.alerta)
        # o heartbeat avisa: estado erro + alerta no detalhe
        hb = self.painel.heartbeats[-1]
        self.assertEqual(hb["estado"], "erro")
        self.assertIn("limite de custo", hb["detalhe"]["alerta"])

    def test_sem_limite_nada_muda(self):
        self.lote(n=2)
        [r] = self.rodar()
        self.assertEqual(r.status, entrada.PRONTO, r.motivo)


class TestInstanciaEAtomico(unittest.TestCase):
    def setUp(self):
        from tempfile import TemporaryDirectory
        self.tmp = TemporaryDirectory()
        self.pasta = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_so_um_servico_por_maquina(self):
        from nucleo import instancia
        primeiro = instancia.travar_servico(self.pasta)
        self.assertIsNotNone(primeiro)
        self.assertIsNone(instancia.travar_servico(self.pasta))  # a segunda instância recusa
        primeiro.close()
        outra = instancia.travar_servico(self.pasta)  # solto o lock, outra pode subir
        self.assertIsNotNone(outra)
        outra.close()

    def test_conserto_recusa_enquanto_o_servico_processa(self):
        from nucleo import instancia
        estado = self.pasta / "estado.json"
        estado.write_text(json.dumps({"situacao": "copiando 3 de 9"}), encoding="utf-8")
        trava, recusa = instancia.travar_conserto(self.pasta)
        self.assertIsNone(trava)
        self.assertIn("processando um lote", recusa)
        estado.write_text(json.dumps({"situacao": "vigiando"}), encoding="utf-8")
        trava, recusa = instancia.travar_conserto(self.pasta)
        self.assertEqual(recusa, "")
        self.assertIsNotNone(trava)
        # enquanto o conserto roda, o serviço não sobe por cima
        self.assertIsNone(instancia.travar_servico(self.pasta))
        trava.close()

    def test_conserto_com_estado_velho_nao_recusa(self):
        import os
        from nucleo import instancia
        estado = self.pasta / "estado.json"
        estado.write_text(json.dumps({"situacao": "processando"}), encoding="utf-8")
        os.utime(estado, (0, 0))
        trava, recusa = instancia.travar_conserto(self.pasta)
        self.assertEqual(recusa, "")
        trava.close()

    def test_escritas_do_vigia_sao_atomicas(self):
        status = self.pasta / "status.json"
        status.write_text(json.dumps({"status": "enviado_windows"}), encoding="utf-8")
        with mock.patch("os.replace", side_effect=OSError("queda")), self.assertRaises(OSError):
            vigia.escrever_status(status, "processando")
        self.assertEqual(json.loads(status.read_text()), {"status": "enviado_windows"})  # intacto
        self.assertEqual(list(self.pasta.glob(".status.json*")), [])  # sem lixo
        vigia.escrever_status(status, "processando", {"x": 1})
        self.assertEqual(json.loads(status.read_text())["status"], "processando")

    def test_transferir_so_apaga_com_hash_igual(self):
        from nucleo import transferir
        a, b = self.pasta / "a.bin", self.pasta / "b.bin"
        a.write_bytes(b"x" * 100)
        b.write_bytes(b"y" * 100)  # mesmo tamanho, outro conteúdo
        self.assertNotEqual(transferir._sha256(a), transferir._sha256(b))
        b.write_bytes(b"x" * 100)
        self.assertEqual(transferir._sha256(a), transferir._sha256(b))
        self.assertTrue(transferir._mesmo_destino({"destino": "b.bin"}, self.pasta, b))
        self.assertFalse(transferir._mesmo_destino({"destino": "a.bin"}, self.pasta, b))
        self.assertFalse(transferir._mesmo_destino(None, self.pasta, b))


class TestManifesto(Base):
    def lote(self, adulterar=False, contagem=None) -> Path:
        pasta = self.scan("F026 - SBU Sami Bussab", "01 - Desenhos e pranchas", "Casa Nova 1980", n=2)
        arquivos = []
        for f in sorted(p for p in pasta.rglob("*") if p.is_file()):
            arquivos.append({"caminho": f.relative_to(pasta).as_posix(), "tamanho": f.stat().st_size,
                             "sha256": hashlib.sha256(f.read_bytes()).hexdigest()})
        escrever(pasta, "manifesto.json", {
            "versao": 1, "estacao_id": "contex1", "tipo_estacao": "contex", "operador": "Beatriz",
            "fundo_codigo": "F026", "serie": "S01", "contagens": {"arquivos": contagem or len(arquivos)},
            "arquivos": arquivos, "gerado_em": datetime.now().isoformat()})
        if adulterar:
            alvo = pasta / arquivos[0]["caminho"]
            dados = bytearray(alvo.read_bytes())
            dados[-5] ^= 0xFF  # mesmo tamanho, conteúdo diferente
            alvo.write_bytes(bytes(dados))
        return pasta

    def test_manifesto_integro_processa(self):
        pasta = self.lote()
        c = entrada.contexto(entrada.Unidade(pasta, pasta.relative_to(self.scanners)), tabela())
        self.assertEqual((c.estacao, c.tipo_estacao, c.serie), ("contex1", "contex", "S01"))
        [r] = self.rodar()
        self.assertEqual(r.status, entrada.PRONTO, r.motivo)

    def test_arquivo_corrompido_vira_erro_sem_apagar(self):
        pasta = self.lote(adulterar=True)
        antes = sorted(p for p in pasta.rglob("*") if p.is_file())
        [r] = self.rodar()
        self.assertEqual(r.status, entrada.ERRO)
        self.assertIn("corrompido", r.motivo)
        self.assertEqual(sorted(p for p in pasta.rglob("*") if p.is_file()), antes)
        self.assertEqual(self.painel.reservas, {})  # nem chegou a reservar projeto

    def test_contagem_divergente_nao_processa(self):
        pasta = self.lote(contagem=9)
        unidade = entrada.Unidade(pasta, pasta.relative_to(self.scanners))
        self.assertEqual(entrada.pronta(unidade, self.config), (False, "contagem 4 de 9"))
        self.assertTrue(entrada.conferir_manifesto(unidade))


class TestHeartbeat(Base):
    def test_situacao_vira_vocabulario_do_painel(self):
        casos = {"copiando 7 de 38": "processando", "preparando 3 de 9": "processando",
                 "gravando metadados (12)": "processando", "relendo": "processando",
                 "consolidando": "processando", "montando planilha do acervo": "processando",
                 "iniciando": "vigiando", "vigiando": "vigiando", "QNAP com problema": "erro",
                 "pasta indisponível": "pasta indisponível",
                 "serviço parado? (sem notícia há 9 min)": "erro", "algo novo": "processando"}
        for situacao, esperado in casos.items():
            self.assertEqual(vigia.estado_do_painel(situacao), esperado, situacao)
            self.assertIn(vigia.estado_do_painel(situacao), vigia.ESTADOS_DO_PAINEL)

    def test_heartbeat_leva_detalhe_e_estado_valido(self):
        v = vigia.Vigia(self.config, None, self.estado, painel=self.painel)
        v.estado.situacao = "copiando 7 de 38"
        v._talvez_heartbeat(forcar=True)
        hb = self.painel.heartbeats[-1]
        self.assertEqual((hb["estado"], hb["detalhe"]), ("processando", {"situacao": "copiando 7 de 38"}))

    def test_falha_de_heartbeat_e_logada_em_warning(self):
        self.painel.heartbeat = lambda dados: False
        self.painel.ultimo_erro = "POST /api/estacoes/heartbeat: HTTP 400"
        v = vigia.Vigia(self.config, None, self.estado, painel=self.painel)
        with self.assertLogs("cv2", level="WARNING") as logs:
            v._talvez_heartbeat(forcar=True)
        self.assertIn("Heartbeat", logs.output[0])


class TestDescartado(Base):
    def test_410_marca_o_lote_descartado_e_para_de_chamar(self):
        chamadas = []

        def reservar(fundo, titulo, chave, **extra):
            chamadas.append(extra)
            self.painel.ultima_reserva = {"pendente": False, "existente": False, "descartado": True,
                                          "mensagem": "Lote descartado pela equipe"}
            return None

        self.painel.reservar = reservar
        pasta = self.scan("F026 - SBU Sami Bussab", "01 - Desenhos e pranchas", "Casa Nova 1980", n=1)
        [r] = self.rodar()
        self.assertEqual(r.status, entrada.DESCARTADO, r.motivo)
        self.assertEqual(len(chamadas), 1)
        self.assertTrue(pasta.exists())  # nada apagado da entrada
        self.assertIn("descartado", [l["acao"] for l in Livro(self.acervo).todas()])
        ultimo = entrada.Estado(self.estado).ultimos()
        self.assertEqual(next(iter(ultimo.values()))["status"], "descartado")
        self.assertEqual(self.rodar(), [])  # a fila ignora o lote descartado (mesma assinatura)
        self.assertEqual(len(chamadas), 1)


# Corpo REAL de GET /api/estacoes/contexto do camp-painel (tests/teste_backend.py, F099).
CONTEXTO_REAL = {"fundos": [
    {"codigo_fundo": "F099", "prefixo": "TST", "nome": "Fundo Teste", "ultimo_projeto": "P0007",
     "proximo_projeto": "P0008",
     "autoridade": {
         "titulares": [{"nome": "Titular Teste", "grafias": ["T. TESTE"], "inicio": 1920, "fim": 1995}],
         "coautores": [{"nome": "Sócio Teste", "inicio": 1960, "fim": 1970}],
         "periodo": {"inicio": 1950, "fim": 1990},
         "autorias_de_projeto": [{"projeto": "F099-P0001", "autores": ["Fulano", "Beltrano"],
                                  "papel_do_titular": "consultor"}]},
     "coautores": ["Sócio Teste"], "periodo_atuacao": {"de": 1950, "ate": 1990}},
    {"codigo_fundo": "F098", "prefixo": "SEM", "nome": "Só Autoridade", "ultimo_projeto": None,
     "proximo_projeto": "P0001",
     "autoridade": {"coautores": [{"nome": "Fulana"}, {"nome": ""}], "periodo": {"inicio": 1960, "fim": 1975}}},
    {"codigo_fundo": "F097", "prefixo": "NUL", "nome": "Sem Período", "autoridade": None,
     "coautores": [], "periodo_atuacao": {"de": None, "ate": None}},
], "operadores": [{"id": 1, "nome": "Beatriz"}]}


class TestAutoridadeDoPainel(unittest.TestCase):
    def test_corpo_real_do_painel(self):
        fundos_ = {f.codigo: f for f in fundos._de_json(CONTEXTO_REAL)}
        self.assertEqual(set(fundos_), {"F099", "F098", "F097"})
        f99 = fundos_["F099"]
        self.assertEqual((f99.sigla, f99.nome, f99.coautores, f99.periodo),
                         ("TST", "Fundo Teste", ("Sócio Teste",), (1950, 1990)))
        self.assertTrue(f99.fora_do_periodo("1995"))
        self.assertFalse(f99.fora_do_periodo("1960"))

    def test_so_autoridade_coautores_nome_e_periodo_inicio_fim(self):
        f98 = {f.codigo: f for f in fundos._de_json(CONTEXTO_REAL)}["F098"]
        self.assertEqual((f98.coautores, f98.periodo), (("Fulana",), (1960, 1975)))

    def test_periodo_nulo_nao_quebra(self):
        f97 = {f.codigo: f for f in fundos._de_json(CONTEXTO_REAL)}["F097"]
        self.assertEqual((f97.coautores, f97.periodo), ((), None))

    def test_formato_antigo_continua(self):
        [f] = fundos._de_json({"fundos": [{"codigo": "F010", "sigla": "FSJ", "nome": "X",
                                           "coautores": "A, B", "periodo_atuacao": {"de": 1980, "ate": 1970}}]})
        self.assertEqual((f.coautores, f.periodo), (("A", "B"), (1970, 1980)))


if __name__ == "__main__":
    unittest.main()
