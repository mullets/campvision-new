"""Renomear documentos de um projeto já no acervo: série e/ou ano no código.

Usado em dois casos:
- automático, no fim do recebimento: o projeto entrou com ano `0000` (a pasta
  não dizia) e a leitura achou o ano do projeto (moda das datas escritas,
  método §4.2) → `F026-P0228-0000-S01-D00001` vira `F026-P0228-1975-S01-D00001`;
- à mão, `vigia.py --reclassificar F026-P0228 --serie S01`: o lote caiu numa
  série errada (ex.: `99 - Não identificado`) e o arquivo muda de pasta.

Move o arquivo (mesmo volume, não copia), atualiza `catalogacao/` inteira, o
checkpoint, o cache de leitura, o preview, o identificador no XMP e anota tudo
no livro de registro como `refeito`. Nada é apagado.
"""

from __future__ import annotations

import logging
import os
import re
import shutil
import subprocess
from pathlib import Path

from . import estrutura

_log = logging.getLogger("cv2.renomear")


def novo_codigo(codigo: str, serie: str | None = None, ano: str | None = None,
                projeto: str | None = None) -> str:
    """`projeto` é o código novo do projeto inteiro (F022-P0001)."""
    achado = estrutura.CODIGO_DOCUMENTO.match(codigo)
    if not achado:
        return codigo
    fundo, proj, ano_atual, serie_atual, numero = achado.groups()
    prefixo = projeto.upper() if projeto else f"{fundo.upper()}-{proj.upper()}"
    return f"{prefixo}-{ano or ano_atual}-{(serie or serie_atual).upper()}-D{numero}"


def _trocar_texto(arquivo: Path, trocas: list[tuple[str, str]]) -> None:
    try:
        texto = arquivo.read_text(encoding="utf-8-sig" if arquivo.suffix == ".csv" else "utf-8")
    except (OSError, UnicodeDecodeError):
        return
    novo = texto
    for antigo, atual in trocas:
        novo = novo.replace(antigo, atual)
    if novo != texto:
        temporario = arquivo.with_name(f".{arquivo.name}.tmp")
        temporario.write_text(novo, encoding="utf-8-sig" if arquivo.suffix == ".csv" else "utf-8")
        os.replace(temporario, arquivo)


