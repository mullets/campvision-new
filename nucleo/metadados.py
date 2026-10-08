"""Metadados embarcados no arquivo.

Regra da casa: **toda imagem que sai daqui leva o nome e o site da CAMP dentro
dela**. Nome de arquivo se perde, pasta se reorganiza, planilha fica para trás
— o metadado viaja junto com a imagem. Se alguém baixar uma prancha do portal
daqui a dez anos, o crédito ainda está lá.

A escrita é feita em **lote**, com um único processo do exiftool para todos os
arquivos (`-@ argfile`). Um processo por arquivo levaria minutos num acervo de
mil pranchas; assim leva segundos.

Nada é escrito no original: a Fase 2 copia, e o metadado vai na cópia.
"""

from __future__ import annotations

import json
import logging
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

from .config import VERSAO_BUILD

_log = logging.getLogger("cv2.metadados")

TEMPO_LIMITE_LOTE = 900


@dataclass
class Identidade:
    """Quem assina o acervo. Vem do config.json, sessão 'identidade'."""

    nome: str = ""
    site: str = ""
    licenca: str = ""           # ex: "CC BY-NC 4.0" ou "Todos os direitos reservados"
    contato: str = ""

    @property
    def credito(self) -> str:
        """Nome + site, que é o que vai em Credit/Source."""
        return f"{self.nome} ({self.site})" if self.site else self.nome

    def direitos(self, autor: str = "") -> str:
        """Linha de copyright: autor da obra, depois a instituição guardiã."""
        partes = [p for p in (autor.strip(), self.nome.strip()) if p]
        linha = " / ".join(partes) or self.nome
        if self.licenca:
            linha = f"{linha} — {self.licenca}"
        return linha

    def problemas(self) -> list[str]:
        """O que falta preencher. O app avisa alto em vez de assinar vazio."""
        faltando = []
        if not self.nome.strip():
            faltando.append("identidade.nome")
        if not self.site.strip():
            faltando.append("identidade.site")
        return faltando


def montar_argumentos(caminho: Path, campos: dict[str, str], identidade: Identidade) -> list[str]:
    """Monta os argumentos de exiftool para UM arquivo.

    `campos` usa os rótulos da planilha (Projeto, Arquiteto, Cidade...).
    Campo vazio é omitido: melhor ausente do que presente e em branco.

    Chaves com `_` na frente são do acervo, não do carimbo:
    `_credito` (crédito do fundo, vence o da identidade), `_codigo`
    (F002-P0002-1975-S01-D00017), `_fundo`, `_serie`, `_operador`,
    `_estacao`, `_digitalizado_em`.
    """
    extras = {k: str(v).strip() for k, v in campos.items() if k.startswith("_") and v}
    campos = {k: v for k, v in campos.items() if not k.startswith("_")}
    def pega(*rotulos: str) -> str:
        for rotulo in rotulos:
            valor = (campos.get(rotulo) or "").strip()
            if valor:
                return valor
        return ""

    autor = pega("Arquiteto", "Escritório")
    titulo = pega("Título da prancha", "Tipo", "Projeto")
    projeto = pega("Projeto")
    ano = pega("Ano")
    cidade = pega("Cidade")
    uf = pega("UF")
    direitos = identidade.direitos(autor)

    descricao_partes = [
        f"{rotulo}: {valor}"
        for rotulo, valor in campos.items()
        if valor and str(valor).strip()
    ]
    credito = extras.get("_credito") or identidade.credito
    if extras.get("_codigo"):
        descricao_partes.insert(0, f"Código: {extras['_codigo']}")
    descricao = " | ".join(descricao_partes)[:1900]
    rodape = credito if extras.get("_credito") else (f"Acervo: {credito}" if credito else "")
    if rodape:
        descricao = f"{descricao} | {rodape}" if descricao else rodape

    palavras = [p for p in (projeto, autor, pega("Tipo"), cidade, ano, identidade.nome) if p]

    argumentos = [
        "-overwrite_original",
        "-charset", "utf8",
        "-charset", "iptc=UTF8",
        "-codedcharacterset=utf8",
    ]

    # Limpa o que o scanner ou um lote anterior deixou (método §2.5): título
    # "Acervo dos Arquitetos — Ruth Verde Zein" herdado chegou a ir para o site.
    for herdado in ("XMP-dc:Title", "XMP-dc:Description", "XMP-dc:Subject", "XMP-photoshop:Headline",
                    "EXIF:ImageDescription", "EXIF:XPTitle", "EXIF:XPSubject", "EXIF:XPComment",
                    "IPTC:ObjectName", "IPTC:Caption-Abstract", "IPTC:Keywords", "PDF:Title",
                    "PDF:Subject", "PDF:Keywords"):
        argumentos.append(f"-{herdado}=")

    def adicionar(tag: str, valor: str) -> None:
        if valor:
            argumentos.append(f"-{tag}={valor}")

    # --- Autoria da obra ---
    adicionar("EXIF:Artist", autor)
    adicionar("XMP-dc:Creator", autor)
    adicionar("IPTC:By-line", autor)

    # --- Crédito institucional: SEMPRE, mesmo sem autor identificado ---
    adicionar("XMP-dc:Publisher", identidade.nome)
    adicionar("XMP-photoshop:Credit", credito)
    adicionar("XMP-photoshop:Source", credito)
    adicionar("IPTC:Credit", credito)
    adicionar("IPTC:Source", credito)
    adicionar("XMP-xmpRights:WebStatement", identidade.site)
    adicionar("XMP-iptcCore:CreatorWorkURL", identidade.site)
    adicionar("XMP-iptcCore:CreatorContactInfoCiEmailWork", identidade.contato)

    # --- Direitos ---
    adicionar("EXIF:Copyright", direitos)
    adicionar("XMP-dc:Rights", direitos)
    adicionar("IPTC:CopyrightNotice", direitos)
    adicionar("XMP-xmpRights:UsageTerms", identidade.licenca or direitos)
    argumentos.append("-XMP-xmpRights:Marked=True")

    # --- Descrição do conteúdo ---
    adicionar("XMP-dc:Title", titulo)
    adicionar("IPTC:ObjectName", titulo[:64])
    adicionar("XMP-photoshop:Headline", projeto)
    adicionar("EXIF:ImageDescription", descricao)
    adicionar("XMP-dc:Description", descricao)
    adicionar("IPTC:Caption-Abstract", descricao)
    # Ano sem mês e dia: registrar "1968:01:01" seria inventar precisão que o
    # carimbo não tem. XMP aceita o ano sozinho.
    adicionar("XMP-dc:Date", ano)
    adicionar("XMP-photoshop:City", cidade)
    adicionar("XMP-photoshop:State", uf)
    adicionar("IPTC:City", cidade)
    adicionar("IPTC:Province-State", uf)
    for palavra in palavras:
        argumentos.append(f"-XMP-dc:Subject+={palavra}")
        argumentos.append(f"-IPTC:Keywords+={palavra}")

    # --- Identificação no acervo ---
    adicionar("XMP-dc:Identifier", extras.get("_codigo", ""))
    adicionar("XMP-photoshop:TransmissionReference", extras.get("_codigo", ""))
    adicionar("IPTC:OriginalTransmissionReference", extras.get("_codigo", "")[:32])
    adicionar("XMP-xmpMM:PreservedFileName", extras.get("_nome_original", ""))
    quem = " / ".join(p for p in (extras.get("_operador", ""), extras.get("_estacao", "")) if p)
    adicionar("XMP-photoshop:CaptionWriter", quem)
    adicionar("XMP-xmp:MetadataDate", extras.get("_digitalizado_em", ""))

    adicionar("XMP-xmp:CreatorTool", f"CAMP Vision 2 ({VERSAO_BUILD})")
    argumentos.append(str(caminho))
    return argumentos


