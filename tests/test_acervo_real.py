"""Testes contra a estrutura real do acervo — a da captura de tela.

    BSG-EdificioPiracicaba-AnteProjeto-1979/   ← projeto: JPG/ + TIF/
    ├── info_projeto.json
    ├── status.json
    ├── JPG/
    └── TIF/

    F001 - ARM - Arnaldo Martino/              ← container (fundo)
    └── P0001 - I Simpósio ... - 1979/         ← projeto (marcador)
        ├── info_projeto.json
        ├── status.json
        ├── catalogacao/
        └── 01 - Desenhos e Pranchas/
            ├── 01 - Arquivo Arquivístico (TIFF)/
            └── 03 - Preview (JPG)/            ← é daqui que ele deve ler
"""

from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from nucleo import vigia
from nucleo.config import Config
from nucleo.vigia import CONTAINER, PROJETO, classificar
from tests.test_nucleo import ClienteFalso, prancha_falsa, resposta_padrao

CFG = Config(trabalhadores=1, consolidar_por_projeto=False)


def imagens(pasta: Path, n: int = 2, prefixo: str = "p") -> Path:
    pasta.mkdir(parents=True, exist_ok=True)
    for i in range(n):
        prancha_falsa(pasta / f"{prefixo}{i:03d}.jpg", 600, 400)
    return pasta


def marcar(pasta: Path, status: str = "enviado_windows", info: dict | None = None) -> Path:
    pasta.mkdir(parents=True, exist_ok=True)
    (pasta / "status.json").write_text(json.dumps({"status": status}), encoding="utf-8")
    if info is not None:
        (pasta / "info_projeto.json").write_text(
            json.dumps(info, ensure_ascii=False), encoding="utf-8"
        )
    return pasta


def montar_acervo(raiz: Path) -> None:
    """Reproduz a árvore da captura de tela."""
    # Projeto no formato antigo: JPG/ e TIF/ lado a lado
    antigo = marcar(raiz / "BSG-EdificioPiracicaba-AnteProjeto-1979", info={"ano": "1979"})
    imagens(antigo / "JPG", 3)
    imagens(antigo / "TIF", 3, prefixo="t")

    # Fundo com projetos codificados e imagens duas camadas abaixo
    fundo = raiz / "F001 - ARM - Arnaldo Martino"
    p1 = marcar(fundo / "P0001 - I Simposio Brasil Africa Ocidental - 1979")
    imagens(p1 / "01 - Desenhos e Pranchas" / "01 - Arquivo Arquivístico (TIFF)", 4, "t")
    imagens(p1 / "01 - Desenhos e Pranchas" / "03 - Preview (JPG)", 4)
    (p1 / "catalogacao").mkdir()

    p2 = marcar(fundo / "P0002 - MICE 80 - 1980")
    imagens(p2 / "01 - Desenhos e Pranchas" / "03 - Preview (JPG)", 2)

    fundo2 = raiz / "F002 - BSG - Barretto Segnini"
    p3 = marcar(fundo2 / "P0002 - paroquia Mae Salvador - 1973")
    imagens(p3 / "01 - Desenhos e Pranchas" / "03 - Preview (JPG)", 2)


class TestClassificacao(unittest.TestCase):
    def test_fundo_e_container_projeto_e_projeto(self):
        with TemporaryDirectory() as tmp:
            raiz = Path(tmp)
            montar_acervo(raiz)
            self.assertEqual(classificar(raiz / "F001 - ARM - Arnaldo Martino", CFG), CONTAINER)
            self.assertEqual(
                classificar(raiz / "F001 - ARM - Arnaldo Martino"
                            / "P0001 - I Simposio Brasil Africa Ocidental - 1979", CFG),
                PROJETO,
            )

    def test_projeto_sem_marcador_com_jpg_e_tif(self):
        """`Projeto/{JPG,TIF}` é UM projeto, não dois."""
        with TemporaryDirectory() as tmp:
            raiz = Path(tmp)
            projeto = raiz / "SemMarcador"
            imagens(projeto / "JPG", 2)
            imagens(projeto / "TIF", 2, "t")
            self.assertEqual(classificar(projeto, CFG), PROJETO)


