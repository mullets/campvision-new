"""JPG do acervo: orientação da câmera/scanner, 16 bits, cor e nitidez do traço."""
import tempfile
import unittest
from pathlib import Path

from PIL import Image, JpegImagePlugin

from nucleo import formatos


class TestQualidadeJpg(unittest.TestCase):
    def setUp(self):
        self.d = Path(tempfile.mkdtemp())

    def test_tag_de_orientacao_e_aplicada(self):
        im = Image.new("RGB", (400, 200), "white")
        ex = im.getexif()
        ex[0x0112] = 6  # câmera em pé
        im.save(self.d / "b.jpg", exif=ex)
        self.assertEqual(formatos.abrir(self.d / "b.jpg", self.d / "x.jpg").size, (200, 400))

    def test_tif_16_bits_nao_estoura(self):
        im = Image.new("I;16", (40, 30))
        im.putdata([30000] * 1200)
        im.save(self.d / "a.tif")
        r = formatos.para_rgb(formatos.abrir(self.d / "a.tif", self.d / "x.jpg"))
        self.assertEqual(r.mode, "RGB")
        self.assertTrue(110 <= r.getpixel((5, 5))[0] <= 125)   # 30000/65535 ≈ 117, não 255

    def test_jpg_sem_subamostragem_e_sem_ampliar(self):
        formatos.salvar_jpg(Image.new("RGB", (100, 80), "red"), self.d / "c.jpg", 6000)
        with Image.open(self.d / "c.jpg") as j:
            self.assertEqual(JpegImagePlugin.get_sampling(j), 0)   # 4:4:4
            self.assertEqual(j.size, (100, 80))

    def test_transparencia_vira_branco(self):
        r = formatos.para_rgb(Image.new("RGBA", (10, 10), (0, 0, 0, 0)))
        self.assertEqual(r.getpixel((1, 1)), (255, 255, 255))


if __name__ == "__main__":
    unittest.main()
