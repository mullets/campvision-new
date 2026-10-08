"""Passada 3 do método de leitura, em CÓDIGO: consenso por grupo. Sem modelo.

Especificação: docs/metodo-de-leitura.md §4.1 (consenso) e §4.2 (ano). Ticket "[Método §4.1] Consenso por grupo em CÓDIGO".

Regras (as da especificação, sem acréscimo):
- valores = leituras NÃO-nulas do campo no grupo; vazio = None (nunca inventar);
- consenso = a MODA do valor normalizado (minúsculo, sem acento, sem pontuação). Moda, não média;
- valor do grupo = a grafia mais completa DENTRO do grupo vencedor;
- toda leitura fora do consenso é marcada OUTLIER e o valor lido original é guardado: NUNCA se sobrescreve em silêncio;
- dúvida vai junto do dado: a ressalva fica anexada à folha ("conferir no original antes de publicar").

Dois campos são calculados mas NÃO sobrescrevem a leitura de cada folha (PRESERVAM_A_LEITURA):
- `ano`: cada prancha mantém o seu ano; o do grupo (a moda) vai em `ano_do_projeto` (§4.2). O caso-guia: as pranchas 0103, 0111 e
  0112 do McDonald's foram lidas como "85", e as outras da série são de 1983. Leitura individual confiante e ERRADA que só o grupo revela.
- `arquiteto`: o consenso é exatamente o que esconderia uma prancha intrusa num grupo de 30 (§4.4). A folha de outro arquiteto
  continua com o que foi lido, marcada outlier. O bloqueio de publicação é de outro ticket (autoria divergente).

NÃO faz (outros tickets): quarentena e canônica congelada entre lotes (vocabulário persistente), tabela de autoridade, detecção de
folha de OUTRO projeto dentro do grupo.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field

from .esquema import Leitura
from .grupos import normalizar

CAMPOS: tuple[str, ...] = ("projeto", "cliente", "arquiteto", "escritorio", "endereco", "cidade", "uf", "ano")
PRESERVAM_A_LEITURA: frozenset[str] = frozenset({"ano", "arquiteto"})


@dataclass
class ResultadoCampo:
    campo: str
    valor: str | None                 # grafia mais completa dentro do vencedor; None = nenhuma folha leu o campo
    votos: int = 0                    # leituras que concordam com o vencedor
    leituras: int = 0                 # leituras não-nulas do campo no grupo
    empate: bool = False              # a moda empatou (desempatada pela soma das confianças)
    outliers: list[tuple[str, str]] = field(default_factory=list)   # [(arquivo, valor lido)]


def consenso_de_campo(campo: str, votos: list[tuple[str, str, float]]) -> ResultadoCampo:
    """votos = [(arquivo, valor lido, confiança)]. Determinístico: o desempate final é a ordem recebida."""
    grupos: dict[str, list[tuple[str, str, float]]] = {}
    for arquivo, valor, conf in votos:
        v = (valor or "").strip()
        n = normalizar(v)
        if n:
            grupos.setdefault(n, []).append((arquivo, v, float(conf or 0.0)))
    if not grupos:
        return ResultadoCampo(campo, None)
    ordenados = sorted(grupos.items(), key=lambda it: (len(it[1]), sum(c for _, _, c in it[1])), reverse=True)
    n_venc, venc = ordenados[0]
    empate = len(ordenados) > 1 and len(ordenados[1][1]) == len(venc)
    por_grafia: dict[str, list[float]] = defaultdict(lambda: [0, 0.0])
    for _, v, c in venc:
        por_grafia[v][0] += 1
        por_grafia[v][1] += c
    melhor = max(por_grafia.items(), key=lambda kv: (len(kv[0]), kv[1][0], kv[1][1]))[0]   # a mais completa; depois a mais frequente
    outliers = [(a, v) for n, lst in grupos.items() if n != n_venc for a, v, _ in lst]
    return ResultadoCampo(campo, melhor, len(venc), sum(len(l) for l in grupos.values()), empate, outliers)


def consenso_do_grupo(leituras: list[Leitura], campos: tuple[str, ...] = CAMPOS) -> dict[str, ResultadoCampo]:
    """Calcula o consenso de cada campo SEM alterar nenhuma leitura (chame `aplicar` depois)."""
    return {c: consenso_de_campo(c, [(l.arquivo, l.valores.get(c, ""), l.confiancas.get(c, 0.0)) for l in leituras]) for c in campos}


def aplicar(leituras: list[Leitura], resultados: dict[str, ResultadoCampo], preservam: frozenset[str] = PRESERVAM_A_LEITURA) -> None:
    """Escreve o consenso nas folhas, guardando o lido, marcando outliers e anexando as ressalvas. Idempotente."""
    melhor_conf = {c: max((l.confiancas.get(c, 0.0) for l in leituras), default=0.0) for c in resultados}
    for l in leituras:
        if not l.lidos_originais:
            l.lidos_originais = dict(l.valores)      # o que foi LIDO de verdade, antes de qualquer consenso (só na primeira vez)
        for campo, r in resultados.items():
            if r.valor is None:
                continue
            lido = (l.lidos_originais.get(campo) or "").strip()
            fora = bool(lido) and normalizar(lido) != normalizar(r.valor)
            avisos = []
            if fora:
                l.outliers[campo] = lido
                avisos.append(f"{campo}: lido '{lido}', consenso do grupo '{r.valor}' ({r.votos} de {r.leituras} leituras); "
                              "conferir no original antes de publicar")
            elif r.empate and lido:
                avisos.append(f"{campo}: empate no grupo (consenso '{r.valor}' com {r.votos} de {r.leituras} leituras); conferir no original")
            for a in avisos:
                if a not in l.ressalvas:
                    l.ressalvas.append(a)
            if campo == "ano":
                l.ano_do_projeto = r.valor
            elif campo not in preservam:
                l.valores[campo] = r.valor
                l.confiancas[campo] = max(l.confiancas.get(campo, 0.0), melhor_conf[campo])
