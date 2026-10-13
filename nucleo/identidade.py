"""Identificação do lote pelo CARIMBO, antes de existir código (auditoria F026, 13/10).

A pasta da estação NÃO diz qual é o projeto: o número dela (P0001, T100) é só
pista e o nome dela pode juntar várias obras. Depois da leitura e ANTES de
usar ou reservar um código:

1. `analisar` agrupa as folhas do lote pelo que está escrito no carimbo:
   obra + local (endereço) e identificadores (contribuinte, alvará). Duas
   numerações de folha ou dois proprietários no mesmo endereço NÃO separam
   projetos; obra diferente em endereço diferente separa. Capas, envelopes e
   tiras não são pranchas. Folha sem nada legível fica em "sem identificação".
2. `casar_com_base` procura cada grupo nos projetos do fundo (o painel manda a
   lista em /api/estacoes/contexto?fundo=): um candidato forte → esse código;
   dois ou mais → PERGUNTAR (decisão no painel), nunca escolher; nenhum → só
   então reservar um P novo.

Nada aqui é de um acervo específico: são regras sobre o que está escrito.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from difflib import SequenceMatcher

from .grupos import normalizar

# Palavras que não distinguem uma obra de outra (nem um lugar de outro).
GENERICAS = {
    "de", "da", "do", "das", "dos", "e", "a", "o", "as", "os", "em", "no", "na", "nos", "nas", "para",
    "por", "com", "sem", "ao", "aos", "um", "uma", "sr", "sra", "srs", "dona", "s", "a", "ltda", "sa", "cia",
    "residencia", "residencial", "casa", "edificio", "predio", "projeto", "obra", "conjunto", "apartamento",
    "apartamentos", "aptos", "apto", "garagem", "garagens", "sobrado", "unidade", "estudo", "preliminar",
    "executivo", "anteprojeto", "planta", "folha", "prancha", "arquitetura", "construcao", "reforma",
    "ampliacao", "proprietario", "cliente", "local", "endereco", "desenho", "programa",
    "ed", "edif", "res", "resid", "conj", "apts", "soc", "prop",
}
LUGAR_GENERICO = {
    "rua", "avenida", "alameda", "travessa", "praca", "estrada", "rodovia", "esquina", "largo", "vila",
    "jardim", "bairro", "parque", "prolongamento", "sao", "paulo", "sp", "brasil", "capital", "lote", "quadra",
    "doutor", "professor", "coronel", "marechal", "general", "capitao", "presidente", "santa", "santo",
}
CONTRIBUINTE = re.compile(r"contribuinte[^0-9]{0,25}(\d{3}[\s.]?\d{3}[\s.]?\d{4}(?:[\s.-]?\d)?)", re.IGNORECASE)
ALVARA = re.compile(r"alvar[aá][^0-9]{0,25}(\d[\d.]{2,}(?:/\d{2,4})?)", re.IGNORECASE)
CAPA = re.compile(r"\b(capa|envelope|contracapa|pasta|invólucro|involucro|rótulo|rotulo|etiqueta)\b", re.IGNORECASE)
SO_CODIGO = re.compile(r"^\W*[A-Z]{1,2}\s?-?\s?\d{2,4}\W*$", re.IGNORECASE)
MINIMO_GRUPO = 2   # um grupo de 1 folha sem projeto na base não vira projeto sozinho


def _tokens(texto: str, extra: set[str] = frozenset()) -> list[str]:
    return [t for t in normalizar(texto).split()
            if t not in GENERICAS and t not in extra and len(t) > 1 and not t.isdigit()]


def tokens_obra(texto: str) -> list[str]:
    # Nome de rua/bairro e santo ("Sto Amaro", "Alameda Santos") não distinguem a OBRA.
    return _tokens(texto, LUGAR_GENERICO)


def tokens_lugar(texto: str) -> list[str]:
    return _tokens(texto, LUGAR_GENERICO)


def _igual(a: str, b: str) -> bool:
    if a == b:
        return True
    return len(a) >= 5 and len(b) >= 5 and SequenceMatcher(None, a, b).ratio() >= 0.8


def comuns(a: list[str], b: list[str]) -> int:
    return sum(1 for t in set(a) if any(_igual(t, u) for u in b))


def _parecidos(a: list[str], b: list[str], limiar: float) -> bool:
    if not a or not b:
        return False
    return comuns(a, b) / min(len(set(a)), len(set(b))) >= limiar


def lido(leitura, campo: str) -> str:
    """O que foi LIDO na folha. O que veio do nome da pasta não conta."""
    if campo in (leitura.campos_da_pasta or []):
        return ""
    return ((leitura.lidos_originais or {}).get(campo) or leitura.valores.get(campo) or "").strip()


def identificadores(leitura) -> set[str]:
    """Contribuinte e alvará escritos na folha: a mesma obra em pastas diferentes."""
    texto = " ".join([leitura.transcricao_integral or "", *(str(v) for v in leitura.valores.values())])
    saida = set()
    for m in CONTRIBUINTE.finditer(texto):
        saida.add("contribuinte " + re.sub(r"\D", "", m.group(1)))
    for m in ALVARA.finditer(texto):
        numero = re.sub(r"\D", "", m.group(1))
        if len(numero) >= 3:
            saida.add("alvara " + numero)
    return saida


def carimbo(leitura) -> dict:
    """O que o carimbo diz, campo a campo, como lido (vai ao painel: ticket T1)."""
    ids = sorted(identificadores(leitura))
    return {
        "obra": lido(leitura, "projeto"), "local": lido(leitura, "endereco"),
        "cidade": lido(leitura, "cidade"), "cliente": lido(leitura, "cliente"),
        "escritorio": lido(leitura, "escritorio"), "arquiteto": lido(leitura, "arquiteto"),
        "data": lido(leitura, "data"), "codigo_unidade": lido(leitura, "codigo_unidade"),
        "contribuinte": next((i.split(" ", 1)[1] for i in ids if i.startswith("contribuinte")), ""),
        "alvara": next((i.split(" ", 1)[1] for i in ids if i.startswith("alvara")), ""),
    }


def motivo_capa(leitura, proporcao: float = 0.0) -> str:
    """Capa, envelope ou tira: não é prancha (T6)."""
    if leitura.modo != "prancha":
        return ""
    titulo = " ".join(filter(None, [leitura.valores.get("titulo_prancha", ""), leitura.valores.get("tipo", ""),
                                    *(leitura.observacoes_leitura or [])]))
    if CAPA.search(titulo):
        return f"capa/envelope ('{CAPA.search(titulo).group(1)}')"
    if proporcao >= 3.0 and not leitura.carimbo_encontrado:
        return f"tira estreita (proporção {proporcao:.1f}:1), sem carimbo"
    texto = (leitura.transcricao_integral or "").strip()
    if not leitura.carimbo_encontrado and texto and SO_CODIGO.match(texto):
        return f"capa com o código escrito à mão ('{texto.strip()[:20]}')"
    return ""


@dataclass
class Grupo:
    obra: str = ""
    local: str = ""
    cliente: str = ""
    folhas: list[str] = field(default_factory=list)
    ids: set[str] = field(default_factory=set)
    escritorios: dict[str, list[str]] = field(default_factory=dict)
    anos: dict[str, list[str]] = field(default_factory=dict)
    codigo: str = ""            # decidido (base ou reserva)
    resolucao: dict = field(default_factory=dict)

    @property
    def titulo(self) -> str:
        return ", ".join(p for p in (self.obra, self.local) if p) or self.cliente or "(sem título lido)"

    @property
    def chave(self) -> str:
        base = "|".join([normalizar(self.obra), normalizar(self.local), *sorted(self.ids)])
        return hashlib.sha1(base.encode()).hexdigest()[:12]

    def aceita(self, obra: list[str], lugar: list[str], ids: set[str]) -> bool:
        if ids and ids & self.ids:
            return True
        meu_obra, meu_lugar = tokens_obra(self.obra), tokens_lugar(self.local)
        lugar_ok = not lugar or not meu_lugar or _parecidos(lugar, meu_lugar, 0.5)
        if obra and meu_obra:
            return _parecidos(obra, meu_obra, 0.6) and lugar_ok
        # Obra genérica ("Edifício de apartamentos") ou ilegível: decide o endereço.
        return bool(lugar and meu_lugar) and _parecidos(lugar, meu_lugar, 0.5)

    def como_dict(self) -> dict:
        return {"chave": self.chave, "titulo": self.titulo, "obra": self.obra, "local": self.local,
                "cliente": self.cliente, "folhas": list(self.folhas), "identificadores": sorted(self.ids),
                "escritorios": {k: list(v) for k, v in self.escritorios.items()},
                "codigo": self.codigo, "resolucao": self.resolucao}


@dataclass
class Analise:
    grupos: list[Grupo] = field(default_factory=list)
    sem_identificacao: list[str] = field(default_factory=list)
    capas: dict[str, str] = field(default_factory=dict)
    mesmo_endereco: list[list[int]] = field(default_factory=list)   # índices de grupos: obras diferentes, mesmo lugar
    fases: list[dict] = field(default_factory=list)                # subgrupos dentro de um grupo (escritório × década)

    @property
    def principal(self) -> Grupo | None:
        return self.grupos[0] if self.grupos else None

    def grupo_de(self, codigo_doc: str) -> Grupo | None:
        return next((g for g in self.grupos if codigo_doc in g.folhas), None)

    def como_dict(self) -> dict:
        return {"grupos": [g.como_dict() for g in self.grupos], "sem_identificacao": list(self.sem_identificacao),
                "capas": dict(self.capas), "mesmo_endereco": [list(m) for m in self.mesmo_endereco],
                "fases": list(self.fases)}


def _ano(texto: str) -> str:
    m = re.search(r"\b(1[89]\d{2}|20\d{2})\b", texto or "")
    if m:
        return m.group(1)
    m = re.search(r"\b\d{1,2}[./-]\d{1,2}[./-](\d{2})\b|\b\d{1,2}[./-](\d{2})\b", texto or "")
    return f"19{next(g for g in m.groups() if g)}" if m else ""


def analisar(leituras: dict, proporcoes: dict | None = None) -> Analise:
    """Agrupa as folhas do lote pelo carimbo. Não usa a pasta."""
    proporcoes = proporcoes or {}
    analise = Analise()
    for codigo in sorted(leituras):
        l = leituras[codigo]
        capa = motivo_capa(l, float(proporcoes.get(codigo) or 0.0))
        if capa:
            analise.capas[codigo] = capa
            continue
        obra_txt, local_txt = lido(l, "projeto"), lido(l, "endereco")
        if not local_txt and lido(l, "cidade") and obra_txt:
            local_txt = ""
        obra, lugar, ids = tokens_obra(obra_txt), tokens_lugar(local_txt), identificadores(l)
        if not obra and not lugar and not ids:
            analise.sem_identificacao.append(codigo)
            continue
        grupo = next((g for g in analise.grupos if g.aceita(obra, lugar, ids)), None)
        if grupo is None:
            grupo = Grupo()
            analise.grupos.append(grupo)
        grupo.folhas.append(codigo)
        grupo.ids |= ids
        if obra_txt and (not grupo.obra or not tokens_obra(grupo.obra)) and obra:
            grupo.obra = obra_txt
        elif obra_txt and not grupo.obra:
            grupo.obra = obra_txt
        if local_txt and not grupo.local:
            grupo.local = local_txt
        cliente = lido(l, "cliente")
        if cliente and not grupo.cliente:
            grupo.cliente = cliente
        escritorio = normalizar(lido(l, "escritorio"))
        if escritorio:
            grupo.escritorios.setdefault(escritorio, []).append(codigo)
            ano = _ano(lido(l, "data"))
            if ano:
                grupo.anos.setdefault(escritorio, []).append(ano)

    # Grupos que se juntam depois (um identificador apareceu só numa folha do meio).
    juntou = True
    while juntou:
        juntou = False
        for i, a in enumerate(analise.grupos):
            for b in analise.grupos[i + 1:]:
                if a.ids & b.ids:
                    a.folhas += b.folhas
                    a.ids |= b.ids
                    for k, v in b.escritorios.items():
                        a.escritorios.setdefault(k, []).extend(v)
                    analise.grupos.remove(b)
                    juntou = True
                    break
            if juntou:
                break
    analise.grupos.sort(key=lambda g: (-len(g.folhas), g.folhas[0]))

    # Obras diferentes no MESMO endereço (cliente igual ou não dito): perguntar, não decidir.
    vistos: set[int] = set()
    for i, a in enumerate(analise.grupos):
        if i in vistos or not tokens_lugar(a.local):
            continue
        juntos = [i]
        for j in range(i + 1, len(analise.grupos)):
            b = analise.grupos[j]
            if j in vistos or not _parecidos(tokens_lugar(a.local), tokens_lugar(b.local), 0.5):
                continue
            ca, cb = tokens_obra(a.cliente), tokens_obra(b.cliente)
            if ca and cb and not _parecidos(ca, cb, 0.5):
                continue
            juntos.append(j)
        if len(juntos) > 1:
            vistos.update(juntos)
            analise.mesmo_endereco.append(juntos)

    # Dentro de um grupo: escritórios diferentes em décadas diferentes = fases ou obras? Perguntar.
    for g in analise.grupos:
        fortes = {e: f for e, f in g.escritorios.items() if len(f) >= 2}
        anos = [int(a) for e in fortes for a in g.anos.get(e, [])]
        if len(fortes) >= 2 and anos and max(anos) - min(anos) >= 5:
            analise.fases.append({"grupo": g.chave, "titulo": g.titulo,
                                  "subgrupos": {e: f for e, f in fortes.items()}})
    return analise


# ================================================================ base do fundo

def _partes_base(titulo: str) -> tuple[list[str], list[str]]:
    """'Comurb, Rua Pavan - Vila Sta. Catarina' → (['comurb'], ['pavan', 'catarina'])."""
    nome, _, lugar = (titulo or "").partition(",")
    nome = re.sub(r"\(.*?\)", " ", nome)
    return tokens_obra(nome), tokens_lugar(lugar)


def casar_com_base(grupo: Grupo, base: list[dict], pista_pasta: str = "") -> dict:
    """{'forte': {...}} | {'candidatos': [...]} | {} (nenhum). `base` = projetos do fundo."""
    obra = tokens_obra(grupo.obra) + tokens_obra(grupo.cliente) + tokens_obra(pista_pasta)
    lugar_lido = tokens_lugar(grupo.local)
    lugar = lugar_lido + tokens_lugar(pista_pasta)
    achados = []
    for p in base or []:
        titulo = str(p.get("projeto") or p.get("titulo") or "")
        codigo = str(p.get("codigo") or "")
        if not titulo or not codigo:
            continue
        nome_b, lugar_b = _partes_base(titulo)
        acerto_nome = comuns(nome_b, obra) if nome_b else 0
        acerto_lugar = comuns(lugar_b, lugar) if lugar_b else 0
        nome_ok = bool(nome_b) and acerto_nome / len(set(nome_b)) >= 0.5
        # Base sem nome distintivo ("Ed. de Apartamentos, Rua Mangueira"): o lugar decide.
        if not nome_ok and not (not nome_b and acerto_lugar):
            continue
        motivos = []
        if nome_ok:
            motivos.append(f"nome '{titulo.split(',')[0].strip()}' no carimbo/pasta")
        if acerto_lugar:
            motivos.append("mesmo lugar")
        achados.append({"codigo": codigo, "titulo": titulo, "nome": acerto_nome, "lugar": acerto_lugar,
                        "tem_lugar": bool(lugar_b), "motivos": motivos})
    if not achados:
        return {}
    achados.sort(key=lambda a: (-(a["nome"] + a["lugar"]), a["codigo"]))
    if len(achados) == 1:
        a = achados[0]
        if a["tem_lugar"] and lugar_lido and not a["lugar"]:
            return {"candidatos": achados}   # o nome bate, o lugar escrito é outro: perguntar
        return {"forte": a}
    com_lugar = [a for a in achados if a["lugar"]]
    if len(com_lugar) == 1 and all(a["tem_lugar"] for a in achados if a is not com_lugar[0]):
        return {"forte": com_lugar[0]}   # Sabesp Vila Alpina × Vila Formosa: o lugar escrito decide
    return {"candidatos": com_lugar if len(com_lugar) >= 2 else achados}


def pista_da_pasta(nome_pasta: str) -> str:
    """'P0002 - Residência Miguel Burihan - 1975 - 1977' → 'Residência Miguel Burihan'."""
    texto = re.sub(r"^\s*(F\d{3}-)?[A-Z]{1,2}\s?\d{2,4}\s*[-_–]?\s*", "", nome_pasta or "", flags=re.IGNORECASE)
    texto = re.sub(r"\b(1[89]\d{2}|20\d{2})\b", " ", texto)
    return re.sub(r"[\s_\-–]+", " ", texto).strip()
