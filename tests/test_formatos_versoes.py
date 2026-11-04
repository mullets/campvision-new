"""CAMP-FORMATOS: a mesma prancha em vários formatos/versões vira UM documento (contrato 2.8).

Caso real (F001 Arnaldo Martino, 29/10): TIFF/, DNG/, JPG/, "JPG LIMPO"/ e "fotos em alta" ×
"fotos para internet" — com nomes diferentes — viravam 2–3 folhas iguais por prancha.
"""

from __future__ import annotations

import json
import random
from pathlib import Path

from PIL import Image, ImageDraw

from nucleo import entrada, formatos
from tests.test_entrada import Base


def prancha(semente: int, largura: int = 1200, altura: int = 800) -> Image.Image:
    """Prancha falsa com desenho próprio (dHash distinto por semente)."""
    rnd = random.Random(semente)
    img = Image.new("RGB", (largura, altura), "white")
    d = ImageDraw.Draw(img)
    for _ in range(14):
        x0, y0 = rnd.randint(0, largura - 200), rnd.randint(0, altura - 200)
        cinza = rnd.randint(0, 160)
        d.rectangle([x0, y0, x0 + rnd.randint(80, 400), y0 + rnd.randint(60, 300)], fill=(cinza, cinza, cinza))
    d.rectangle([largura * 0.7, altura * 0.8, largura - 10, altura - 10], outline="black", width=5)
    return img


def limpo(img: Image.Image) -> Image.Image:
    """'JPG LIMPO': a mesma prancha clareada (sem a sujeira do scanner)."""
    return img.point(lambda v: min(255, int(v * 1.08) + 6))


def projetos(acervo: Path) -> list[Path]:
    return sorted(p for p in (acervo / "F026 - SBU Sami Bussab" / "01 - Projetos").iterdir() if p.is_dir())


def codigos(projeto: Path) -> set[str]:
    return {p.stem for p in projeto.rglob("F026-*") if p.is_file() and "catalogacao" not in p.parts}


class TestPastasDeVersao(Base):
    def test_reconhece_pastas_de_formato_e_versao(self):
        for nome in ("TIFF", "02 - JPG", "JPG LIMPO", "Jpg Limpo", "limpo", "fotos em alta",
                     "Fotos para Internet", "alta", "baixa", "internet", "web", "alta resolução",
                     "Baixa Resolução", "originais", "tratadas", "01 - DNG"):
            self.assertTrue(entrada._e_pasta_de_formato(nome), nome)
        for nome in ("03 - Fotografias", "Fotos", "Negativos", "Casa da Praia", "S01 Pranchas",
                     "01 - Arquivo Arquivístico (TIFF)", "03 - Preview (JPG)"):
            self.assertFalse(entrada._e_pasta_de_formato(nome), nome)

    def test_projeto_com_jpg_limpo_e_um_lote_so(self):
        pasta = self.scanners / "F026" / "P0033 - Casa"
        for sub in ("TIFF", "DNG", "JPG", "JPG LIMPO"):
            (pasta / sub).mkdir(parents=True)
            prancha(1).save(pasta / sub / f"x.{'jpg' if 'JPG' in sub else 'tif'}")
        self.assertEqual([u.pasta for u in entrada.descobrir(self.scanners)], [pasta])

    def test_fotos_em_alta_e_para_internet_e_um_lote_so(self):
        pasta = self.scanners / "F026" / "P0001 - Casa"
        for sub in ("fotos em alta", "fotos para internet"):
            (pasta / sub).mkdir(parents=True)
            prancha(1).save(pasta / sub / "foto 1.jpg")
        self.assertEqual([u.pasta for u in entrada.descobrir(self.scanners)], [pasta])


class TestAgrupar(Base):
    def test_radical_do_nome_entre_pastas_de_versao(self):
        raiz = self.scanners / "u"
        arquivos = []
        for sub, nome in (("TIFF", "Prancha 01.tif"), ("JPG LIMPO", "prancha_1_limpo.jpg"),
                          ("TIFF", "Prancha 02.tif"), ("JPG LIMPO", "prancha_02 LIMPO.jpg")):
            (raiz / sub).mkdir(parents=True, exist_ok=True)
            (raiz / sub / nome).write_bytes(b"x")
            arquivos.append(raiz / sub / nome)
        docs = formatos.agrupar(arquivos, raiz=raiz, assinatura=lambda p: None)
        self.assertEqual(sorted(sorted(v.name for v in d.versoes) for d in docs),
                         [["Prancha 01.tif", "prancha_1_limpo.jpg"], ["Prancha 02.tif", "prancha_02 LIMPO.jpg"]])
        papeis = docs[0].papeis()
        tif = next(v for v in docs[0].versoes if v.suffix == ".tif")
        self.assertEqual(papeis[tif]["papel"], "matriz")

    def test_mesma_pasta_nunca_junta(self):
        raiz = self.scanners / "u"
        (raiz / "JPG").mkdir(parents=True)
        (raiz / "TIFF").mkdir(parents=True)
        for nome in ("a.jpg", "a final.jpg"):
            prancha(5).save(raiz / "JPG" / nome)
        docs = formatos.agrupar(sorted(raiz.rglob("*.jpg")), raiz=raiz)
        self.assertEqual(len(docs), 2)


