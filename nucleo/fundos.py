"""Tabela de autoridade dos fundos.

O código do fundo vem SEMPRE daqui, nunca do nome de uma pasta: várias pastas
antigas tinham código errado e chegaram a ser publicadas assim.

Fonte, em ordem:
  1. painel (`GET /api/estacoes/contexto`), guardado em cache local;
  2. o cache local da última vez que o painel respondeu;
  3. a tabela embutida abaixo (22/09/2026), para nunca ficar sem nada.

Fundo fora da tabela (F031 em diante, ainda sem código) não é inventado: o
lote para com erro até o fundo ser cadastrado no painel.
"""

from __future__ import annotations

import json
import logging
import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path

_log = logging.getLogger("cv2.fundos")

CREDITO_INSTITUICAO = "CAMP - Casa da Arquitetura Moderna Paulista"


@dataclass(frozen=True)
class Fundo:
    codigo: str
    sigla: str
    nome: str

    @property
    def pasta(self) -> str:
        """Nome da pasta em ACERVOS_CAMP: `F026 - SBU Sami Bussab`."""
        meio = f"{self.sigla} " if self.sigla else ""
        return f"{self.codigo} - {meio}{self.nome}".strip()

    @property
    def credito(self) -> str:
        return f"Acervo {self.nome}/{CREDITO_INSTITUICAO}"


EMBUTIDA: tuple[Fundo, ...] = (
    Fundo("F001", "ARM", "Arnaldo Martino"),
    Fundo("F002", "BSG", "Barretto Segnini"),
    Fundo("F003", "BMX", "Burle Marx"),
    Fundo("F004", "CBM", "Carlos Barjas Millan"),
    Fundo("F005", "CEN", "CENPLA"),
    Fundo("F006", "CMS", "Chu Ming Silveira"),
    Fundo("F007", "DLB", "David Libeskind"),
    Fundo("F008", "EDA", "Eduardo de Almeida"),
    Fundo("F009", "EOL", "Euclides Oliveira"),
    Fundo("F010", "FSJ", "Francisco Segnini Jr."),
    Fundo("F011", "HBR", "Hans Broos"),
    Fundo("F012", "JVA", "João Valente"),
    Fundo("F013", "JXA", "João Xavier"),
    Fundo("F014", "JBR", "Joaquim Barretto"),
    Fundo("F015", "JAB", "José Augusto Bellucci"),
    Fundo("F016", "JCB", "José Carlos Bellucci"),
    Fundo("F017", "JGU", "José Gugliotta"),
    Fundo("F018", "JOL", "José Olympio"),
    Fundo("F019", "LCL", "Lauro da Costa Lima"),
    Fundo("F020", "LCB", "Luiz Cesar Barillari"),
    Fundo("F021", "MAC", "Marcos Acayaba"),
    Fundo("F022", "MSL", "Marklen Siag Landa"),
    Fundo("F023", "OCG", "Oswaldo Corrêa Gonçalves"),
    Fundo("F024", "PMR", "Paulo Mendes da Rocha"),
    Fundo("F025", "", "Ruth Verde Zein"),
    Fundo("F026", "SBU", "Sami Bussab"),
    Fundo("F027", "", "Sandra Valente"),
    Fundo("F028", "", "Sidnei Magalhães"),
    Fundo("F029", "", "Sylvio Sawaya"),
    Fundo("F030", "KWA", "Fernando e Ana Karazawa"),
)


def _normalizar(texto: str) -> str:
    sem_acento = "".join(
        c for c in unicodedata.normalize("NFD", texto or "") if unicodedata.category(c) != "Mn"
    )
    return re.sub(r"[^a-z0-9]+", " ", sem_acento.lower()).strip()


class Tabela:
    def __init__(self, fundos: list[Fundo] | tuple[Fundo, ...], origem: str = "embutida") -> None:
        self.origem = origem
        self.por_codigo = {f.codigo: f for f in fundos}
        self.por_sigla = {f.sigla: f for f in fundos if f.sigla}

    def __len__(self) -> int:
        return len(self.por_codigo)

    def get(self, codigo: str) -> Fundo | None:
        return self.por_codigo.get((codigo or "").upper())

    def identificar(self, texto: str) -> Fundo | None:
        """Acha o fundo num nome de pasta: código F026, sigla SBU ou nome.

        Código explícito vence. Se o código não existe na tabela, devolve None
        (não cai para a sigla): pasta com código errado não pode ser
        reinterpretada em silêncio.
        """
        achado = re.search(r"\bF(\d{3})\b", texto or "", re.IGNORECASE)
        if achado:
            fundo = self.get(f"F{achado.group(1)}")
            # "F005 - Chu Ming" existiu (o certo é F006): código e nome
            # apontando para fundos diferentes é conflito, não palpite.
            resto = re.sub(r"\bF\d{3}\b", " ", texto, flags=re.IGNORECASE)
            palavras = {p for p in _normalizar(resto).split() if len(p) > 2}
            if fundo and fundo.sigla:
                palavras.discard(fundo.sigla.lower())
            if palavras and fundo is not None:
                proprias = set(_normalizar(fundo.nome).split())
                if not palavras & proprias:
                    for outro in self.por_codigo.values():
                        if outro is not fundo and palavras <= set(_normalizar(outro.nome).split()) | {outro.sigla.lower()}:
                            return None
            return fundo
        for pedaco in re.split(r"[\s_\-–.]+", texto or ""):
            if pedaco.isupper() and pedaco in self.por_sigla:
                return self.por_sigla[pedaco]
        alvo = _normalizar(texto)
        for fundo in self.por_codigo.values():
            if _normalizar(fundo.nome) and _normalizar(fundo.nome) in alvo:
                return fundo
        return None


def _de_json(dados) -> list[Fundo]:
    """Aceita a resposta do painel em formatos razoáveis."""
    if isinstance(dados, dict):
        dados = dados.get("fundos") or dados.get("dados") or []
    fundos: list[Fundo] = []
    for item in dados if isinstance(dados, list) else []:
        if not isinstance(item, dict):
            continue
        codigo = str(item.get("codigo") or item.get("fundo_codigo") or "").upper()
        if not re.match(r"^F\d{3}$", codigo):
            continue
        if item.get("ativo") is False:
            continue
        fundos.append(Fundo(
            codigo,
            str(item.get("sigla") or item.get("prefixo") or ""),
            str(item.get("nome") or item.get("titulo") or codigo),
        ))
    return fundos


def caminho_cache(pasta_estado: Path) -> Path:
    return pasta_estado / "fundos.json"


def carregar(pasta_estado: Path, painel=None) -> Tabela:
    """Tabela mais recente disponível. Nunca levanta exceção."""
    cache = caminho_cache(pasta_estado)
    if painel is not None:
        resposta = painel.contexto()
        fundos = _de_json(resposta) if resposta else []
        if fundos:
            try:
                cache.parent.mkdir(parents=True, exist_ok=True)
                cache.write_text(json.dumps(resposta, ensure_ascii=False, indent=1), encoding="utf-8")
            except OSError as erro:
                _log.warning("Não gravei o cache de fundos: %s", erro)
            return Tabela(fundos, "painel")
    if cache.exists():
        try:
            fundos = _de_json(json.loads(cache.read_text(encoding="utf-8")))
            if fundos:
                return Tabela(fundos, "cache")
        except (OSError, json.JSONDecodeError):
            pass
    return Tabela(EMBUTIDA, "embutida")
