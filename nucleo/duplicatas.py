"""Passada 1 do método de leitura: hashes na entrada e marcação de duplicatas. NADA é apagado.

Especificação: docs/metodo-de-leitura.md §2.3 e §2.4.

- `md5` do arquivo: duplicata byte a byte (já achadas em A. Abreu, José Baia, Araruama, Banespa A-9, Safra D27/D28, Barillari CD03).
- hash perceptual: a MESMA folha digitalizada duas vezes com bytes diferentes (caso das pranchas 0085 e 0086 do McDonald's,
  que foi detectado por olho, não por código).
- Chave de identidade = pasta + nome do arquivo, NUNCA o nome só: `Jose-Carlos-Bellucci-2025-02-04-0001.jpg` existe em Abílio
  Diniz (3.464.390 bytes) e em Giorgi (3.464.394 bytes), arquivos e obras diferentes com o mesmo nome.

Marca-se `duplicata_de` (a chave da única) e `tipo_duplicata` (`exata` | `perceptual`); só a única segue para o site.

ATENÇÃO (hipótese: medido só em folhas SINTÉTICAS, falta medir em pares reais, ver o ticket do protocolo de validação):
marcar errado esconde uma folha real do site, e prancha de arquitetura tem carimbo, moldura e muito branco em comum. Medido
(hash de 256 bits, distância de Hamming):
    mesma folha, recompressão JPEG q95/q60/q30 ........ 3 a 6      mesma folha reduzida a 90% ..... 5      a 50% ..... 9
    mesma folha reduzida a 80% + JPEG q60 ............ 13         deslocada 3 px ................. 19     girada 1° . 18
    folhas DIFERENTES com a mesma moldura e carimbo .. mínimo 34 (276 pares), mediana 78
Um rescan real sempre tem deslocamento ou giro: um limiar baixo único perderia o caso das pranchas 0085/0086, e um alto
arriscaria esconder folha de verdade. Por isso são DOIS níveis: até `LIMIAR_MARCA` marca `perceptual`; entre `LIMIAR_MARCA` e
`LIMIAR_REVISAO` só LISTA para revisão humana (`candidatas_a_revisao`), sem marcar nada. O resultado é sempre uma marca ou uma
sugestão, nunca uma exclusão.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

from PIL import Image

LADO_HASH = 16                     # 16x16 = 256 bits (dHash: compara pixels vizinhos)
LIMIAR_MARCA = 8                   # até aqui: marca `perceptual`. HIPÓTESE (ver acima): medir em pares reais antes de adotar.
LIMIAR_REVISAO = 24                # de LIMIAR_MARCA até aqui: só sugere revisão humana. Mesma ressalva.
LIMIAR_PERCEPTUAL_PADRAO = LIMIAR_MARCA


def md5(caminho: Path | str, bloco: int = 1 << 20) -> str:
    h = hashlib.md5()  # noqa: S324 - identidade de arquivo, não segurança
    with open(caminho, "rb") as f:
        for parte in iter(lambda: f.read(bloco), b""):
            h.update(parte)
    return h.hexdigest()


def hash_perceptual(origem: Image.Image | Path | str) -> int:
    """dHash de 256 bits: cinza, reduz para 17x16 e compara cada pixel com o vizinho da direita."""
    img = origem if isinstance(origem, Image.Image) else Image.open(origem)
    try:
        # `draft` faz o JPEG decodificar já reduzido (rápido em folhas grandes); inofensivo nos demais formatos.
        img.draft("L", (LADO_HASH * 8, LADO_HASH * 8))
    except Exception:  # noqa: BLE001
        pass
    pequeno = img.convert("L").resize((LADO_HASH + 1, LADO_HASH), Image.LANCZOS)
    pix = list(pequeno.get_flattened_data() if hasattr(pequeno, "get_flattened_data") else pequeno.getdata())
    valor = 0
    for linha in range(LADO_HASH):
        base = linha * (LADO_HASH + 1)
        for col in range(LADO_HASH):
            valor = (valor << 1) | (1 if pix[base + col] > pix[base + col + 1] else 0)
    return valor


def distancia(a: int, b: int) -> int:
    return (a ^ b).bit_count()


def chave_de_identidade(raiz: Path | str, arquivo: Path | str) -> str:
    """`pasta/nome` relativo à raiz do lote (POSIX). Nunca o nome do arquivo sozinho."""
    return Path(arquivo).resolve().relative_to(Path(raiz).resolve()).as_posix()


@dataclass(frozen=True)
class Registro:
    chave: str
    md5: str
    phash: int | None = None


@dataclass(frozen=True)
class Marca:
    duplicata_de: str      # chave da folha única (a primeira, na ordem recebida)
    tipo_duplicata: str    # "exata" | "perceptual"


def registrar(raiz: Path | str, arquivo: Path | str, com_perceptual: bool = True) -> Registro:
    """Calcula na ENTRADA (passada 1), não depois. O perceptual é opcional (só imagens)."""
    ph = None
    if com_perceptual:
        try:
            ph = hash_perceptual(arquivo)
        except Exception:  # noqa: BLE001 - arquivo que não é imagem (pdf, dng sem preview): só md5
            ph = None
    return Registro(chave_de_identidade(raiz, arquivo), md5(arquivo), ph)


def marcar(registros: list[Registro], limiar: int = LIMIAR_PERCEPTUAL_PADRAO) -> dict[str, Marca]:
    """Devolve {chave: Marca} só para as DUPLICATAS; a única de cada grupo não aparece. Nada é removido.

    A ordem recebida decide qual é a única (a primeira). Exata tem prioridade sobre perceptual.
    """
    unicas: list[Registro] = []
    por_md5: dict[str, Registro] = {}
    marcas: dict[str, Marca] = {}
    for r in registros:
        igual = por_md5.get(r.md5)
        if igual is not None:
            marcas[r.chave] = Marca(igual.chave, "exata")
            continue
        parecida = None
        if r.phash is not None:
            parecida = next((u for u in unicas if u.phash is not None and distancia(u.phash, r.phash) <= limiar), None)
        if parecida is not None:
            marcas[r.chave] = Marca(parecida.chave, "perceptual")
            continue
        por_md5[r.md5] = r
        unicas.append(r)
    return marcas


def candidatas_a_revisao(registros: list[Registro], limiar_marca: int = LIMIAR_MARCA,
                         limiar_revisao: int = LIMIAR_REVISAO) -> list[tuple[str, str, int]]:
    """Pares que NÃO foram marcados mas se parecem (`limiar_marca` < distância <= `limiar_revisao`), para olho humano.

    Devolve [(chave, parecida_com, distancia)], na ordem recebida; a comparação é sempre com as folhas únicas anteriores.
    Não altera nada: é uma sugestão para a folha de contatos / relatório de fim de lote.
    """
    marcadas = marcar(registros, limiar_marca)
    unicas: list[Registro] = []
    sugestoes: list[tuple[str, str, int]] = []
    for r in registros:
        if r.chave in marcadas:
            continue
        if r.phash is not None:
            melhor = min(((distancia(u.phash, r.phash), u.chave) for u in unicas if u.phash is not None), default=None)
            if melhor is not None and limiar_marca < melhor[0] <= limiar_revisao:
                sugestoes.append((r.chave, melhor[1], melhor[0]))
        unicas.append(r)
    return sugestoes