class TestVersoesNoRecebimento(Base):
    def unidade_f001(self, n: int = 3) -> Path:
        """TIFF/DNG/JPG/JPG LIMPO com NOMES diferentes; o JPG veio girado 90°."""
        pasta = self.scanners / "F026" / "Desenhos" / "Casa Martino"
        for sub in ("TIFF", "DNG", "JPG", "JPG LIMPO"):
            (pasta / sub).mkdir(parents=True)
        for i in range(n):
            img = prancha(100 + i)
            img.save(pasta / "TIFF" / f"Prancha {i + 1:02d}.tif")
            img.save(pasta / "DNG" / f"IMG_{4400 + i}.dng", format="TIFF",   # DNG falso: TIFF que o PIL abre
                     compression="tiff_lzw")                         # (bytes diferentes do TIFF)
            img.rotate(90, expand=True).save(pasta / "JPG" / f"DSC_{i + 7:04d}.jpg", quality=92)
            limpo(img).save(pasta / "JPG LIMPO" / f"arnaldo martino {chr(65 + i)}.jpg", quality=95)
        return pasta

    def test_formatos_de_nomes_diferentes_viram_um_documento_por_prancha(self):
        self.unidade_f001(3)
        [r] = self.rodar()
        self.assertEqual(r.status, entrada.PRONTO, r.motivo)
        [projeto] = projetos(self.acervo)
        self.assertEqual(len(codigos(projeto)), 3, sorted(codigos(projeto)))
        status = json.loads((projeto / "status.json").read_text())
        self.assertEqual((status["folhas"], status["imagens"]), (3, 12))
        serie = next(projeto.glob("01 - *"))
        self.assertEqual(sorted(p.name for p in serie.iterdir() if p.is_dir()), ["DNG", "JPG", "JPG V2", "TIF"])
        pacote = json.loads((projeto / "catalogacao" / "pacote_tainacan.json").read_text())
        docs = pacote["documentos"] + pacote["retirados"]
        self.assertEqual(len(docs), 3)
        for doc in docs:
            versoes = doc["versoes"]
            self.assertEqual([v["papel"] for v in versoes], ["matriz", "derivado", "derivado", "derivado"])
            self.assertEqual(sorted(v["formato"] for v in versoes), ["dng", "jpg", "jpg limpo", "tif"])
            self.assertTrue(versoes[0]["arquivo"].endswith(".tif"))
            self.assertEqual(sorted(v["arquivo"] for v in versoes), sorted(doc["arquivos"]))
            self.assertIsNone(doc["duplicata_de"])
        # a leitura saiu do JPG LIMPO (a pasta JPG/ do acervo); o JPG girado sabe o próprio giro
        preparo = json.loads((projeto / "catalogacao" / "preparo.json").read_text())
        for prep in preparo.values():
            self.assertEqual(prep["origem_formato"], "jpg")
            self.assertIn(prep["giro_versoes"]["JPG V2"], (90, 270))
        mapa = json.loads((projeto / "catalogacao" / "mapa_origem.json").read_text())
        limpos = [m for m in mapa.values() if m["formato"] == "jpg limpo"]
        self.assertEqual(len(limpos), 3)
        self.assertTrue(all("/JPG/" in m["destino"] for m in limpos))

    def test_fotos_em_alta_e_para_internet_com_o_mesmo_nome(self):
        pasta = self.scanners / "F026" / "Fotos" / "Casa Martino"
        for sub in ("fotos em alta", "fotos para internet"):
            (pasta / sub).mkdir(parents=True)
        for i in range(2):
            img = prancha(300 + i)
            img.save(pasta / "fotos em alta" / f"foto {i + 1}.jpg", quality=95)
            img.resize((600, 400)).save(pasta / "fotos para internet" / f"foto {i + 1}.jpg", quality=80)
        [r] = self.rodar()
        self.assertEqual(r.status, entrada.PRONTO, r.motivo)
        [projeto] = projetos(self.acervo)
        self.assertEqual(len(codigos(projeto)), 2)
        pacote = json.loads((projeto / "catalogacao" / "pacote_tainacan.json").read_text())
        for doc in pacote["documentos"] + pacote["retirados"]:
            self.assertEqual([(v["formato"], v["papel"]) for v in doc["versoes"]],
                             [("jpg alta", "matriz"), ("jpg internet", "publicacao")])
            self.assertIn("/JPG INTERNET/", doc["versoes"][1]["arquivo"])

    def test_pasta_sem_formato_continua_como_antes(self):
        pasta = self.scanners / "F026" / "Fotos" / "Casa Martino"
        pasta.mkdir(parents=True)
        prancha(7).save(pasta / "a.jpg")
        prancha(8).save(pasta / "b.jpg")
        prancha(7).save(pasta / "c.jpg", quality=60)   # mesma imagem, mesma pasta: continua folha (duplicata)
        [r] = self.rodar()
        self.assertEqual(r.status, entrada.PRONTO, r.motivo)
        [projeto] = projetos(self.acervo)
        self.assertEqual(len(codigos(projeto)), 3)

    def test_refazer_lote_nao_cria_d_para_versao_registrada(self):
        self.config.apagar_original_apos_pronto = False
        pasta = self.scanners / "F026" / "Desenhos" / "Casa Martino"
        for sub in ("TIFF", "JPG"):
            (pasta / sub).mkdir(parents=True)
        imagens = [prancha(500 + i) for i in range(2)]
        for i, img in enumerate(imagens):
            img.save(pasta / "TIFF" / f"Prancha {i + 1:02d}.tif")
            img.save(pasta / "JPG" / f"DSC_{i + 50:04d}.jpg", quality=92)
        [r] = self.rodar()
        self.assertEqual(r.status, entrada.PRONTO, r.motivo)
        [projeto] = projetos(self.acervo)
        antes = codigos(projeto)
        self.assertEqual(len(antes), 2)
        # o mesmo lote de novo (--refazer-lote): nada muda
        entrada.Estado(self.estado).anotar(str(Path("F026/Desenhos/Casa Martino")), "refazer")
        [r2] = self.rodar()
        self.assertEqual(r2.status, entrada.PRONTO, r2.motivo)
        self.assertEqual(codigos(projeto), antes)
        # chegou o JPG LIMPO depois (outros nomes): vira versão do D que já existe, nunca D novo
        (pasta / "JPG LIMPO").mkdir()
        for i, img in enumerate(imagens):
            limpo(img).save(pasta / "JPG LIMPO" / f"limpo {chr(65 + i)}.jpg", quality=95)
        entrada.Estado(self.estado).anotar(str(Path("F026/Desenhos/Casa Martino")), "refazer")
        [r3] = self.rodar()
        self.assertEqual(r3.status, entrada.PRONTO, r3.motivo)
        self.assertEqual(projetos(self.acervo), [projeto])
        self.assertEqual(codigos(projeto), antes)
        mapa = json.loads((projeto / "catalogacao" / "mapa_origem.json").read_text())
        limpos = [m for m in mapa.values() if m.get("formato") == "jpg limpo"]
        self.assertEqual(len(limpos), 2)
        for m in limpos:
            self.assertIn(Path(m["destino"]).stem, antes)
            self.assertTrue((self.acervo / m["destino"]).exists())
        pacote = json.loads((projeto / "catalogacao" / "pacote_tainacan.json").read_text())
        for doc in pacote["documentos"] + pacote["retirados"]:
            self.assertEqual(len(doc["versoes"]), 3)