class TestDescobertaNoAcervoReal(unittest.TestCase):
    def _achados(self, raiz: Path) -> dict[str, vigia.Projeto]:
        return {p.nome: p for p in vigia.descobrir(raiz, CFG)}

    def test_acha_os_quatro_projetos_e_nenhum_a_mais(self):
        with TemporaryDirectory() as tmp:
            raiz = Path(tmp)
            montar_acervo(raiz)
            achados = self._achados(raiz)
            self.assertEqual(len(achados), 4, f"achou: {sorted(achados)}")
            for nome in achados:
                self.assertNotIn("Preview", nome)
                self.assertNotIn("TIFF", nome)
                self.assertNotIn("Desenhos", nome)

    def test_fundo_nao_vira_projeto(self):
        with TemporaryDirectory() as tmp:
            raiz = Path(tmp)
            montar_acervo(raiz)
            self.assertNotIn("F001 - ARM - Arnaldo Martino", self._achados(raiz))

    def test_prefere_o_preview_jpg_e_ignora_o_tiff(self):
        with TemporaryDirectory() as tmp:
            raiz = Path(tmp)
            montar_acervo(raiz)
            projeto = self._achados(raiz)["P0001 - I Simposio Brasil Africa Ocidental - 1979"]
            self.assertEqual(len(projeto.pastas_imagens), 1)
            self.assertIn("Preview", projeto.pastas_imagens[0].name)
            self.assertEqual(len(projeto.arquivos(CFG)), 4)

    def test_projeto_antigo_prefere_jpg_a_tif(self):
        with TemporaryDirectory() as tmp:
            raiz = Path(tmp)
            montar_acervo(raiz)
            projeto = self._achados(raiz)["BSG-EdificioPiracicaba-AnteProjeto-1979"]
            self.assertEqual([p.name for p in projeto.pastas_imagens], ["JPG"])

    def test_so_tiff_disponivel_le_o_tiff(self):
        with TemporaryDirectory() as tmp:
            raiz = Path(tmp)
            projeto = marcar(raiz / "P0009 - So Matriz - 1970")
            imagens(projeto / "01 - Desenhos" / "01 - Arquivo Arquivístico (TIFF)", 3, "t")
            achado = vigia.descobrir(raiz, CFG)[0]
            self.assertIn("TIFF", achado.pastas_imagens[0].name)
            self.assertEqual(len(achado.arquivos(CFG)), 3)

    def test_imagens_em_dois_ramos_de_preview_sao_somadas(self):
        with TemporaryDirectory() as tmp:
            raiz = Path(tmp)
            projeto = marcar(raiz / "P0010 - Dois Ramos - 1975")
            imagens(projeto / "01 - Desenhos e Pranchas" / "03 - Preview (JPG)", 2, "a")
            imagens(projeto / "02 - Documentos" / "03 - Preview (JPG)", 3, "b")
            achado = vigia.descobrir(raiz, CFG)[0]
            self.assertEqual(len(achado.pastas_imagens), 2)
            self.assertEqual(len(achado.arquivos(CFG)), 5)

    def test_catalogacao_nao_e_lida_de_volta(self):
        with TemporaryDirectory() as tmp:
            raiz = Path(tmp)
            projeto = marcar(raiz / "P0011 - Com Saida - 1980")
            imagens(projeto / "JPG", 2)
            imagens(projeto / "catalogacao", 5, "lixo")
            achado = vigia.descobrir(raiz, CFG)[0]
            self.assertEqual(len(achado.arquivos(CFG)), 2)

    def test_projeto_marcado_e_vazio_e_pulado_sem_erro(self):
        with TemporaryDirectory() as tmp:
            raiz = Path(tmp)
            marcar(raiz / "P0012 - Vazio - 1990")
            self.assertEqual(vigia.descobrir(raiz, CFG), [])


