"""`vigia.py --reidentificar F0xx[-P0xxx]`: o que JÁ entrou no código errado (auditoria F026).

Relê nada (usa leituras.json): agrupa as folhas de cada projeto pelo carimbo, casa
cada grupo com a base do fundo e mostra o plano. Com `--sim`:

- grupo com UM projeto forte na base, diferente do atual → as folhas vão para lá
  (move e renumera, como no recebimento; o livro registra `refeito`);
- grupo que precisa de pessoa (dois candidatos, mesmo endereço, fases) → NÃO
  mexe: fica listado em catalogacao/reidentificacao.json;
- folhas de outra obra sem projeto na base, sem identificação e capas → saem do
  pacote de publicação (retirados), com o que o carimbo diz;
- refaz os pacotes e avisa o painel de cada projeto tocado.

Nada é apagado. Folha já publicada no site muda de código; o painel recusa (409) mover
folha pública sem despublicar antes.

CAMP-009 — painel: depois de mover os arquivos, `aplicar` grava
`catalogacao/reidentificacao_painel.json` com as chamadas das rotas do painel
(`POST /api/itens/mover`, `/api/projetos/{origem}/mesclar-em`; admin, por cookie de
sessão — o token da estação não vale). Só CHAMA se houver `CAMP_PAINEL_SESSAO`;
sem ela o arquivo é o roteiro para fazer pela tela. O aviso por projeto continua.
Sem `--sim` (ou com `--simular`) nada é regravado nem chamado.
"""

from __future__ import annotations

import json
from pathlib import Path

from .atomico import gravar_json
from . import estrutura, identidade as mod_ident, planilha as mod_planilha
from . import releitura as mod_rel, transferir as mod_transferir