class TestGiroDasVersoes(Base):
    def test_versao_girada_90_sai_em_pe_com_a_lida(self):
        from nucleo import derivados

        serie = self.acervo / "serie"
        (serie / "JPG").mkdir(parents=True)
        (serie / "JPG V2").mkdir()
        img = prancha(9)
        img.save(serie / "JPG" / "D1.jpg", quality=95)
        img.rotate(90, expand=True).save(serie / "JPG V2" / "D1.jpg", quality=95)
        giro = formatos.giro_relativo(serie / "JPG V2" / "D1.jpg", serie / "JPG" / "D1.jpg")
        self.assertEqual(giro, 90)   # PIL.rotate(90) é anti-horário: volta girando 90° no horário
        prep = {"rotacao": 0, "espelhada": False, "incerta": False, "giro_versoes": {"JPG V2": giro}}
        self.assertFalse(derivados.orientar_versao(serie / "JPG" / "D1.jpg", prep))   # a lida já está em pé
        self.assertTrue(derivados.orientar_versao(serie / "JPG V2" / "D1.jpg", prep))
        with Image.open(serie / "JPG V2" / "D1.jpg") as girada:
            self.assertEqual(girada.size, img.size)
        prep["giro_versoes"] = {"JPG V2": None}   # não deu para saber: não gira
        self.assertFalse(derivados.orientar_versao(serie / "JPG V2" / "D1.jpg", prep))
