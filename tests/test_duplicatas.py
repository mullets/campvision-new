"""Passada 1 (docs/metodo-de-leitura.md §2.3 e §2.4): hashes, chave de identidade e marcação de duplicatas (nada é apagado)."""
import io
import tempfile
import unittest
from pathlib import Path

from PIL import Image, ImageDraw

from nucleo import duplicatas as dup


def folha(seed: int, lado: int = 600) -> Image.Image:
    """Prancha sintética: moldura + carimbo iguais em todas (como numa série real) e um desenho interno que muda com a semente."""
    img = Image.new("RGB", (lado, int(lado * 0.7)), "white")
    d = ImageDraw.Draw(img)
    d.rectangle([10, 10, img.width - 10, img.height - 10], outline="black", width=4)
    d.rectangle([img.width - 220, img.height - 110, img.width - 15, img.height - 15], outline="black", width=3)   # carimbo igual
    for i in range(14):                     # traços do desenho, determinísticos por semente
        x0 = 30 + (seed * 37 + i * 61) % (img.width - 260)
        y0 = 30 + (seed * 53 + i * 29) % (img.height - 160)
        d.line([x0, y0, x0 + 40 + (seed * 11 + i * 17) % 160, y0 + 20 + (seed * 7 + i * 13) % 110], fill="black", width=2)
    return img


def salvar(img: Image.Image, caminho: Path, **kw):
    caminho.parent.mkdir(parents=True, exist_ok=True)
    img.save(caminho, **kw)
    return caminho


def jpeg(img: Image.Image, q: int) -> Image.Image:
    b = io.BytesIO(); img.save(b, "JPEG", quality=q)
    return Image.open(io.BytesIO(b.getvalue()))


class Hashes(unittest.TestCase):
    def test_md5_de_arquivo_e_estavel(self):
        with tempfile.TemporaryDirectory() as t:
            a = Path(t) / "a.bin"; a.write_bytes(b"abc")
            self.assertEqual(dup.md5(a), "900150983cd24fb0d6963f7d28e17f72")

    def test_variacoes_de_arquivo_da_mesma_folha_ficam_abaixo_do_limiar_de_marca(self):
        base = folha(1); h0 = dup.hash_perceptual(base)
        for nome, img in (("JPEG q60", jpeg(base, 60)), ("JPEG q30", jpeg(base, 30)),
                          ("reduzida a 90%", base.resize((int(base.width * .9), int(base.height * .9)), Image.LANCZOS))):
            self.assertLessEqual(dup.distancia(h0, dup.hash_perceptual(img)), dup.LIMIAR_MARCA, nome)

    def test_rescan_com_deslocamento_ou_giro_cai_na_faixa_de_revisao_nao_na_de_marca(self):
        # O que um rescan real tem (deslocamento, giro). Medido: 13 a 19. NÃO é marcado; é SUGERIDO para olho humano.
        base = folha(1); h0 = dup.hash_perceptual(base)
        mexidas = {"deslocada 3 px": base.transform(base.size, Image.AFFINE, (1, 0, 3, 0, 1, 3), fillcolor="white"),
                   "girada 1 grau": base.rotate(1, fillcolor="white"),
                   "80% + JPEG q60": jpeg(base.resize((int(base.width * .8), int(base.height * .8)), Image.LANCZOS), 60)}
        for nome, img in mexidas.items():
            dist = dup.distancia(h0, dup.hash_perceptual(img))
            self.assertGreater(dist, dup.LIMIAR_MARCA, nome)
            self.assertLessEqual(dist, dup.LIMIAR_REVISAO, nome)

    def test_folhas_diferentes_do_mesmo_projeto_ficam_acima_do_limiar_de_revisao(self):
        # Mesma moldura e mesmo carimbo, desenho diferente: o caso que o perceptual NÃO pode confundir (medido: mínimo 34).
        for s in range(2, 8):
            self.assertGreater(dup.distancia(dup.hash_perceptual(folha(1)), dup.hash_perceptual(folha(s))), dup.LIMIAR_REVISAO, s)


class ChaveDeIdentidade(unittest.TestCase):
    def test_mesmo_nome_em_pastas_diferentes_sao_arquivos_diferentes(self):
        # Colisão real: Jose-Carlos-Bellucci-2025-02-04-0001.jpg existe em Abílio Diniz e em Giorgi.
        with tempfile.TemporaryDirectory() as t:
            raiz = Path(t)
            a = salvar(folha(1), raiz / "Abilio Diniz" / "Jose-Carlos-Bellucci-2025-02-04-0001.jpg")
            b = salvar(folha(2), raiz / "Giorgi" / "Jose-Carlos-Bellucci-2025-02-04-0001.jpg")
            ka, kb = dup.chave_de_identidade(raiz, a), dup.chave_de_identidade(raiz, b)
            self.assertNotEqual(ka, kb)
            self.assertEqual(ka, "Abilio Diniz/Jose-Carlos-Bellucci-2025-02-04-0001.jpg")
            marcas = dup.marcar([dup.registrar(raiz, a), dup.registrar(raiz, b)])
            self.assertEqual(marcas, {})   # arquivos e obras diferentes: nenhuma marca


