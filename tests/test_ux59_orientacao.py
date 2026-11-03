"""UX-59: folhas chegavam de cabeça para baixo na conferência, todas com "orientação incerta".

Casos reais: F001-P0033 Teatro Piracicaba D00001 (41 "orientação incerta" no projeto) e
F001-P0013 Pavilhão do Brasil D00003/D00004.

Causas (reproduzidas aqui com pranchas sintéticas):
- o OCR de orientação rodava a 1400 px: numa prancha grande o texto do carimbo ficava com
  poucos pixels, o tesseract só devolvia lixo e o lixo da rotação 180° ganhava;
- a pontuação dava +1 a qualquer "palavra plausível", e texto de cabeça para baixo vira
  palavras plausíveis (VIVA, ONVINA…): a folha virada pontuava quase tanto quanto a certa;
- o espelho (vertical) de "DE", "DO"… lê igual: a regra do espelho marcava quase toda folha
  como incerta;
- na dúvida valia o maior lixo: folha sem texto saía girada à toa no preview.
"""

from __future__ import annotations

import random
import shutil
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from PIL import Image, ImageDraw, ImageFont

from nucleo import preparo


def _fonte(tamanho: int):
    preferida = Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf")
    fontes = [preferida] if preferida.exists() else sorted(Path("/usr/share/fonts").rglob("*.ttf"))
    return ImageFont.truetype(str(fontes[0]), tamanho)


TEM_FONTE = Path("/usr/share/fonts").exists() and bool(sorted(Path("/usr/share/fonts").rglob("*.ttf")))
TEM_OCR = bool(shutil.which("tesseract"))


def prancha(texto: bool = True, tamanho: int = 28) -> Image.Image:
    """Prancha grande (desenho de traços + carimbo pequeno no canto), já reduzida como a bruta (3000 px)."""
    random.seed(7)
    largura, altura = 4200, 3000
    img = Image.new("L", (largura, altura), 255)
    d = ImageDraw.Draw(img)
    for _ in range(60):
        x, y = random.randint(100, 3000), random.randint(100, 2400)
        d.line([x, y, x + random.randint(-800, 800), y + random.randint(-800, 800)], fill=0, width=4)
    d.rectangle([3000, 2400, 4150, 2950], outline=0, width=6)
    if texto:
        f = _fonte(tamanho)
        for i, linha in enumerate(["TEATRO MUNICIPAL DE PIRACICABA", "PROJETO: REFORMA E AMPLIACAO",
                                   "ARQUITETO: FULANO DE TAL", "ESCALA 1:100   FOLHA 01/12",
                                   "DATA 10/05/1979   DESENHO: J.S."]):
            d.text((3030, 2420 + i * (tamanho + 30)), linha, fill=0, font=f)
    img.thumbnail((3000, 3000))
    return img.convert("RGB")


class TestPontuacao(unittest.TestCase):
    """Sem tesseract: a pontuação e a regra do mais provável."""

    def test_texto_de_cabeca_para_baixo_quase_nao_pontua(self):
        certo = ("TEATRO MUNICIPAL DE PIRACICABA PROJETO: REFORMA E AMPLIACAO ARQUITETO: FULANO DE TAL "
                 "ESCALA 1:100 FOLHA 01/12 DATA 10/05/1979 DESENHO: J.S.")
        virado = ("S'[:OHNISIA 626T/S0/0T Viva TUTO VHIOS O0T:T VIVOSI WL 3G ONVINA ‘O13 LINOWY "
                  "OVOVIIdWY VWHOJIU :OLIÍOUA VEVIIDVEId 3d IVAIDINNW OULVIL po AN")
        a, b = preparo.pontuar_texto(certo), preparo.pontuar_texto(virado)
        self.assertGreaterEqual(a, preparo.PISO_TEXTO)
        self.assertLess(b, a * (1 - preparo.EMPATE) / 2, (a, b))

    def test_mais_provavel_sem_indicio_fica_como_veio(self):
        self.assertEqual(preparo._mais_provavel({0: 0.0, 90: 2.0, 180: 3.0, 270: 1.0}), 0)
        self.assertEqual(preparo._mais_provavel({0: 10.0, 90: 0.0, 180: 11.0, 270: 0.0}), 0)   # empate com 0
        self.assertEqual(preparo._mais_provavel({0: 2.0, 90: 0.0, 180: 9.0, 270: 0.0}), 180)   # indício: vale
        self.assertEqual(preparo._mais_provavel({0: 29.0, 90: 0.0, 180: 6.0, 270: 0.0}), 0)


@unittest.skipUnless(TEM_OCR and TEM_FONTE, "tesseract ou fonte TTF ausente")
class TestPranchaGrande(unittest.TestCase):
    def test_carimbo_pequeno_em_pe_e_de_cabeca_para_baixo(self):
        # Antes: 22 px → girada 90° e incerta; 36 px → certa, mas marcada incerta.
        for tamanho, graus in ((22, 0), (22, 180), (36, 0)):
            o = preparo.orientar(prancha(tamanho=tamanho).rotate(graus))
            self.assertEqual((o.rotacao, o.espelhada, o.incerta), (graus, False, False), (tamanho, graus, o))

    def test_preview_de_folha_virada_sai_em_pe(self):
        original = prancha()
        with TemporaryDirectory() as tmp:
            bruta = Path(tmp) / "bruta.jpg"
            original.rotate(180).save(bruta, quality=92)
            prep = preparo.preparar(bruta, bruta, Path(tmp) / "leitura.jpg", Path(tmp) / "preview.jpg")
            with Image.open(prep.preview) as preview:
                em_pe = preview.convert("RGB").resize(original.size)
        self.assertEqual(prep.orientacao.rotacao, 180)
        self.assertLessEqual(preparo.distancia(preparo.dhash(em_pe), preparo.dhash(original)), 6)

    def test_sem_texto_fica_como_veio_e_incerta(self):
        for graus in (0, 180):
            o = preparo.orientar(prancha(texto=False).rotate(graus))
            self.assertEqual((o.rotacao, o.incerta), (0, True), (graus, o))


if __name__ == "__main__":
    unittest.main()
