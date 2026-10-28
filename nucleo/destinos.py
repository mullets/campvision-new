"""UX-43: o CAMP Vision reorganiza sozinho as pastas que o painel mandou para outro projeto.

O painel decide, para o lote em "Verificar destino", se a pasta é de outro projeto existente ou de um
projeto novo que ele criou (camp-painel `destino_auto.py`; CONTRATO_UNICO §5.2, versão 1.3). Ele não
mexe no QNAP: marca o lote "movendo para F0xx-P0yyy" e lista em `GET /api/campvision/destinos`
`{id, lote_id, pasta, nome_pasta, de, para, para_titulo, criado, desde, comando}`.

Aqui o vigia, ocioso, faz o que uma pessoa faria com o `comando` (`vigia.py --reidentificar <de>`):

1. SIMULA: o plano do `--reidentificar` (pelo carimbo, sem mexer em nada) só da pasta daquele lote;
2. CONFERE que o plano bate com o pedido do painel: mesmo fundo, todos os grupos da pasta vão para
   `para` (nenhum fica, nenhum vai para outro código, nenhum sai da publicação, nada para uma pessoa
   decidir), `para` vem do painel e está na lista de projetos dele (nunca inventa código);
3. só então APLICA (o equivalente a `--sim`): move e renumera (nada é apagado), refaz os pacotes e
   avisa o painel de cada projeto tocado (`/api/campvision/aviso`); o painel dá o item por concluído
   quando o material chega ao projeto `para`. Se o item ainda estiver na lista depois disso, fecha pelo
   lote (`/api/campvision/reidentificar` com `lote_id`, simulado antes; o painel só aplica com
   `campvision.reidentificar_aplicar` ligado).

Plano que não bate (ou falha): NÃO aplica, registra no livro (`erro`) e no `motivo` do status.json da
pasta (campo que o painel já mostra), e lembra o item em `<estado>/destinos_painel.json` para não tentar
de novo em loop — só tenta outra vez se o painel mudar o item (lote, de, para ou pasta).
Painel fora ou QNAP indisponível não conta como tentativa: fica para a próxima rodada.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime
from pathlib import Path

from .atomico import gravar_json
from . import estrutura

_log = logging.getLogger("cv2.destinos")

MEMORIA = "destinos_painel.json"
PREFIXO = "UX-43"
_CODIGO = re.compile(r"^F\d{3}-P\d{4}$")


class Recusa(Exception):
    """O plano não bate / o pedido não serve: não aplica e lembra o item."""


class Transitorio(Exception):
    """Painel ou QNAP fora agora: não conta como tentativa."""


def assinatura(item: dict) -> str:
    return "|".join(str(item.get(k) or "") for k in ("lote_id", "de", "para", "pasta"))


def _ler_memoria(pasta_estado: Path) -> dict:
    try:
        dados = json.loads((pasta_estado / MEMORIA).read_text(encoding="utf-8"))
        return dados if isinstance(dados, dict) else {}
    except (OSError, ValueError):
        return {}


def _gravar_memoria(pasta_estado: Path, memoria: dict) -> None:
    try:
        pasta_estado.mkdir(parents=True, exist_ok=True)
        gravar_json(pasta_estado / MEMORIA, memoria, indent=1)
    except OSError as erro:
        _log.warning("Memória dos destinos do painel não gravada: %s", erro)


def achar_pasta(raiz: Path, item: dict) -> Path:
    """A pasta DAQUELE lote: código `de` e o mesmo nome de pasta que o painel guardou."""
    from . import releitura as mod_rel

    de = str(item.get("de") or "").upper()
    nome = Path(str(item.get("pasta") or "").rstrip("/\\")).name
    candidatas = [p for p in mod_rel.projetos(raiz, de[:4]) if estrutura.codigo_da_pasta(p) == de]
    if nome:
        candidatas = [p for p in candidatas if p.name == nome]
    if not candidatas:
        raise Recusa(f"a pasta {nome or de} não está no acervo")
    if len(candidatas) > 1:
        raise Recusa(f"{len(candidatas)} pastas com o código {de}: uma pessoa precisa dizer qual")
    return candidatas[0]


def conferir_plano(plano: dict, item: dict) -> list[str]:
    """O plano da simulação bate com o pedido do painel? Lista do que não bate (vazia = pode aplicar)."""
    de, para = str(item["de"]).upper(), str(item["para"]).upper()
    erros = []
    if str(plano.get("codigo") or "").upper() != de:
        erros.append(f"o plano é do projeto {plano.get('codigo')}, não de {de}")
    grupos = plano.get("grupos") or []
    if not grupos:
        erros.append("a pasta não tem folhas lidas para levar")
    for g in grupos:
        n, titulo = len(g.get("folhas") or []), str(g.get("titulo") or "")[:60]
        if g.get("acao") == "mover" and str(g.get("destino") or "").upper() == para:
            continue
        o_que = {"fica": f"o carimbo diz que fica em {de}",
                 "mover": f"o carimbo manda para {g.get('destino')}, não para {para}",
                 "retirar": "sairiam da publicação (outra obra, sem projeto na base)",
                 "perguntar": "precisam de uma pessoa"
                              + (f" ({' × '.join(g.get('candidatos') or [])})" if g.get("candidatos") else "")}
        erros.append(f"{n} folha(s) “{titulo}”: {o_que.get(g.get('acao'), g.get('acao'))}")
    if plano.get("sem_identificacao"):
        erros.append(f"{len(plano['sem_identificacao'])} folha(s) sem identificação ficariam para trás")
    for juntos in plano.get("mesmo_endereco") or []:
        erros.append(f"mesmo endereço, obras diferentes: {' | '.join(juntos)}")
    return erros


def _status_motivo(painel, raiz: Path, pasta: Path | None, motivo: str) -> None:
    """Põe (ou tira, com motivo vazio) o motivo no status.json `pronto` da pasta e avisa o painel.
    Só mexe em status `pronto` e só apaga motivo que este módulo escreveu. Nunca levanta."""
    if pasta is None:
        return
    try:
        caminho = pasta / "status.json"
        atual = json.loads(caminho.read_text(encoding="utf-8"))
        if not isinstance(atual, dict) or atual.get("status") != "pronto":
            return
        antigo = str(atual.get("motivo") or "")
        if not motivo and not antigo.startswith(PREFIXO):
            return
        if antigo == motivo:
            return
        atual["motivo"] = motivo
        gravar_json(caminho, atual)
        codigo = estrutura.codigo_da_pasta(pasta)
        try:
            relativo = str(pasta.relative_to(raiz))
        except ValueError:
            relativo = str(pasta)
        painel.aviso(codigo, relativo, "pronto")
    except (OSError, ValueError) as erro:
        _log.warning("status.json de %s: motivo não gravado (%s)", pasta, erro)


def _fechar_no_painel(painel, item: dict) -> str:
    """O aviso da pasta normalmente basta. Se o painel ainda lista o item, fecha pelo lote (simula antes)."""
    lista = painel.destinos()
    if lista is None:
        return "painel sem resposta depois do aviso (ele conclui quando reler a pasta)"
    if not any(str(d.get("id")) == str(item.get("id")) for d in lista):
        return "o painel deu por concluído"
    lote = item.get("lote_id")
    if lote in (None, ""):
        return "o painel ainda lista o item (sem lote_id para fechar)"
    motivo = f"pasta reorganizada pelo CAMP Vision para {item['para']} (UX-43)"
    status, dados = painel.reidentificar_lote(lote, item["para"], simular=True, motivo=motivo)
    if status != 200 or not isinstance(dados, dict) or not dados.get("ok"):
        return f"o painel ainda lista o item; simulação do lote no painel: HTTP {status}"
    status, dados = painel.reidentificar_lote(lote, item["para"], simular=False, motivo=motivo)
    if status == 200 and isinstance(dados, dict) and dados.get("aplicado"):
        return f"lote {lote} passado para {item['para']} no painel"
    mensagem = (dados or {}).get("mensagem") if isinstance(dados, dict) else ""
    return f"o painel ainda lista o item; lote não passado (HTTP {status}{': ' + str(mensagem)[:120] if mensagem else ''})"


def processar(item: dict, config, painel, livro, pasta_estado: Path, tabela=None) -> dict:
    """Um item da lista: simula, confere, aplica. Levanta Recusa ou Transitorio; devolve o resumo do aplicado."""
    from . import reidentificar as mod_reid

    de, para = str(item.get("de") or "").upper(), str(item.get("para") or "").upper()
    if not _CODIGO.match(de) or not _CODIGO.match(para):
        raise Recusa(f"códigos inválidos no pedido do painel ({de or '?'} → {para or '?'})")
    if de == para:
        raise Recusa(f"o destino é o próprio projeto {de}")
    if de[:4] != para[:4]:
        raise Recusa(f"{para} é de outro fundo que {de}: não reorganiza entre fundos")
    item = {**item, "de": de, "para": para}
    raiz = Path(config.raiz_final)
    if not raiz.is_dir():
        raise Transitorio(f"acervo indisponível em {raiz}")
    pasta = achar_pasta(raiz, item)
    contexto = painel.contexto(de[:4])
    if not isinstance(contexto, dict):
        raise Transitorio("painel sem a lista de projetos do fundo")
    base = contexto.get("projetos") or []
    if not any(str(p.get("codigo") or "").upper() == para for p in base if isinstance(p, dict)):
        raise Recusa(f"o projeto {para} não está na lista de projetos do painel")
    try:
        plano = mod_reid.planejar(pasta, base, pasta_estado, usar_nome_da_pasta=False)
    except OSError as erro:
        raise Transitorio(f"QNAP ao ler {pasta.name}: {erro}") from erro
    except Exception as erro:  # noqa: BLE001 - plano que não sai não é aplicado
        raise Recusa(f"a simulação falhou: {erro}") from erro
    _log.info("Destino do painel %s: simulação\n%s", item.get("id"), mod_reid.texto(plano))
    erros = conferir_plano(plano, item)
    if erros:
        raise Recusa("a simulação não bate com o destino pedido: " + "; ".join(erros))
    try:
        tocados = mod_reid.aplicar(plano, raiz, pasta_estado, base, livro, painel, tabela, chamar_rotas_admin=False)
    except Exception as erro:  # noqa: BLE001
        raise Recusa(f"falhou ao aplicar (veja o livro e catalogacao/ da pasta): {erro}") from erro
    folhas = sum(len(g["folhas"]) for g in plano["grupos"])
    livro.anotar("refeito", codigo_projeto=de, arquivo_origem=pasta.name,
                 detalhe=f"{PREFIXO}: {folhas} folha(s) reorganizada(s) de {de} para {para} "
                         f"(destino {item.get('id')} decidido pelo painel; simulado antes)")
    _status_motivo(painel, raiz, pasta, "")   # tira um motivo antigo deste módulo, se houver
    return {"folhas": folhas, "tocados": tocados, "painel": _fechar_no_painel(painel, item)}


def atender(config, painel, livro, pasta_estado: Path, cancelar=None, estado=None, tabela=None) -> int:
    """Uma rodada: lê a lista do painel e reorganiza, uma pasta por vez, até o teto. Devolve quantas aplicou."""
    lista = painel.destinos()
    if lista is None:                       # painel antigo (404) ou fora: em silêncio
        return 0
    memoria = _ler_memoria(pasta_estado)
    ids = {str(d.get("id")) for d in lista}
    memoria = {k: v for k, v in memoria.items() if k in ids}   # saiu da lista: esquece
    teto = max(0, int(getattr(config, "destinos_por_rodada", 3) or 0))
    tentativas = aplicados = 0
    for item in lista:
        if tentativas >= teto or (cancelar is not None and cancelar.is_set()):
            break
        chave = str(item.get("id"))
        if memoria.get(chave, {}).get("assinatura") == assinatura(item):
            continue                        # já tentado assim: só de novo se o painel mudar o item
        if estado is not None:
            estado.situacao = "reorganizando pasta"
            estado.projeto_atual = str(item.get("de") or "")
        tentativas += 1
        pasta = None
        try:
            pasta = achar_pasta(Path(config.raiz_final), item) if Path(config.raiz_final).is_dir() else None
        except Recusa:
            pass
        try:
            r = processar(item, config, painel, livro, pasta_estado, tabela)
        except Transitorio as erro:
            _log.warning("Destino do painel %s adiado: %s", chave, erro)
            break                           # painel/QNAP fora: nada mais nesta rodada
        except Recusa as erro:
            motivo = str(erro)[:400]
            _log.warning("Destino do painel %s (%s → %s) NÃO aplicado: %s", chave, item.get("de"), item.get("para"), motivo)
            livro.anotar("erro", codigo_projeto=str(item.get("de") or ""),
                         arquivo_origem=Path(str(item.get("pasta") or "")).name,
                         detalhe=f"{PREFIXO}: não reorganizado para {item.get('para')} (destino {chave} do painel): {motivo}")
            _status_motivo(painel, Path(config.raiz_final), pasta,
                           f"{PREFIXO}: o CAMP Vision não reorganizou para {item.get('para')}: {motivo}"[:500])
            memoria[chave] = {"assinatura": assinatura(item), "resultado": "nao_aplicado", "motivo": motivo,
                              "em": datetime.now().isoformat(timespec="seconds")}
            if estado is not None:
                estado.anotar(f"destino {item.get('de')} → {item.get('para')}: não aplicado"[:120])
            continue
        aplicados += 1
        memoria[chave] = {"assinatura": assinatura(item), "resultado": "aplicado", "folhas": r["folhas"],
                          "painel": r["painel"], "em": datetime.now().isoformat(timespec="seconds")}
        _log.info("Destino do painel %s: %s → %s aplicado (%d folha(s)); %s", chave, item["de"], item["para"],
                  r["folhas"], r["painel"])
        if estado is not None:
            estado.anotar(f"pasta {item['de']} → {item['para']}: {r['folhas']} folha(s)"[:120])
    _gravar_memoria(pasta_estado, memoria)
    if estado is not None:
        estado.projeto_atual = ""
    return aplicados
