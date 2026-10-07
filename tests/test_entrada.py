"""Recebimento de lotes brutos das estações. Nenhum teste toca a rede."""

from __future__ import annotations

import json
import os
import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from nucleo import entrada, vigia
from nucleo.config import Config
from tests.test_nucleo import ClienteFalso, prancha_falsa, resposta_padrao


def manifesto(**sobrescritas) -> dict:
    base = {
        "versao": 1,
        "lote_id": "2026-10-07-contex1-001",
        "operador": "Beatriz",
        "operador_email": "beatriz@camp.arq.br",
        "estacao": "Contex 1",
        "tipo_estacao": "contex",
        "fundo": "F026",
        "fundo_nome": "SBU Sami Bussab",
        "projeto": "Edifício Tarumã",
        "projeto_codigo": "P0001",
        "ano": "1972",
        "tipo_material": "pranchas",
        "enviado_em": "2026-10-07T14:00:00",
        "contagens": {"arquivos": 4},
    }
    base.update(sobrescritas)
    return base


def montar_lote(entrada_dir: Path, nome: str, dados: dict | None, n: int = 2) -> Path:
    lote = entrada_dir / nome
    for sub, ext in (("TIF", "tif"), ("JPG", "jpg")):
        (lote / sub).mkdir(parents=True)
        for i in range(n):
            prancha_falsa(lote / sub / f"SBU-Taruma-1972-{i:03d}.{ext}", 900, 700)
    if dados is not None:
        (lote / "manifesto.json").write_text(json.dumps(dados), encoding="utf-8")
    return lote


def cfg(raiz_final: Path, entrada_dir: Path, **extra) -> Config:
    return Config(
        espera_estabilidade_segundos=0, trabalhadores=1,
        pasta_vigiada=str(raiz_final), pasta_entrada=str(entrada_dir), **extra,
    )


def exif_ok(itens, identidade):
    return len(itens), []


class TestManifesto(unittest.TestCase):
    def test_valido_e_serie_derivada(self):
        with TemporaryDirectory() as tmp:
            lote = montar_lote(Path(tmp), "L1", manifesto())
            m = entrada.ler_manifesto(lote)
            self.assertEqual((m.fundo, m.serie, m.contagem_esperada), ("F026", "S01", 4))
            self.assertEqual(m.contexto()["operador"], "Beatriz")

    def test_recusa_fundo_fora_da_tabela(self):
        with TemporaryDirectory() as tmp:
            lote = montar_lote(Path(tmp), "L1", manifesto(fundo="SBU"))
            with self.assertRaises(entrada.ManifestoInvalido):
                entrada.ler_manifesto(lote)

    def test_recusa_campo_faltando(self):
        with TemporaryDirectory() as tmp:
            lote = montar_lote(Path(tmp), "L1", manifesto(operador=""))
            with self.assertRaisesRegex(entrada.ManifestoInvalido, "operador"):
                entrada.ler_manifesto(lote)

    def test_material_desconhecido_exige_serie(self):
        with TemporaryDirectory() as tmp:
            lote = montar_lote(Path(tmp), "L1", manifesto(tipo_material="maquete"))
            with self.assertRaisesRegex(entrada.ManifestoInvalido, "série"):
                entrada.ler_manifesto(lote)


class TestVarredura(unittest.TestCase):
    def test_sem_manifesto_nao_entra(self):
        with TemporaryDirectory() as tmp:
            base = Path(tmp)
            montar_lote(base / "entrada", "enviando", None)
            montar_lote(base / "entrada", "completo", manifesto())
            achados = entrada.varrer(base / "entrada", base / "estado")
            self.assertEqual([l.pasta.name for l in achados], ["completo"])


