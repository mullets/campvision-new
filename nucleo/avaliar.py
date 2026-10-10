"""Avaliar uma versão do CV2 contra o gabarito do painel (ticket 89).

`vigia.py --avaliar gabarito.csv` relê as folhas do gabarito (colunas
`arquivo_origem`, `campo`, `valor_correto`), exportado pelo painel a partir das
correções feitas por gente, e devolve o placar por campo e o custo.

NÃO mexe no acervo: não move, não renomeia, não grava em ACERVOS_CAMP. A imagem
de leitura vai para um diretório temporário fora do acervo.
"""

from __future__ import annotations

import csv
import json
import tempfile
from collections import defaultdict
from pathlib import Path

from . import formatos, preparo as mod_preparo
from .grupos import normalizar


def ler_gabarito(caminho: Path) -> dict[str, dict[str, str]]:
    texto = caminho.read_text(encoding="utf-8-sig")
    dialeto = csv.Sniffer().sniff(texto[:2000], ";,\t")
    gab: dict[str, dict[str, str]] = defaultdict(dict)
    for linha in csv.DictReader(texto.splitlines(), dialect=dialeto):
        origem = (linha.get("arquivo_origem") or "").strip()
        campo = (linha.get("campo") or "").strip()
        if origem and campo:
            gab[origem][campo] = (linha.get("valor_correto") or "").strip()
    return dict(gab)


def _indice_origem(raiz_final: Path) -> dict[str, Path]:
    """arquivo_origem (pasta + nome original) -> arquivo no acervo, pelos mapa_origem.json."""
    indice: dict[str, Path] = {}
    for mapa in raiz_final.glob("F*/01 - Projetos/*/catalogacao/mapa_origem.json"):
        try:
            dados = json.loads(mapa.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        for item in dados.values():
            if item.get("origem") and item.get("destino"):
                indice[item["origem"]] = raiz_final / item["destino"]
    return indice


def _achar(origem: str, indice: dict[str, Path]) -> Path | None:
    if origem in indice:
        return indice[origem]
    fim = [k for k in indice if k.endswith("/" + origem) or origem.endswith("/" + k)]
    return indice[fim[0]] if len(fim) == 1 else None


def avaliar(raiz_final: Path, gabarito: dict[str, dict[str, str]], leitor, config=None) -> dict:
    indice = _indice_origem(raiz_final)
    certos: dict[str, int] = defaultdict(int)
    total: dict[str, int] = defaultdict(int)
    erros: list[dict] = []
    nao_achados: list[str] = []
    t_in = t_out = 0
    with tempfile.TemporaryDirectory(prefix="cv2-avaliar-") as tmp:
        for origem, campos in sorted(gabarito.items()):
            arquivo = _achar(origem, indice)
            if arquivo is None or not arquivo.exists():
                nao_achados.append(origem)
                continue
            bruta = formatos.imagem_de_leitura(arquivo, Path(tmp) / f"{arquivo.stem}.bruta.jpg")
            if bruta is None:
                nao_achados.append(origem)
                continue
            leitura_jpg = Path(tmp) / f"{arquivo.stem}.jpg"
            mod_preparo.preparar(bruta, arquivo, leitura_jpg, None)
            leitura = leitor.ler(leitura_jpg)
            t_in += leitura.tokens_entrada
            t_out += leitura.tokens_saida
            for campo, correto in campos.items():
                lido = leitura.valores.get(campo, "") or ""
                total[campo] += 1
                if normalizar(lido) == normalizar(correto):
                    certos[campo] += 1
                else:
                    erros.append({"arquivo_origem": origem, "campo": campo, "correto": correto, "lido": lido})
    placar = {c: {"certos": certos[c], "total": total[c], "taxa": round(certos[c] / total[c], 3) if total[c] else 0.0}
              for c in sorted(total)}
    custo = config.custo_estimado_usd(t_in, t_out) if config is not None else 0.0
    return {"placar": placar, "erros": erros, "nao_achados": nao_achados, "tokens_entrada": t_in,
            "tokens_saida": t_out, "custo_usd": round(custo, 4)}