def _montar_argfile(itens: list[tuple[Path, dict[str, str]]], identidade: Identidade) -> str:
    """Um argumento por linha, blocos separados por -execute (formato do exiftool)."""
    linhas: list[str] = []
    for caminho, campos in itens:
        linhas.extend(montar_argumentos(caminho, campos, identidade))
        linhas.append("-execute")
    return "\n".join(linhas) + "\n"


def disponivel() -> bool:
    return shutil.which("exiftool") is not None


def gravar_em_lote(
    itens: list[tuple[Path, dict[str, str]]], identidade: Identidade
) -> tuple[int, list[str]]:
    """Grava os metadados de todos os arquivos num processo só.

    Devolve (quantos_gravados, avisos).
    """
    if not itens:
        return 0, []

    avisos: list[str] = []
    faltando = identidade.problemas()
    if faltando:
        avisos.append(
            "Identidade incompleta (" + ", ".join(faltando) + ") — "
            "preencha em ~/.campvision2/config.json antes de publicar estas imagens."
        )
        _log.warning(avisos[-1])

    if not disponivel():
        aviso = "exiftool não encontrado no PATH — nenhum metadado foi gravado. brew install exiftool"
        _log.error(aviso)
        return 0, avisos + [aviso]

    conteudo = _montar_argfile(itens, identidade)
    with tempfile.NamedTemporaryFile(
        "w", suffix=".args", delete=False, encoding="utf-8"
    ) as arquivo:
        arquivo.write(conteudo)
        caminho_args = Path(arquivo.name)

    try:
        proc = subprocess.run(
            ["exiftool", "-@", str(caminho_args)],
            capture_output=True, text=True, timeout=TEMPO_LIMITE_LOTE,
        )
    except (OSError, subprocess.TimeoutExpired) as erro:
        _log.error("exiftool falhou: %s", erro)
        return 0, avisos + [str(erro)]
    finally:
        caminho_args.unlink(missing_ok=True)

    saida = proc.stdout + proc.stderr
    gravados = saida.count("1 image files updated")
    falhas = saida.count("files weren't updated") + saida.count("Error")
    if falhas:
        avisos.append(f"exiftool relatou {falhas} problema(s): {saida[-300:].strip()}")
        _log.warning(avisos[-1])
    _log.info("Metadados gravados em %d de %d arquivo(s).", gravados, len(itens))
    return gravados, avisos


def conferir(caminho: Path) -> dict[str, str]:
    """Lê de volta os campos institucionais. Serve para auditar uma amostra."""
    if not disponivel():
        return {}
    try:
        proc = subprocess.run(
            [
                "exiftool", "-json", "-charset", "utf8",
                "-XMP-dc:Publisher", "-XMP-photoshop:Credit",
                "-XMP-xmpRights:WebStatement", "-EXIF:Copyright",
                "-XMP-dc:Title", "-EXIF:Artist", "-XMP-dc:Identifier",
                str(caminho),
            ],
            capture_output=True, text=True, timeout=60,
        )
        dados = json.loads(proc.stdout)
    except (OSError, subprocess.TimeoutExpired, json.JSONDecodeError, IndexError):
        return {}
    if not dados:
        return {}
    return {k: str(v) for k, v in dados[0].items() if k != "SourceFile"}
