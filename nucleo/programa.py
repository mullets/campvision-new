"""Sugestão de programa/uso (taxonomia 9947) e natureza (9952) pelo título do
projeto — ticket CV-26. Porte literal das regras validadas em 27/09/2026 contra
804 fichas (programa 89,7% exato, natureza 93,8%): mesma ordem, mesmos IDs.

Sempre SUGESTÃO: o pacote leva `programa_sugerido`/`natureza_sugerida` e a
regra que disparou; quem grava no site é gente. Título que descreve documento
(CV-14) não recebe sugestão.
"""

from __future__ import annotations

import re
import unicodedata

from .derivacao import e_documento


def _norm(texto: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", texto or "")
                   if unicodedata.category(c) != "Mn").lower()


# Ordem = do específico ao genérico (Anexo C). Não reordenar sem back-test (CV-23).
REGRAS_PROGRAMA: tuple[tuple[str, int], ...] = (
    (r"casa de veraneio", 64), (r"casa de praia", 62), (r"loteamento", 254),
    (r"casa de campo|chacara|sitio\b", 63), (r"sede de fazenda|fazenda", 236),
    (r"conjunto habitacional|habitacao social|conjunto residencial", 69),
    (r"comercial */ *residencial|residencial e comercial|comercial e residencial|uso misto|edificio misto"
     r"|edificio multiuso|lojas e ap|ap\w* e lojas", 258),
    (r"predios? de apartamentos|edificios? de apartamentos|apartamentos tipo|conjunto de apartamentos"
     r"|predio de apt|apartamentos\b|edificio residencial|ed\.? *apt|edificio multifamiliar"
     r"|habitacao coletiva", 68),
    (r"apartamento\b|apto\b|ap\.? *\d", 301), (r"edicula", 66),
    (r"apart-?hotel|flat\b", 73), (r"colonia de ferias", 77), (r"pousada|motel", 74), (r"hotel", 72),
    (r"alojamento", 76),
    (r"agencia (bb|do bb|safra|comind|bradesco|itau|banespa|banco)|banco safra|banco comind"
     r"|banco do brasil|\bbanco\b|\bag\.|agencia bancaria|banespa|caixa economica", 95),
    (r"laboratorio", 115), (r"deposito|armazem|galpao|centro de distribuicao", 206),
    (r"fabrica|industria|industrial|\bcimento\b|siderurg", 203),
    (r"escritorio|organizacao do escritorio|layout|lay-?out|sede administrativa|sede da", 93),
    (r"supermercado", 84), (r"shopping", 83), (r"galeria comercial", 81),
    (r"concessionaria|revendedora", 86), (r"drogaria|drogacenter|farmacia", 80),
    (r"loja|magazine|boutique|banca de jornal", 80), (r"lanchonete|restaurante|churrascaria", 88),
    (r"\bbar\b|cafeteria|confeitaria", 90),
    (r"stand|cenografia|pavilhao de exposicoes|expografia", 265),
    (r"posto de combustivel|posto de gasolina", 91),
    (r"hospital", 105), (r"maternidade", 107), (r"pronto-?socorro", 108), (r"clinica", 109),
    (r"consultorio", 110), (r"ambulatorio", 111), (r"sanatorio", 114),
    (r"creche", 122), (r"pre-?escola|jardim de infancia", 124), (r"escola tecnica", 126),
    (r"escola|colegio", 125), (r"faculdade", 131), (r"universidade|campus", 132),
    (r"igreja|paroquia|santuario", 168), (r"capela", 169), (r"convento|mosteiro", 176),
    (r"seminario", 177), (r"cemiterio", 178), (r"crematorio", 179),
    (r"plano diretor", 256),
    (r"desenvolvimento urbano|desenv\.? *urbano|macro eixo|requalificacao urbana|urbanizacao", 253),
    (r"praca", 245), (r"parque", 246), (r"canteiro central", 244), (r"calcadao", 249),
    (r"monumento", 251),
    (r"teatro", 145), (r"cinema", 146), (r"auditorio|sala de concertos", 147), (r"museu", 140),
    (r"biblioteca", 143), (r"centro cultural", 141), (r"galeria de arte", 142),
    (r"centro de convencoes", 149),
    (r"iate clube", 156), (r"clube", 153),
    (r"centro esportivo|ginasio|quadra (poli)?esportiva|centro poliesportivo", 157), (r"estadio", 158),
    (r"piscina", 160),
    (r"usina de lixo", 233), (r"frigorifico", 208), (r"silo", 209), (r"usina", 210),
    (r"subestacao", 232), (r"barragem", 228), (r"caixa-?d.agua|reservatorio", 229),
    (r"rodoviaria", 213), (r"terminal de onibus", 214), (r"estacionamento|edificio-?garagem", 220),
    (r"instituto de pesquisas?", 136), (r"centro de pesquisa", 135),
    (r"prefeitura|paco municipal", 186), (r"camara municipal", 187), (r"forum", 190),
    (r"tribunal", 191), (r"repartic", 184),
    (r"centro comunitario|centro de convivencia", 119), (r"asilo|casa de repouso", 117),
    (r"orfanato", 118),
    (r"quartel", 198), (r"delegacia", 197), (r"corpo de bombeiros", 199),
    (r"mobiliario|luminaria|moveis", 262),
    (r"edificio comercial|edificio de escritorios|edificio corporativo", 94),
    (r"residencia|resid\b|resid\.|sobrado|moradia", 61),
    (r"\bsesc\b|\bsenac\b", 151),
)