class TestProcessamentoNoAcervoReal(unittest.TestCase):
    def test_le_o_projeto_inteiro_e_grava_na_raiz_dele(self):
        with TemporaryDirectory() as tmp:
            raiz = Path(tmp)
            montar_acervo(raiz)
            projeto = {p.nome: p for p in vigia.varrer(raiz, CFG)}[
                "P0001 - I Simposio Brasil Africa Ocidental - 1979"
            ]
            evento = vigia.processar(projeto, CFG, ClienteFalso([resposta_padrao()]))
            self.assertEqual(evento.pranchas, 4, "só as 4 do preview, não as 4 do TIFF")
            # checkpoint e planilha na RAIZ do projeto, não dentro do Preview
            self.assertTrue((projeto.pasta / "campvision2_checkpoint.jsonl").exists())
            self.assertTrue((projeto.pasta / "catalogacao" / "catalogacao.xlsx").exists())
            self.assertFalse((projeto.pastas_imagens[0] / "campvision2_checkpoint.jsonl").exists())

    def test_status_existente_e_respeitado(self):
        with TemporaryDirectory() as tmp:
            raiz = Path(tmp)
            projeto = marcar(raiz / "P0013 - Ja Feito - 1970", status="campvision_concluido")
            imagens(projeto / "JPG", 2)
            self.assertEqual(vigia.varrer(raiz, CFG), [], "não pode reprocessar")
            self.assertEqual(len(vigia.descobrir(raiz, CFG)), 1, "mas continua visível")


if __name__ == "__main__":
    unittest.main()


class TestInfoProjeto(unittest.TestCase):
    def test_le_chaves_com_nomes_variados(self):
        from nucleo import info_projeto

        with TemporaryDirectory() as tmp:
            pasta = Path(tmp)
            (pasta / "info_projeto.json").write_text(json.dumps({
                "nome_do_projeto": "Edifício Piracicaba",
                "autoria": "Barretto Segnini",
                "data_projeto": "1979-04-03",
                "municipio": "São Paulo",
                "campo_que_nao_conheco": "seja o que for",
            }, ensure_ascii=False), encoding="utf-8")
            campos = info_projeto.ler(pasta)
            self.assertEqual(campos["projeto"], "Edifício Piracicaba")
            self.assertEqual(campos["arquiteto"], "Barretto Segnini")
            self.assertEqual(campos["ano"], "1979", "extrai o ano de uma data completa")
            self.assertEqual(campos["cidade"], "São Paulo")
            self.assertNotIn("campo_que_nao_conheco", campos)

    def test_json_aninhado_e_achatado(self):
        from nucleo import info_projeto

        with TemporaryDirectory() as tmp:
            pasta = Path(tmp)
            (pasta / "info_projeto.json").write_text(json.dumps({
                "projeto": {"titulo": "MICE 80", "ano": "1980"},
                "palavras_chave": ["planta", "corte"],
            }, ensure_ascii=False), encoding="utf-8")
            campos = info_projeto.ler(pasta)
            self.assertEqual(campos["projeto"], "MICE 80")
            self.assertEqual(campos["ano"], "1980")

    def test_arquivo_corrompido_nao_quebra(self):
        from nucleo import info_projeto

        with TemporaryDirectory() as tmp:
            pasta = Path(tmp)
            (pasta / "info_projeto.json").write_text("{quebrado", encoding="utf-8")
            self.assertEqual(info_projeto.ler(pasta), {})

    def test_info_vence_a_pasta_mas_perde_do_carimbo(self):
        from nucleo import vigia as mod

        with TemporaryDirectory() as tmp:
            raiz = Path(tmp)
            projeto = marcar(
                raiz / "P0001 - Nome Da Pasta - 1999",
                info={"nome": "NOME DO INFO", "ano": "1979"},
            )
            imagens(projeto / "JPG", 1)
            achado = mod.varrer(raiz, CFG)[0]
            # o carimbo falso diz CASA DA PRAIA e não traz ano
            mod.processar(achado, CFG, ClienteFalso([
                dict(resposta_padrao(), ano={"valor": "", "confianca": 0.0})
            ]))
            from nucleo.planilha import ler_json

            leitura = ler_json(achado.pasta / "catalogacao" / "leituras.json")[0]
            self.assertEqual(leitura.valores["projeto"], "CASA DA PRAIA",
                             "carimbo vence tudo")
            self.assertEqual(leitura.valores["ano"], "1979",
                             "sem carimbo, o info vence o 1999 da pasta")