def _ler(caminho: Path, padrao):
    try:
        return json.loads(caminho.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return padrao


def planejar(pasta: Path, base: list[dict], pasta_estado: Path, usar_nome_da_pasta: bool = True) -> dict:
    """usar_nome_da_pasta=False (UX-43, destino já decidido no painel): o nome da pasta não serve de pista. Desde 28/10 ele é o nome
    digitado na estação (não o carimbo), justamente o que pode estar errado quando o painel manda a pasta para outro projeto."""
    codigo = estrutura.codigo_da_pasta(pasta)
    leituras = {Path(l.arquivo).stem: l for l in mod_planilha.ler_json(pasta / "catalogacao" / "leituras.json")}
    cache = pasta_estado / "leitura" / codigo
    from .entrada import _proporcao

    analise = mod_ident.analisar(leituras, {c: _proporcao(cache / f"{c}.jpg") for c in leituras}, base)
    varios = len(analise.grupos) > 1
    pista = "" if varios or not usar_nome_da_pasta else mod_ident.pista_da_pasta(estrutura.nome_sem_codigo(pasta.name))
    perguntar = {analise.grupos[i].chave for juntos in analise.mesmo_endereco for i in juntos}
    perguntar |= {f["grupo"] for f in analise.fases}
    grupos = []
    for g in analise.grupos:
        casa = mod_ident.casar_com_base(g, base, pista)   # o código atual concorre como qualquer outro
        forte = (casa.get("forte") or {}).get("codigo", "")
        # O próprio código atual também é candidato: um lote homogêneo no código certo fica.
        atual = next((p for p in base if str(p.get("codigo")) == codigo), None)
        if atual and not varios:
            casa_atual = mod_ident.casar_com_base(g, [atual], pista)
            if "forte" in casa_atual and not forte:
                forte = codigo
        if g.chave in perguntar:
            acao, alvo = "perguntar", ""
        elif forte and forte.upper() == codigo.upper():
            acao, alvo = "fica", codigo
        elif forte:
            acao, alvo = "mover", forte
        elif casa.get("candidatos"):
            acao, alvo = "perguntar", ""
        elif varios and len(g.folhas) < mod_ident.MINIMO_GRUPO:
            acao, alvo = "retirar", ""
        else:
            acao, alvo = ("fica", codigo) if not varios else ("perguntar", "")
        grupos.append({**g.como_dict(), "acao": acao, "destino": alvo,
                       "candidatos": [c["codigo"] for c in casa.get("candidatos") or []]})
    # O maior grupo que fica (ou o maior de todos) é o "dono" das capas e das sem identificação.
    return {"codigo": codigo, "pasta": str(pasta), "grupos": grupos, "capas": analise.capas,
            "sem_identificacao": analise.sem_identificacao if varios else [],
            "mesmo_endereco": [[analise.grupos[i].titulo for i in j] for j in analise.mesmo_endereco],
            "_analise": analise, "_leituras": leituras}


def texto(plano: dict) -> str:
    linhas = [f"{plano['codigo']}  ({Path(plano['pasta']).name})"]
    for g in plano["grupos"]:
        alvo = {"fica": "fica", "mover": f"→ {g['destino']}", "retirar": "fora da publicação (outra obra, sem projeto na base)",
                "perguntar": "PERGUNTAR" + (f" ({' × '.join(g['candidatos'])})" if g["candidatos"] else "")}[g["acao"]]
        linhas.append(f"   {len(g['folhas']):3d} folha(s)  {g['titulo'][:70]:70s}  {alvo}")
    if plano["sem_identificacao"]:
        linhas.append(f"   {len(plano['sem_identificacao']):3d} sem identificação: {', '.join(plano['sem_identificacao'][:6])}")
    if plano["capas"]:
        linhas.append(f"   {len(plano['capas']):3d} capa(s)/tira(s): {', '.join(plano['capas'])}")
    for juntos in plano["mesmo_endereco"]:
        linhas.append(f"   mesmo endereço, obras diferentes: {' | '.join(juntos)}")
    return "\n".join(linhas)


def chamadas_painel(origem: str, destino: str, trocas: dict, folhas_origem: list[str]) -> dict:
    """Rota certa para uma mudança: projeto inteiro → mesclar-em; parte → itens/mover. `trocas` = {código antigo: novo}."""
    antigos = sorted(trocas)
    inteiro = bool(folhas_origem) and set(folhas_origem) <= set(antigos)
    if inteiro:
        return {"rota": f"POST /api/projetos/{origem}/mesclar-em", "corpo": {"destino": destino,
                "motivo": f"reidentificado pelo carimbo"}, "origem": origem, "destino": destino, "folhas": antigos}
    return {"rota": "POST /api/itens/mover", "corpo": {"itens": antigos, "destino": destino,
            "motivo": "reidentificado pelo carimbo"}, "origem": origem, "destino": destino, "folhas": antigos}


def avisar_painel_das_mudancas(painel, chamadas: list[dict]) -> list[dict]:
    """Chama as rotas só com sessão de admin; registra o resultado de cada uma. Nunca levanta."""
    for c in chamadas:
        if not (painel is not None and getattr(painel, "ligado", False) and getattr(painel, "sessao", "")):
            c["resultado"] = "não chamado (sem CAMP_PAINEL_SESSAO): faça pela tela do painel"
            continue
        if c["rota"].endswith("mesclar-em"):
            status, dados = painel.mesclar_projeto(c["origem"], c["destino"], c["corpo"]["motivo"])
        else:
            status, dados = painel.mover_folhas(c["destino"], c["corpo"]["itens"], c["corpo"]["motivo"])
        c["resultado"] = {"http": status, "resposta": dados if isinstance(dados, dict) else None}
    return chamadas


def aplicar(plano: dict, raiz_final: Path, pasta_estado: Path, base: list[dict], livro, painel, tabela=None,
            chamar_rotas_admin: bool = True) -> list[str]:
    """Move o que tem destino forte, marca o resto no preparo, refaz os pacotes. Devolve os projetos tocados.

    `chamar_rotas_admin=False` (UX-43, destino decidido pelo painel): não chama mesclar/mover de admin,
    nem com CAMP_PAINEL_SESSAO — quem decidiu foi o painel, e o aviso da pasta basta para ele."""
    from .entrada import marcar_identidade

    pasta = Path(plano["pasta"])
    codigo = plano["codigo"]
    analise, leituras = plano["_analise"], plano["_leituras"]
    tocados = {codigo}
    # Preparo: carimbo, capa, sem identificação e "divergente" (grupos sem destino que não ficam).
    ficam = [g for g in plano["grupos"] if g["acao"] in ("fica", "perguntar")]
    dono = max(ficam or plano["grupos"], key=lambda g: len(g["folhas"])) if plano["grupos"] else None
    folhas_dono = set(dono["folhas"]) if dono else set()
    destinos = [{"codigo": codigo, "documentos": list(leituras), "folhas_grupo": sorted(folhas_dono)}]
    preparos = _ler(pasta / "catalogacao" / "preparo.json", {}) or {}
    marcar_identidade(preparos, analise, destinos, leituras)
    for g in plano["grupos"]:
        if g["acao"] == "perguntar":       # a pessoa decide: não publica até lá, mas não é "outra obra"
            for c in g["folhas"]:
                preparos.setdefault(c, {}).setdefault("identidade", {})["divergente"] = g is not dono
    gravar_json(pasta / "catalogacao" / "preparo.json", preparos, indent=1)
    pasta_fundo = pasta.parents[1]
    chamadas: list[dict] = []
    for g in plano["grupos"]:
        if g["acao"] != "mover":
            continue
        titulo = next((str(p.get("projeto") or "") for p in base if str(p.get("codigo")) == g["destino"]), g["titulo"])
        from . import fundos as mod_fundos

        fundo = (tabela.get(codigo[:4]) if tabela else None) or mod_fundos.Fundo(codigo[:4], "", codigo[:4])
        destino = estrutura.garantir_projeto(pasta_fundo, g["destino"], titulo, fundo)
        trocas, _movidos = mod_transferir.transferir(raiz_final, pasta_estado, pasta, destino, g["folhas"],
                                                     g["destino"], livro, f"reidentificado pelo carimbo: {g['titulo']}")
        prep_d = _ler(destino / "catalogacao" / "preparo.json", {}) or {}
        for novo in trocas.values():   # no projeto certo, a folha é do projeto
            ident = prep_d.setdefault(novo, {}).setdefault("identidade", {})
            ident.update(divergente=False, sem_identificacao=False)
        gravar_json(destino / "catalogacao" / "preparo.json", prep_d, indent=1)
        _mover_leituras(pasta, destino, trocas)
        chamadas.append(chamadas_painel(codigo, g["destino"], trocas, list(leituras)))
        tocados.add(g["destino"])
    for c in sorted(tocados):
        try:
            mod_rel.remontar_pacote(raiz_final, pasta_estado, c, tabela)
        except FileNotFoundError:
            continue
        alvo = mod_rel.mod_renomear.achar_projeto(raiz_final, c)
        if alvo is not None and painel is not None:
            painel.aviso(c, str(alvo.relative_to(raiz_final)), "pronto")
    if chamadas:
        if chamar_rotas_admin:
            chamadas = avisar_painel_das_mudancas(painel, chamadas)
        else:
            for c in chamadas:
                c["resultado"] = "não chamado: destino decidido pelo painel (UX-43); o aviso da pasta basta"
        gravar_json(pasta / "catalogacao" / "reidentificacao_painel.json", chamadas, indent=1)
    salvo = {k: v for k, v in plano.items() if not k.startswith("_")}
    gravar_json(pasta / "catalogacao" / "reidentificacao.json", salvo, indent=1)
    return sorted(tocados)


def _mover_leituras(origem: Path, destino: Path, trocas: dict) -> None:
    """leituras.json: as folhas levadas saem da origem e entram no destino com o código novo."""
    lo = mod_planilha.ler_json(origem / "catalogacao" / "leituras.json")
    ld = mod_planilha.ler_json(destino / "catalogacao" / "leituras.json")
    ficam, vao = [], []
    for l in lo:
        stem = Path(l.arquivo).stem
        if stem in trocas:
            l.arquivo = l.arquivo.replace(stem, trocas[stem])
            vao.append(l)
        else:
            ficam.append(l)
    ja = {Path(l.arquivo).stem for l in ld}
    mod_planilha.escrever_json(ficam, origem / "catalogacao" / "leituras.json")
    mod_planilha.escrever_json(ld + [l for l in vao if Path(l.arquivo).stem not in ja],
                               destino / "catalogacao" / "leituras.json")