def renomear(pasta_projeto: Path, raiz_final: Path, pasta_estado: Path,
             trocas_codigo: dict[str, str], livro=None, motivo: str = "") -> dict[Path, Path]:
    """Aplica {código antigo: código novo}. Devolve {caminho antigo: caminho novo}."""
    trocas_codigo = {a: b for a, b in trocas_codigo.items() if a != b}
    if not trocas_codigo:
        return {}
    codigo_projeto = estrutura.codigo_da_pasta(pasta_projeto)
    movidos: dict[Path, Path] = {}
    for arquivo in sorted(pasta_projeto.rglob("*")):
        if not arquivo.is_file() or "catalogacao" in arquivo.relative_to(pasta_projeto).parts:
            continue
        novo = trocas_codigo.get(arquivo.stem)
        if not novo:
            continue
        serie = novo.split("-")[3]
        destino = estrutura.destino_documento(pasta_projeto, serie, novo, arquivo.suffix)
        if destino.exists():
            raise FileExistsError(f"{destino.name} já existe — renomeação abortada")
        destino.parent.mkdir(parents=True, exist_ok=True)
        os.replace(arquivo, destino)
        movidos[arquivo] = destino

    def rel(p: Path) -> str:
        try:
            return str(p.relative_to(raiz_final))
        except ValueError:
            return str(p)

    # Caminhos completos primeiro (incluem a pasta da série), depois os códigos.
    trocas = [(rel(a), rel(b)) for a, b in movidos.items()]
    trocas += sorted(trocas_codigo.items(), key=lambda t: -len(t[0]))
    for arquivo in [*(pasta_projeto / "catalogacao").rglob("*"),
                    pasta_projeto / "campvision2_checkpoint.jsonl", pasta_projeto / "status.json"]:
        if arquivo.is_file() and arquivo.suffix in (".json", ".jsonl", ".csv", ".txt"):
            _trocar_texto(arquivo, trocas)

    # Cache de leitura e preview, fora do projeto.
    for base in (pasta_estado / "leitura" / codigo_projeto,
                 raiz_final / "_campvision" / "preview" / codigo_projeto):
        for antigo, atual in trocas_codigo.items():
            origem = base / f"{antigo}.jpg"
            if origem.exists():
                os.replace(origem, base / f"{atual}.jpg")

    # Identificador dentro do arquivo (XMP/IPTC), num processo só.
    if movidos and shutil.which("exiftool"):
        linhas: list[str] = []
        for destino in movidos.values():
            linhas += ["-overwrite_original", f"-XMP-dc:Identifier={destino.stem}",
                       f"-XMP-photoshop:TransmissionReference={destino.stem}",
                       f"-IPTC:OriginalTransmissionReference={destino.stem[:32]}", str(destino), "-execute"]
        argfile = pasta_estado / "renomear.args"
        argfile.parent.mkdir(parents=True, exist_ok=True)
        argfile.write_text("\n".join(linhas) + "\n", encoding="utf-8")
        try:
            subprocess.run(["exiftool", "-@", str(argfile)], capture_output=True, timeout=900)
        except (OSError, subprocess.TimeoutExpired) as erro:
            _log.warning("Identificador XMP não atualizado: %s", erro)
        finally:
            argfile.unlink(missing_ok=True)

    if livro is not None:
        for antigo, atual in movidos.items():
            livro.anotar("refeito", codigo_projeto=codigo_projeto, codigo_documento=atual.stem,
                         arquivo_origem=rel(antigo), arquivo_destino=rel(atual),
                         nome_original=antigo.name, detalhe=motivo)
    _log.info("Projeto %s: %d arquivo(s) renomeado(s) (%s).", codigo_projeto, len(movidos), motivo)
    return movidos


def organizar_formatos(raiz_final: Path, pasta_estado: Path, livro=None) -> int:
    """Move os arquivos já no acervo para <série>/<FORMATO>/ (TIF/, JPG/...).

    Mesmo código, só muda a pasta; catalogacao/ e status são reescritos. Nada é apagado.
    """
    total = 0
    for fundo in sorted(raiz_final.iterdir()) if raiz_final.is_dir() else []:
        projetos = fundo / estrutura.PASTA_PROJETOS
        if not projetos.is_dir():
            continue
        for pasta in sorted(p for p in projetos.iterdir() if p.is_dir()):
            movidos: dict[Path, Path] = {}
            for arquivo in sorted(pasta.rglob("*")):
                if not arquivo.is_file() or "catalogacao" in arquivo.relative_to(pasta).parts:
                    continue
                achado = estrutura.CODIGO_DOCUMENTO.match(arquivo.stem)
                if not achado:
                    continue
                destino = estrutura.destino_documento(pasta, achado.group(4).upper(), arquivo.stem, arquivo.suffix)
                if destino == arquivo or destino.exists():
                    continue
                destino.parent.mkdir(parents=True, exist_ok=True)
                os.replace(arquivo, destino)
                movidos[arquivo] = destino
            if not movidos:
                continue

            def rel(p: Path) -> str:
                try:
                    return str(p.relative_to(raiz_final))
                except ValueError:
                    return str(p)

            trocas = [(rel(a), rel(b)) for a, b in movidos.items()]
            for arquivo in [*(pasta / "catalogacao").rglob("*"), pasta / "campvision2_checkpoint.jsonl",
                            pasta / "status.json"]:
                if arquivo.is_file() and arquivo.suffix in (".json", ".jsonl", ".csv", ".txt"):
                    _trocar_texto(arquivo, trocas)
            if livro is not None:
                codigo_projeto = estrutura.codigo_da_pasta(pasta)
                for antigo, atual in movidos.items():
                    livro.anotar("refeito", codigo_projeto=codigo_projeto, codigo_documento=atual.stem,
                                 arquivo_origem=rel(antigo), arquivo_destino=rel(atual),
                                 nome_original=antigo.name, detalhe="separado por formato")
            total += len(movidos)
    return total


