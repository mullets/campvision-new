"""Estrutura do acervo final (ACERVOS_CAMP) e nomenclatura CAMP.

Segue o MODELO de pastas da CAMP:

    <Fundo>/
      01 - Projetos/
        <F002-P0002 - Nome>/
          01 - Desenhos e pranchas … 07 - Publicações, 99 - Não identificado
          README.md
      02 - Obras e documentação de obra … 09 - Publicações, 99 - Não identificado
      README.md

Nome de arquivo: F0xx-P000x-AAAA-S0x-DNNNNN.ext — AAAA é o ano do PROJETO
(0000 sem data), D é sequencial no projeto, contínuo entre séries e lotes.
"""

from __future__ import annotations

import re
import unicodedata
from datetime import date
from pathlib import Path

PASTA_PROJETOS = "01 - Projetos"

SERIES: dict[str, str] = {
    "S01": "01 - Desenhos e pranchas",
    "S02": "02 - Documentos textuais",
    "S03": "03 - Fotografias",
    "S04": "04 - Negativos",
    "S05": "05 - Slides",
    "S06": "06 - Materiais e especificações",
    "S07": "07 - Publicações",
    "S99": "99 - Não identificado",
}

PASTAS_DO_FUNDO = (
    PASTA_PROJETOS,
    "02 - Obras e documentação de obra",
    "03 - Fotografias",
    "04 - Negativos",
    "05 - Slides",
    "06 - Desenhos e pranchas",
    "07 - Documentos textuais",
    "08 - Materiais e especificações",
    "09 - Publicações",
    "99 - Não identificado",
)

# Palavra no caminho -> série. Ordem importa: a primeira que casar vence.
PALAVRAS_SERIE: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("S99", ("nao identificado",)),
    ("S04", ("negativ",)),
    ("S05", ("slide", "diapositiv", "cromo")),
    ("S03", ("fotograf", "foto")),
    ("S06", ("materia", "especifica")),
    ("S07", ("publica", "livro", "revista", "jornal")),
    ("S02", ("documento", "textua", "texto", "memorial", "carta")),
    ("S01", ("desenho", "prancha", "planta")),
)

CODIGO_PROJETO = re.compile(r"\b(F\d{3})-(P\d{4})\b", re.IGNORECASE)
CODIGO_DOCUMENTO = re.compile(r"^(F\d{3})-(P\d{4})-(\d{4})-(S\d{2})-D(\d{5})$", re.IGNORECASE)


def normalizar(texto: str) -> str:
    sem_acento = "".join(
        c for c in unicodedata.normalize("NFD", texto or "") if unicodedata.category(c) != "Mn"
    )
    return re.sub(r"[^a-z0-9]+", " ", sem_acento.lower()).strip()


def serie_do_texto(texto: str) -> str:
    """Série pelo nome de uma pasta (`03 - Fotografias`, `Negativos`, `S01`)."""
    achado = re.search(r"\bS(0[1-7]|99)\b", texto or "")
    if achado:
        return f"S{achado.group(1)}"
    alvo = normalizar(texto)
    for serie, palavras in PALAVRAS_SERIE:
        if any(p in alvo for p in palavras):
            return serie
    return ""


def limpar(texto: str, limite: int = 90) -> str:
    limpo = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "", (texto or "").strip())
    limpo = re.sub(r"\s+", " ", limpo).strip(" .")
    return limpo[:limite].strip() or "Sem titulo"


def nome_sem_codigo(nome_pasta: str) -> str:
    """`F002-P0002 - Residência X` -> `Residência X`."""
    return re.sub(r"^\s*(F\d{3}-)?P\d{4}\s*[-–]\s*", "", nome_pasta, flags=re.IGNORECASE).strip()


def readme_fundo(fundo) -> str:
    return (
        f"# {fundo.pasta}\n\n"
        f"Crédito: {fundo.credito}\n\n"
        "Pastas criadas pelo CAMP Vision 2 seguindo o MODELO da CAMP.\n"
        "Projetos ficam em `01 - Projetos/`, um por pasta, com o código do painel.\n"
    )