class Marcacao(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.raiz = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def reg(self, caminho):
        return dup.registrar(self.raiz, caminho)

    def test_duplicata_exata_byte_a_byte(self):
        img = folha(3)
        a = salvar(img, self.raiz / "P1" / "0001.jpg", quality=90)
        b = self.raiz / "P1" / "0002.jpg"; b.write_bytes(a.read_bytes())
        marcas = dup.marcar([self.reg(a), self.reg(b)])
        self.assertEqual(marcas, {"P1/0002.jpg": dup.Marca("P1/0001.jpg", "exata")})   # só a segunda é marcada; a primeira é a única

    def test_duplicata_perceptual_bytes_diferentes(self):
        # O caso das pranchas 0085 e 0086: mesma folha digitalizada duas vezes.
        a = salvar(folha(4), self.raiz / "McD" / "0085.jpg", quality=95)
        b = salvar(folha(4).resize((520, 364)), self.raiz / "McD" / "0086.jpg", quality=70)
        self.assertNotEqual(dup.md5(a), dup.md5(b))
        marcas = dup.marcar([self.reg(a), self.reg(b)])
        self.assertEqual(marcas, {"McD/0086.jpg": dup.Marca("McD/0085.jpg", "perceptual")})

    def test_nada_e_apagado_e_so_as_duplicatas_aparecem(self):
        arqs = [salvar(folha(s), self.raiz / "P" / f"{s}.jpg") for s in (5, 6, 7)]
        copia = self.raiz / "P" / "7-copia.jpg"; copia.write_bytes(arqs[2].read_bytes())
        marcas = dup.marcar([self.reg(x) for x in [*arqs, copia]])
        self.assertEqual(list(marcas), ["P/7-copia.jpg"])
        self.assertTrue(all(x.exists() for x in [*arqs, copia]))     # nenhum arquivo foi tocado

    def test_tres_copias_apontam_todas_para_a_primeira(self):
        a = salvar(folha(8), self.raiz / "P" / "a.jpg")
        b = self.raiz / "P" / "b.jpg"; b.write_bytes(a.read_bytes())
        c = self.raiz / "P" / "c.jpg"; c.write_bytes(a.read_bytes())
        marcas = dup.marcar([self.reg(x) for x in (a, b, c)])
        self.assertEqual({k: m.duplicata_de for k, m in marcas.items()}, {"P/b.jpg": "P/a.jpg", "P/c.jpg": "P/a.jpg"})

    def test_arquivo_que_nao_e_imagem_so_tem_md5(self):
        pdf = self.raiz / "P" / "doc.pdf"; pdf.parent.mkdir(); pdf.write_bytes(b"%PDF-1.4 nao e imagem")
        r = self.reg(pdf)
        self.assertIsNone(r.phash)
        self.assertEqual(len(r.md5), 32)

    def test_candidatas_a_revisao_lista_o_que_nao_foi_marcado_sem_alterar_nada(self):
        a = salvar(folha(10), self.raiz / "P" / "a.jpg")
        b = salvar(folha(10).rotate(1, fillcolor="white"), self.raiz / "P" / "b.jpg")      # rescan com 1 grau de giro
        c = salvar(folha(11), self.raiz / "P" / "c.jpg")                                    # folha realmente diferente
        regs = [self.reg(x) for x in (a, b, c)]
        self.assertEqual(dup.marcar(regs), {})                                             # nada marcado
        sug = dup.candidatas_a_revisao(regs)
        self.assertEqual([(k, p) for k, p, _ in sug], [("P/b.jpg", "P/a.jpg")])           # só o rescan é sugerido
        self.assertTrue(dup.LIMIAR_MARCA < sug[0][2] <= dup.LIMIAR_REVISAO)
        self.assertTrue(all(x.exists() for x in (a, b, c)))

    def test_limiar_e_configuravel_e_zero_desliga_o_perceptual(self):
        a = salvar(folha(9), self.raiz / "P" / "a.jpg", quality=95)
        b = salvar(folha(9).resize((520, 364)), self.raiz / "P" / "b.jpg", quality=60)
        self.assertEqual(dup.marcar([self.reg(a), self.reg(b)], limiar=-1), {})   # sem perceptual: só a exata conta


if __name__ == "__main__":
    unittest.main()
