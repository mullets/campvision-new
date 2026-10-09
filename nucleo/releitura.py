"""Releitura de um projeto já no acervo, a pedido (painel ou linha de comando).

O fluxo continua de mão única: o painel REGISTRA o pedido e o CV2 PERGUNTA
(GET /api/estacoes/pedidos-releitura), igual ao heartbeat. O CV2 relê, grava o
resultado AO LADO do que existe e responde (POST .../{id}/concluido).

Regras:
- a releitura NUNCA sobrescreve catalogacao/leituras.json nem o que foi
  revisado: o resultado vai para catalogacao/releituras/<quando>/ com
  `leituras.json` e `comparacao.json` (campo a campo: antes × agora). Quem
  escolhe é o revisor;
- escopo: "projeto" (todas as folhas), "documentos" (lista de códigos) ou
  "vazios" (folhas com erro, sem carimbo, ou com campo do modelo vazio);
- sem a imagem de leitura em cache, ela é refeita a partir do master no acervo
  (mesmo preparo: orientação pelo texto, 2000 px).
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path

from . import formatos, planilha as mod_planilha, preparo as mod_preparo, renomear as mod_renomear
from .esquema import CAMPOS_DO_MODELO, Leitura

_log = logging.getLogger("cv2.releitura")
ESCOPOS = ("projeto", "documentos", "vazios")


def _agora() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _vazia(l: Leitura) -> bool:
    if l.erro or (l.modo == "prancha" and not l.carimbo_encontrado):
        return True
    if l.modo != "prancha":
        return not l.valores.get("titulo_prancha")
    return any(not l.valores.get(c.nome) for c in CAMPOS_DO_MODELO
               if c.nome in ("projeto", "arquiteto", "data", "titulo_prancha"))


def _masters(pasta_projeto: Path) -> dict[str, list[Path]]:
    from . import estrutura

    por_codigo: dict[str, list[Path]] = {}
    for arquivo in pasta_projeto.rglob("*"):
        if arquivo.is_file() and "catalogacao" not in arquivo.relative_to(pasta_projeto).parts \
                and estrutura.CODIGO_DOCUMENTO.match(arquivo.stem) and formatos.suportado(arquivo):
            por_codigo.setdefault(arquivo.stem, []).append(arquivo)
    return por_codigo


def alvos(pasta_projeto: Path, escopo: str, documentos: list[str] | None = None) -> list[str]:
    if escopo not in ESCOPOS:
        raise ValueError(f"escopo deve ser {', '.join(ESCOPOS)}")
    todos = sorted(_masters(pasta_projeto))
    if escopo == "projeto":
        return todos
    if escopo == "documentos":
        pedidos = {d.upper() for d in documentos or []}
        return [c for c in todos if c.upper() in pedidos]
    atuais = {Path(l.arquivo).stem: l for l in
              mod_planilha.ler_json(pasta_projeto / "catalogacao" / "leituras.json")}
    return [c for c in todos if c not in atuais or _vazia(atuais[c])]


def _imagem(codigo_doc: str, versoes: list[Path], cache: Path, pasta_estado: Path) -> Path | None:
    jpg = cache / f"{codigo_doc}.jpg"
    if jpg.exists():
        return jpg
    melhor = formatos.Documento(codigo_doc, versoes).para_ler
    bruta = formatos.imagem_de_leitura(melhor, pasta_estado / "leitura_bruta" / "releitura" / f"{codigo_doc}.jpg")
    if bruta is None:
        return None
    mod_preparo.preparar(bruta, melhor, jpg, None)
    bruta.unlink(missing_ok=True)
    return jpg


def reler(raiz_final: Path, pasta_estado: Path, config, cliente, codigo_projeto: str,
          escopo: str = "vazios", documentos: list[str] | None = None, motivo: str = "",
          pedido_por: str = "", livro=None, leitor=None) -> dict:
    """Relê e grava ao lado. Devolve o resumo (vai na resposta ao painel)."""
    pasta = mod_renomear.achar_projeto(raiz_final, codigo_projeto)
    if pasta is None:
        raise FileNotFoundError(f"projeto {codigo_projeto} não encontrado no acervo")
    codigo = pasta.name.split(" - ")[0]
    escolhidos = alvos(pasta, escopo, documentos)
    masters = _masters(pasta)
    cache = pasta_estado / "leitura" / codigo
    cache.mkdir(parents=True, exist_ok=True)
    if leitor is None:
        from .visao import LeitorDeCarimbo

        leitor = LeitorDeCarimbo(config, cliente)

    atuais = {Path(l.arquivo).stem: l for l in mod_planilha.ler_json(pasta / "catalogacao" / "leituras.json")}
    novas: list[Leitura] = []
    comparacao: dict[str, dict] = {}
    t_in = t_out = 0
    falhas: list[str] = []
    for doc in escolhidos:
        jpg = _imagem(doc, masters.get(doc, []), cache, pasta_estado)
        if jpg is None:
            falhas.append(f"{doc}: não abri o master")
            continue
        nova = leitor.ler(jpg)
        nova.arquivo = jpg.name
        t_in += nova.tokens_entrada
        t_out += nova.tokens_saida
        if nova.erro:
            falhas.append(f"{doc}: {nova.erro[:120]}")
        novas.append(nova)
        antes = atuais.get(doc)
        mudou = {}
        for campo in sorted(set(nova.valores) | set(antes.valores if antes else {})):
            a = (antes.valores.get(campo, "") if antes else "") or ""
            b = nova.valores.get(campo, "") or ""
            if a != b:
                mudou[campo] = {"antes": a, "agora": b,
                                "confianca_agora": round(nova.confiancas.get(campo, 0.0), 2)}
        comparacao[doc] = {"mudou": mudou, "preencheu": sorted(k for k, v in mudou.items() if not v["antes"]),
                           "erro": nova.erro}

    quando = datetime.now().strftime("%Y%m%d-%H%M%S")
    destino = pasta / "catalogacao" / "releituras" / quando
    destino.mkdir(parents=True, exist_ok=True)
    mod_planilha.escrever_json(novas, destino / "leituras.json")
    custo = round(config.custo_estimado_usd(t_in, t_out), 4) if hasattr(config, "custo_estimado_usd") else 0.0
    resumo = {
        "projeto_codigo": codigo, "escopo": escopo, "motivo": motivo, "pedido_por": pedido_por,
        "relido_em": _agora(), "documentos": escolhidos, "relidos": len(novas),
        "com_mudanca": sum(1 for c in comparacao.values() if c["mudou"]),
        "campos_preenchidos": sum(len(c["preencheu"]) for c in comparacao.values()),
        "falhas": falhas, "custo_usd": custo,
        "pasta": str(destino.relative_to(raiz_final)) if destino.is_relative_to(raiz_final) else str(destino),
    }
    (destino / "comparacao.json").write_text(
        json.dumps({"resumo": resumo, "documentos": comparacao}, ensure_ascii=False, indent=1), encoding="utf-8")
    if livro is not None:
        livro.anotar("relido", codigo_projeto=codigo,
                     detalhe=f"{escopo}: {len(novas)} folha(s), {resumo['com_mudanca']} com mudança "
                             f"({motivo or 'sem motivo'}; por {pedido_por or 'linha de comando'})")
    _log.info("Releitura %s (%s): %d folha(s), %d com mudança, US$ %.2f.",
              codigo, escopo, len(novas), resumo["com_mudanca"], custo)
    return resumo