class TestProcessarLote(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        base = Path(self.tmp.name)
        self.entrada_dir = base / "entrada"
        self.raiz = base / "acervo"
        self.estado = base / "estado"
        self.raiz.mkdir()
        self.entrada_dir.mkdir()

    def tearDown(self):
        self.tmp.cleanup()

    def rodar(self, config=None, exif=exif_ok):
        config = config or cfg(self.raiz, self.entrada_dir)
        lotes = entrada.varrer(self.entrada_dir, self.estado)
        with mock.patch.object(entrada.mod_metadados, "gravar_em_lote", side_effect=exif):
            return [entrada.processar_lote(l, config, ClienteFalso([resposta_padrao()]), self.estado)
                    for l in lotes]

    def test_ponta_a_ponta(self):
        lote = montar_lote(self.entrada_dir, "L1", manifesto())
        originais = {p: p.read_bytes() for p in lote.rglob("*") if p.is_file()}

        [final] = self.rodar()

        self.assertEqual(final["status"], entrada.PRONTO)
        serie = self.raiz / "F026 - SBU Sami Bussab" / "P0001 - Edifício Tarumã (1972)" / "01 - Desenhos e Pranchas"
        self.assertTrue((serie / "TIF" / "SBU-Taruma-1972-000.tif").exists())
        self.assertTrue((serie / "JPG" / "SBU-Taruma-1972-001.jpg").exists())
        # Originais intactos.
        for caminho, conteudo in originais.items():
            self.assertEqual(caminho.read_bytes(), conteudo)
        # JSON final no disco, com contexto e contagens.
        projeto = serie.parent
        no_disco = json.loads((projeto / "catalogacao" / "lotes" / "2026-10-07-contex1-001.json").read_text())
        self.assertEqual(no_disco["status"], "pronto")
        self.assertEqual(no_disco["lote"]["operador"], "Beatriz")
        self.assertEqual(no_disco["lote"]["estacao"], "Contex 1")
        self.assertEqual(no_disco["contagens"]["copiados"], 4)
        self.assertEqual(no_disco["exif"]["gravados"], 4)
        self.assertEqual(len(no_disco["arquivos"]), 4)
        # TIF herda a leitura do JPG irmão.
        tif = next(a for a in no_disco["arquivos"] if a["tipo"] == "tif")
        self.assertEqual(tif["metadados"].get("projeto"), "CASA DA PRAIA")
        # status.json do projeto carrega o contexto e o lote pronto.
        status = json.loads((projeto / "status.json").read_text())
        self.assertEqual(status["status"], "campvision_concluido")
        self.assertEqual(status["ultimo_lote_pronto"], "2026-10-07-contex1-001")
        self.assertEqual(status["contexto_ultimo_lote"]["fundo"], "F026")
        # Não volta à fila.
        self.assertEqual(entrada.varrer(self.entrada_dir, self.estado), [])

    def test_usa_pasta_de_fundo_existente(self):
        (self.raiz / "F026 - SAMI BUSSAB").mkdir()
        montar_lote(self.entrada_dir, "L1", manifesto())
        self.rodar()
        self.assertEqual(sorted(p.name for p in self.raiz.iterdir() if not p.name.startswith("_")),
                         ["F026 - SAMI BUSSAB"])

    def test_contagem_incompleta_espera(self):
        montar_lote(self.entrada_dir, "L1", manifesto(contagens={"arquivos": 10}))
        [r] = self.rodar()
        self.assertEqual(r["status"], "aguardando")
        self.assertFalse(any(self.raiz.iterdir()))
        self.assertEqual(len(entrada.varrer(self.entrada_dir, self.estado)), 1)

    def test_arquivo_recente_espera(self):
        montar_lote(self.entrada_dir, "L1", manifesto())
        config = cfg(self.raiz, self.entrada_dir)
        config.espera_estabilidade_segundos = 3600
        [r] = self.rodar(config)
        self.assertEqual(r["status"], "aguardando")

    def test_exif_falhou_nao_fica_pronto(self):
        montar_lote(self.entrada_dir, "L1", manifesto())
        [final] = self.rodar(exif=lambda itens, ident: (0, ["exiftool não encontrado"]))
        self.assertEqual(final["status"], entrada.ERRO)
        self.assertIn("EXIF", final["erros"][-1])
        # Erro não volta sozinho; --refazer-lote devolve.
        self.assertEqual(entrada.varrer(self.entrada_dir, self.estado), [])
        lote = self.entrada_dir / "L1"
        entrada.registrar(self.estado, {"chave": entrada.chave_do_lote(lote), "status": "refazer"})
        [final] = self.rodar()
        self.assertEqual(final["status"], entrada.PRONTO)

    def test_conflito_de_nome_nao_sobrescreve(self):
        montar_lote(self.entrada_dir, "L1", manifesto())
        self.rodar()
        # Segundo lote do mesmo projeto, mesmo nome de arquivo, conteúdo diferente.
        lote2 = montar_lote(self.entrada_dir, "L2", manifesto(lote_id="L2"))
        prancha_falsa(lote2 / "JPG" / "SBU-Taruma-1972-000.jpg", 800, 600)
        os.utime(lote2 / "JPG" / "SBU-Taruma-1972-000.jpg", (time.time() - 10, time.time() - 10))
        [final] = self.rodar()
        self.assertEqual(final["status"], entrada.ERRO)
        self.assertIn("conteúdo diferente", final["erros"][-1])

    def test_repetir_recebimento_e_idempotente(self):
        montar_lote(self.entrada_dir, "L1", manifesto())
        self.rodar()
        lote = self.entrada_dir / "L1"
        entrada.registrar(self.estado, {"chave": entrada.chave_do_lote(lote), "status": "refazer"})
        [final] = self.rodar()
        self.assertEqual(final["status"], entrada.PRONTO)

    def test_manifesto_invalido_e_recusado(self):
        montar_lote(self.entrada_dir, "L1", manifesto(fundo="X"))
        [r] = self.rodar()
        self.assertEqual(r["status"], "recusado")
        self.assertFalse(any(self.raiz.iterdir()))


class TestVigiaRecebe(unittest.TestCase):
    def test_uma_rodada_recebe_lote(self):
        with TemporaryDirectory() as tmp:
            base = Path(tmp)
            (base / "acervo").mkdir()
            montar_lote(base / "entrada", "L1", manifesto())
            config = cfg(base / "acervo", base / "entrada", auto_atualizar=False)
            v = vigia.Vigia(config, ClienteFalso([resposta_padrao()]), base / "estado")
            with mock.patch.object(entrada.mod_metadados, "gravar_em_lote", side_effect=exif_ok):
                self.assertEqual(v.uma_rodada(), 1)
            self.assertIn("pronto", " ".join(v.estado.ultimas_linhas))


if __name__ == "__main__":
    unittest.main()
