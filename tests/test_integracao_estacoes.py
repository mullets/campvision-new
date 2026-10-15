"""Integração com as estações (contrato C1): JSONs gravados COMO as estações gravam.

As estruturas abaixo copiam o que sistema_windows.py (`_escrever_info_local`,
`_escrever_status_enviado`) e nucleo.py do Mac (`escrever_info_lote`) escrevem.
"""

from __future__ import annotations

import hashlib
import json
import unittest
from datetime import datetime
from pathlib import Path

from nucleo import entrada, fundos, vigia
from tests.test_entrada import Base


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


if __name__ == "__main__":
    unittest.main()