def achar_projeto(raiz_final: Path, codigo: str) -> Path | None:
    for fundo in sorted(raiz_final.iterdir()) if raiz_final.is_dir() else []:
        projetos = fundo / estrutura.PASTA_PROJETOS
        if not projetos.is_dir():
            continue
        for pasta in projetos.iterdir():
            if pasta.is_dir() and estrutura.codigo_da_pasta(pasta) == codigo.upper():
                return pasta
    return None


def reclassificar(raiz_final: Path, pasta_estado: Path, codigo_projeto: str,
                  serie: str | None = None, de: str | None = None, ano: str | None = None,
                  livro=None, codigo_novo: str | None = None, nome_novo: str | None = None) -> int:
    """Troca série, ano e/ou o CÓDIGO e nome do projeto de todos os documentos."""
    pasta = achar_projeto(raiz_final, codigo_projeto)
    if pasta is None:
        raise FileNotFoundError(f"projeto {codigo_projeto} não encontrado em {raiz_final}")
    if serie and serie.upper() not in estrutura.SERIES:
        raise ValueError(f"série {serie} desconhecida (use {', '.join(estrutura.SERIES)})")
    codigo_antigo = estrutura.codigo_da_pasta(pasta)
    if codigo_novo:
        codigo_novo = codigo_novo.upper()
        if not estrutura.CODIGO_PROJETO.fullmatch(codigo_novo) or codigo_novo[:4] != codigo_antigo[:4]:
            raise ValueError(f"código novo {codigo_novo} inválido ou de outro fundo")
        if achar_projeto(raiz_final, codigo_novo):
            raise FileExistsError(f"já existe um projeto {codigo_novo}: junte à mão ou escolha outro")
    trocas: dict[str, str] = {}
    for arquivo in pasta.rglob("*"):
        achado = estrutura.CODIGO_DOCUMENTO.match(arquivo.stem)
        if not achado or "catalogacao" in arquivo.parts:
            continue
        if de and achado.group(4).upper() != de.upper():
            continue
        trocas[arquivo.stem] = novo_codigo(arquivo.stem, serie, ano, codigo_novo)
    motivo = (f"reclassificado: série {serie or '='} ano {ano or '='}"
              + (f" projeto {codigo_antigo}→{codigo_novo}" if codigo_novo else ""))
    n = len(renomear(pasta, raiz_final, pasta_estado, trocas, livro, motivo))
    if codigo_novo or nome_novo:
        nome = nome_novo or estrutura.nome_sem_codigo(pasta.name)
        nova = pasta.parent / estrutura.limpar(f"{codigo_novo or codigo_antigo} - {nome}", 110)
        if nova != pasta:
            os.replace(pasta, nova)

            def rel(p: Path) -> str:
                return str(p.relative_to(raiz_final))

            textos = [(rel(pasta), rel(nova))]
            if codigo_novo:
                textos.append((codigo_antigo, codigo_novo))
            for arquivo in [*(nova / "catalogacao").rglob("*"), nova / "status.json",
                            nova / "info_projeto.json", nova / "campvision2_checkpoint.jsonl"]:
                if arquivo.is_file() and arquivo.suffix in (".json", ".jsonl", ".csv", ".txt"):
                    _trocar_texto(arquivo, textos)
            if codigo_novo:
                for base in (pasta_estado / "leitura", raiz_final / "_campvision" / "preview"):
                    if (base / codigo_antigo).is_dir() and not (base / codigo_novo).exists():
                        os.replace(base / codigo_antigo, base / codigo_novo)
            readme = nova / "README.md"
            if readme.exists():
                _trocar_texto(readme, textos + [(estrutura.nome_sem_codigo(pasta.name), nome)])
    return n