REGRAS_NATUREZA: tuple[tuple[str, int], ...] = (
    (r"restauro|restauracao|tombad", 268),
    (r"comunicacao visual|sinalizacao|identidade visual", 274),
    (r"paisagismo", 271),
    (r"mobiliario|luminaria|moveis", 270),
    (r"interiores|layout|decoracao", 269),
    (r"reforma|ampliacao|anexo|adaptacao|readequacao|modernizacao|remodelacao", 267),
    (r"plano diretor|desenvolvimento urbano|desenv\.? *urbano|loteamento|cidade nova|macro eixo", 273),
    (r"desenho urbano|requalificacao urbana|urbanizacao", 272),
)
NATUREZA_PADRAO = 266  # obra nova, quando um programa casou e nenhum marcador apareceu

_RP = tuple((re.compile(r), i) for r, i in REGRAS_PROGRAMA)
_RN = tuple((re.compile(r), i) for r, i in REGRAS_NATUREZA)


def sugerir(titulo: str) -> dict:
    """{'programa': id|None, 'regra_programa', 'natureza': id|None, 'regra_natureza', 'motivo'}."""
    t = _norm(titulo)
    vazio = {"programa": None, "regra_programa": "", "natureza": None, "regra_natureza": "", "motivo": ""}
    if not t.strip():
        return vazio
    if e_documento(titulo):
        return {**vazio, "motivo": "título descreve documento, não obra (CV-14)"}
    banco = bool(re.search(r"agencia|\bbanco\b|\bag\.", t))
    resid = bool(re.search(r"residencia|resid\.", t))
    saida = dict(vazio)
    if banco and resid:
        saida["motivo"] = "ambíguo: residência e banco no mesmo título"
    else:
        for regex, ident in _RP:
            if not regex.search(t):
                continue
            if ident == 236 and resid:
                continue  # "Residência Fazenda X" é residência
            saida["programa"], saida["regra_programa"] = ident, regex.pattern
            break
    for regex, ident in _RN:
        if regex.search(t):
            saida["natureza"], saida["regra_natureza"] = ident, regex.pattern
            break
    if saida["natureza"] is None and saida["programa"] is not None:
        saida["natureza"], saida["regra_natureza"] = NATUREZA_PADRAO, "padrão: obra nova"
    return saida


LIMIAR = 0.85


def backtest(linhas: list[dict]) -> dict:
    """CV-23: compara a sugestão com fichas já preenchidas por gente.

    `linhas`: dicts com 'titulo' e 'programa'/'natureza' (IDs). Libera só com
    ≥ 85% de acerto exato; as divergências vão listadas para exame uma a uma.
    """
    saida = {}
    for campo, chave in (("programa", "programa"), ("natureza", "natureza")):
        exatos, divergentes, sem_regra = 0, [], 0
        for linha in linhas:
            curado = str(linha.get(chave) or "").strip()
            if not curado or not linha.get("titulo"):
                continue
            sugerido = sugerir(linha["titulo"])[campo]
            if sugerido is None:
                sem_regra += 1
                continue
            if str(sugerido) == curado:
                exatos += 1
            else:
                divergentes.append((linha["titulo"], curado, str(sugerido)))
        total = exatos + len(divergentes)
        taxa = exatos / total if total else 0.0
        saida[campo] = {"exatos": exatos, "total": total, "taxa": taxa, "sem_regra": sem_regra,
                        "divergentes": divergentes, "liberado": total > 0 and taxa >= LIMIAR}
    return saida