def readme_projeto(codigo: str, nome: str, fundo) -> str:
    return (
        f"# {codigo} - {nome}\n\n"
        f"Fundo: {fundo.pasta}\n"
        f"Crédito: {fundo.credito}\n"
        f"Criado em: {date.today():%d/%m/%Y} pelo CAMP Vision 2\n\n"
        "Arquivos nomeados F0xx-P000x-AAAA-S0x-DNNNNN. Catalogação, contatos e erros\n"
        "em `catalogacao/`. Histórico de cada arquivo no livro de registro\n"
        "(`ACERVOS_CAMP/_campvision/registro/`).\n"
    )


def garantir_fundo(raiz: Path, fundo) -> Path:
    """Pasta do fundo (reaproveita a que começa com o código) com o MODELO."""
    pasta = None
    if raiz.is_dir():
        for filho in sorted(raiz.iterdir()):
            if filho.is_dir() and re.match(rf"^{fundo.codigo}\b", filho.name, re.IGNORECASE):
                pasta = filho
                break
    pasta = pasta or raiz / limpar(fundo.pasta)
    for nome in PASTAS_DO_FUNDO:
        (pasta / nome).mkdir(parents=True, exist_ok=True)
    leia = pasta / "README.md"
    if not leia.exists():
        leia.write_text(readme_fundo(fundo), encoding="utf-8")
    return pasta


def achar_projeto(pasta_fundo: Path, codigo: str = "", nome: str = "") -> Path | None:
    """Projeto existente em `01 - Projetos/`, pelo código ou pelo nome."""
    base = pasta_fundo / PASTA_PROJETOS
    if not base.is_dir():
        return None
    alvo = normalizar(nome)
    numero = codigo.split("-")[-1].upper() if codigo else ""
    for filho in sorted(base.iterdir()):
        if not filho.is_dir() or filho.name.startswith((".", "MODELO")):
            continue
        achado = CODIGO_PROJETO.search(filho.name)
        if codigo and achado and f"{achado.group(1)}-{achado.group(2)}".upper() == codigo.upper():
            return filho
        if numero and re.match(rf"^{numero}\b", filho.name, re.IGNORECASE):
            return filho
        if alvo and normalizar(nome_sem_codigo(filho.name)) == alvo:
            return filho
    return None


def codigo_da_pasta(pasta: Path) -> str:
    achado = CODIGO_PROJETO.search(pasta.name)
    return f"{achado.group(1).upper()}-{achado.group(2).upper()}" if achado else ""


def garantir_projeto(pasta_fundo: Path, codigo: str, nome: str, fundo) -> Path:
    pasta = achar_projeto(pasta_fundo, codigo, nome) or (
        pasta_fundo / PASTA_PROJETOS / limpar(f"{codigo} - {nome}", 110)
    )
    for nome_serie in SERIES.values():
        (pasta / nome_serie).mkdir(parents=True, exist_ok=True)
    leia = pasta / "README.md"
    if not leia.exists():
        leia.write_text(readme_projeto(codigo, nome, fundo), encoding="utf-8")
    return pasta


def proximo_documento(pasta_projeto: Path) -> int:
    """Próximo D livre no projeto, olhando todos os arquivos já nomeados."""
    maior = 0
    for arquivo in pasta_projeto.rglob("*"):
        achado = CODIGO_DOCUMENTO.match(arquivo.stem)
        if achado:
            maior = max(maior, int(achado.group(5)))
    return maior + 1


def codigo_documento(codigo_projeto: str, ano: str, serie: str, numero: int) -> str:
    ano = ano if re.fullmatch(r"\d{4}", ano or "") else "0000"
    return f"{codigo_projeto.upper()}-{ano}-{serie}-D{numero:05d}"


def numeros_p(raiz_final: Path, fundo_codigo: str) -> tuple[int, set[str]]:
    """(maior número P, códigos em uso) do fundo no acervo — CV-27: nunca reusar."""
    usados: set[str] = set()
    fundo_codigo = fundo_codigo.upper()
    for pasta_fundo in raiz_final.glob(f"{fundo_codigo}*"):
        projetos = pasta_fundo / PASTA_PROJETOS
        if not projetos.is_dir():
            continue
        for pasta in projetos.iterdir():
            codigo = codigo_da_pasta(pasta) if pasta.is_dir() else ""
            if codigo.startswith(fundo_codigo + "-"):
                usados.add(codigo)
    maior = max((int(c.split("-P")[1]) for c in usados), default=0)
    return maior, usados
