"""Pistas tiradas do caminho da pasta.

A estrutura de pastas do acervo já carrega projeto, ano e fundo. É informação
de graça e vale usar — mas com uma regra rígida de proveniência:

**A pasta NUNCA é mostrada ao modelo de visão.** Se o modelo souber que a pasta
se chama `TeatroDeSantos-1968`, ele passa a confirmar isso no carimbo em vez de
transcrever o que está escrito. A leitura é cega; a pasta entra depois, como
uma segunda fonte, sempre etiquetada como tal.

Com isso a pasta serve para três coisas, nesta ordem de valor:

1. **Conferir** — carimbo e pasta discordando é sinal de prancha na pasta errada.
2. **Preencher o que faltou** — campo ilegível ganha o valor da pasta, marcado
   na planilha com cor própria, nunca se passando por leitura.
3. **Agrupar** — a pasta do projeto é um agrupador melhor que semelhança de
   texto, porque não depende de o carimbo ter sido lido.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from pathlib import Path

# Ano plausível para acervo de arquitetura.
ANO = re.compile(r"(?<!\d)(1[89]\d{2}|20\d{2})(?!\d)")
# Código de fundo/prefixo: sigla curta em caixa alta no começo (OCG-, SBU_).
PREFIXO = re.compile(r"^([A-Z]{2,6})[-_ ]+")
# Código de digitalização: letras seguidas de muitos dígitos (DEST3524).
CODIGO = re.compile(r"^[A-Za-z]{2,6}\d{3,}$")
SEPARADORES = re.compile(r"[-_.\s]+")
CAMELO = re.compile(r"(?<=[a-zà-ÿ])(?=[A-ZÀ-Þ])|(?<=[A-ZÀ-Þ])(?=[A-ZÀ-Þ][a-zà-ÿ])")

PALAVRAS_DE_ESTRUTURA = {
    "jpg", "jpeg", "tif", "tiff", "png", "pdf", "imagens", "digitalizado",
    "digitalizacao", "scans", "originais", "acervo", "acervos", "arquivo",
    "arquivos", "catalogacao", "projetos", "pranchas", "fundo", "colecao",
}


@dataclass
class PistaDePasta:
    """O que o caminho da pasta sugere. Sugere — não afirma."""

    projeto: str = ""
    ano: str = ""
    fundo: str = ""
    segmentos: list[str] = field(default_factory=list)

    def como_campos(self) -> dict[str, str]:
        """Nos nomes de campo do esquema, para casar com os do carimbo."""
        return {k: v for k, v in (("projeto", self.projeto), ("ano", self.ano)) if v}


def _limpar(texto: str) -> str:
    """Deixa o nome da pasta legível: tira código, ano, separador e CamelCase."""
    bruto = texto.strip()
    bruto = ANO.sub(" ", bruto)
    bruto = PREFIXO.sub("", bruto)
    partes: list[str] = []
    for pedaco in SEPARADORES.split(bruto):
        pedaco = pedaco.strip()
        if not pedaco or CODIGO.match(pedaco):
            continue
        partes.append(CAMELO.sub(" ", pedaco))
    limpo = re.sub(r"\s+", " ", " ".join(partes)).strip(" -_.")
    return limpo


def _e_estrutural(nome: str) -> bool:
    simples = re.sub(r"[^a-z]", "", nome.lower())
    return simples in PALAVRAS_DE_ESTRUTURA or bool(ANO.fullmatch(nome.strip()))


def extrair(pasta: Path, raiz: Path | None = None) -> PistaDePasta:
    """Lê projeto, ano e fundo do caminho.

    O projeto vem da pasta folha; o ano, do primeiro segmento que for um ano
    (da folha para cima); o fundo, do segmento mais alto que não seja ano nem
    palavra de estrutura.
    """
    try:
        segmentos = list(pasta.relative_to(raiz).parts) if raiz else [pasta.name]
    except ValueError:
        segmentos = [pasta.name]
    segmentos = [s for s in segmentos if s and not s.startswith(".")]
    if not segmentos:
        return PistaDePasta()

    pista = PistaDePasta(segmentos=segmentos)
    # Se a folha é estrutural (JPG, TIF, imagens), o projeto está acima dela.
    indice_folha = len(segmentos) - 1
    while indice_folha > 0 and _e_estrutural(segmentos[indice_folha]):
        indice_folha -= 1
    folha = segmentos[indice_folha]
    pista.projeto = _limpar(folha)

    # Ano: procura da folha para cima; segmento que É um ano vence o embutido.
    for segmento in reversed(segmentos):
        if ANO.fullmatch(segmento.strip()):
            pista.ano = segmento.strip()
            break
    if not pista.ano:
        for segmento in reversed(segmentos):
            achado = ANO.search(segmento)
            if achado:
                pista.ano = achado.group(1)
                break

    # Fundo: o segmento mais alto que não seja ano nem palavra de estrutura.
    for segmento in segmentos[:indice_folha]:
        if not _e_estrutural(segmento):
            pista.fundo = _limpar(segmento) or segmento.strip()
            break
    if not pista.fundo:
        achado = PREFIXO.match(folha)
        if achado:
            pista.fundo = achado.group(1)

    return pista


def de_url_smb(texto: str) -> Path:
    """Converte smb://host/share/resto no caminho POSIX onde o macOS monta.

    O Finder monta em /Volumes/<share>, então o host e o `._smb._tcp.local`
    do Bonjour são descartados. Texto que já é caminho passa direto.
    """
    from urllib.parse import unquote

    bruto = (texto or "").strip()
    if not bruto.lower().startswith(("smb://", "cifs://", "afp://")):
        return Path(bruto).expanduser()

    resto = bruto.split("://", 1)[1]
    partes = [unquote(p) for p in resto.split("/") if p]
    if len(partes) < 2:  # só o host, sem share
        return Path("/Volumes")
    # partes[0] é o host (Server-Camp._smb._tcp.local); o share vem depois.
    return Path("/Volumes").joinpath(*partes[1:])


def _normalizar(texto: str) -> str:
    import unicodedata

    sem_acento = "".join(
        c for c in unicodedata.normalize("NFD", texto or "")
        if unicodedata.category(c) != "Mn"
    )
    return re.sub(r"[^a-z0-9 ]+", " ", sem_acento.lower()).strip()


def combinam(a: str, b: str, limiar: float = 0.62) -> bool:
    """Duas grafias falam da mesma coisa? Tolerante, porque uma vem de pasta."""
    na, nb = _normalizar(a), _normalizar(b)
    if not na or not nb:
        return True  # sem informação não é divergência
    if na in nb or nb in na:
        return True
    palavras_a = {p for p in na.split() if len(p) > 3}
    palavras_b = {p for p in nb.split() if len(p) > 3}
    if palavras_a & palavras_b:
        return True
    return SequenceMatcher(None, na, nb).ratio() >= limiar


def aplicar(
    leituras: list,
    pista: PistaDePasta,
    preencher_faltantes: bool = True,
    do_info: dict[str, str] | None = None,
) -> tuple[int, int]:
    """Cruza as leituras com as fontes secundárias.

    Prioridade: carimbo lido > info_projeto.json > nome da pasta. Nada
    sobrescreve o que o carimbo disse; e o info, sendo curadoria humana
    anterior, vence a convenção de nome de pasta.

    Devolve (quantos_campos_preenchidos, quantas_divergencias).
    """
    preenchidos = divergencias = 0
    # O info entra primeiro no dicionário, então prevalece sobre a pasta.
    campos_da_pasta = {**pista.como_campos()}
    campos_da_pasta.update({k: v for k, v in (do_info or {}).items() if v})

    for leitura in leituras:
        leitura.pista_projeto = pista.projeto
        leitura.pista_ano = pista.ano
        leitura.pista_fundo = pista.fundo

        leitura.campos_do_info = dict(do_info or {})

        for campo, valor_pasta in campos_da_pasta.items():
            lido = (leitura.valores.get(campo) or "").strip()
            if lido:
                if not combinam(lido, valor_pasta):
                    leitura.divergencias.append(campo)
                    divergencias += 1
                continue
            if not preencher_faltantes:
                continue
            leitura.valores[campo] = valor_pasta
            # Confiança de pasta é deliberadamente média: é uma pista boa, não
            # uma leitura. A planilha pinta esses campos com cor própria.
            leitura.confiancas[campo] = 0.5
            leitura.campos_da_pasta.append(campo)
            preenchidos += 1

    return preenchidos, divergencias
