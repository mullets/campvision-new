"""Triagem por série ANTES da leitura (procedimento padrão, etapa 2). Só código.

Toda pasta é material misturado: a "pasta de 10 pranchas" da Paróquia tinha 9
pranchas, uma carta e 21 fotografias. Ler carta com o prompt de prancha
devolve carimbo vazio e escala nula, por isso a série é decidida antes, com
sinais baratos da imagem de leitura (2000 px, já orientada):

- fração de papel claro e saturação → papel desenhado/escrito × fotografia;
- proporção da folha (A4/ofício em pé) e quantidade de texto (pontos do OCR
  da orientação) → documento textual (S02) × prancha (S01).

Em dúvida, devolve a série padrão com `incerta=True` — vai para a folha de
contatos e para o relatório, nunca é decidida no chute.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageStat

LADO = 400
PAPEL = 200            # luminância a partir da qual o pixel é "papel"
TEXTO_DOCUMENTO = 60   # pontos de OCR de uma página de texto corrido
TEXTO_MINIMO = 12      # abaixo disso, não há texto que ajude


@dataclass
class Triagem:
    serie: str
    incerta: bool
    motivo: str
    papel: float = 0.0
    saturacao: float = 0.0
    proporcao: float = 0.0
    texto: float = 0.0

    def como_dict(self) -> dict:
        return {"serie": self.serie, "incerta": self.incerta, "motivo": self.motivo,
                "papel": round(self.papel, 3), "saturacao": round(self.saturacao, 1),
                "proporcao": round(self.proporcao, 3), "texto": round(self.texto, 1)}


def medir(caminho: Path) -> tuple[float, float, float, float]:
    """(fração de papel claro, saturação média, desvio de luminância, proporção ≥ 1)."""
    with Image.open(caminho) as img:
        img = img.convert("RGB")
        img.thumbnail((LADO, LADO))
        cinza = img.convert("L")
        histo = cinza.histogram()
        total = sum(histo) or 1
        papel = sum(histo[PAPEL:]) / total
        desvio = ImageStat.Stat(cinza).stddev[0]
        saturacao = ImageStat.Stat(img.convert("HSV")).mean[1]
        largura, altura = img.size
    proporcao = max(largura, altura) / max(1, min(largura, altura))
    return papel, saturacao, desvio, proporcao


def _a4_em_pe(proporcao: float, retrato: bool) -> bool:
    return retrato and 1.35 <= proporcao <= 1.70  # A4 1,41 · ofício/carta 1,29–1,65


def triar(caminho: Path, pontos_texto: dict | None, padrao: str = "S01") -> Triagem:
    try:
        papel, saturacao, desvio, proporcao = medir(caminho)
        with Image.open(caminho) as img:
            retrato = img.height > img.width
    except OSError as erro:
        return Triagem(padrao, True, f"não abri a imagem de leitura: {erro}")
    texto = max((pontos_texto or {}).values(), default=0.0)
    m = dict(papel=papel, saturacao=saturacao, proporcao=proporcao, texto=texto)

    # Fotografia: pouco papel claro, tons espalhados, quase nenhum texto.
    if papel < 0.25 and desvio > 25 and texto < TEXTO_MINIMO:
        return Triagem("S03", False, "imagem fotográfica: pouco papel claro e sem texto", **m)
    # Papel: prancha ou documento textual.
    if papel >= 0.45:
        if _a4_em_pe(proporcao, retrato) and texto >= TEXTO_DOCUMENTO:
            return Triagem("S02", False, "folha A4/ofício em pé com texto corrido", **m)
        if _a4_em_pe(proporcao, retrato) and texto >= TEXTO_MINIMO * 2:
            return Triagem(padrao, True, "folha em pé com texto: prancha pequena ou documento?", **m)
        return Triagem("S01", False, "papel com desenho", **m)
    # Prancha com foto colada, cópia escura, slide de desenho...
    return Triagem(padrao, True, "sinais misturados (papel escuro ou foto em prancha)", **m)
