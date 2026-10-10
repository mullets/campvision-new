"""Esquema dos dados lidos do carimbo.

Este arquivo é a fonte única da verdade: os campos aqui definem o que a IA
recebe como schema, o que vira coluna da planilha e o que a consolidação
por projeto pode corrigir. Para acrescentar um campo, mexa SÓ aqui.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class Campo:
    nome: str
    rotulo: str
    descricao: str
    # Campo que tende a ser igual em todas as pranchas do mesmo projeto e por
    # isso pode ser normalizado/preenchido na consolidação por projeto.
    do_projeto: bool = False
    # False = derivado em código depois da leitura, nunca pedido ao modelo.
    do_modelo: bool = True


# Método de leitura (projeto Tainacam: claude/campvision-metodo-de-leitura.md §3.2).
# Os nomes antigos foram mantidos onde o significado é o mesmo:
# projeto = projeto_carimbo, titulo_prancha = nome_prancha, folha = numero_folha.
# `do_modelo=False` marca campo DERIVADO em código (§5), não pedido ao modelo.
CAMPOS: tuple[Campo, ...] = (
    Campo("projeto", "Projeto", "Nome da obra/projeto como escrito no carimbo.", True),
    Campo("cliente", "Cliente", "Proprietário ou contratante.", True),
    Campo("arquiteto", "Arquiteto", "Autor do projeto (pessoa física), como escrito.", True),
    Campo("escritorio", "Escritório", "Escritório/empresa responsável, SEPARADO do arquiteto. "
                                      "Ex.: 'XYZ Arquitetos Associados S/C'.", True),
    Campo("endereco", "Endereço", "Endereço da obra, com número se houver.", True),
    Campo("cidade", "Cidade", "Município da obra.", True),
    Campo("uf", "UF", "Sigla do estado, 2 letras.", True),
    Campo("data", "Data", "Data como escrita (carimbo, margem, lápis). Literal: '14.10.83'.", False),
    Campo("escala", "Escala", "Escala, ex: 1:50, 1:100, S/ESC.", False),
    Campo("titulo_prancha", "Título da prancha", "Nome/título desta prancha, literal.", False),
    Campo("folha", "Folha", "Número da folha, onde estiver (carimbo ou contorno): '03', 'F.34'.", False),
    Campo("total_folhas", "Total de folhas", "Total de folhas do conjunto, se indicado.", False),
    Campo("codigo_serie", "Código de série", "Código da série/folha, ex.: 'FL 1/6', 'C-3/12', 'ABC-2/R-1'.", False),
    Campo("codigo_unidade", "Código de unidade",
          "Código que identifica a obra/unidade escrito nas folhas (sigla + número, ex.: 'ABC-1', "
          "'ABC-1/R-1'), quando houver. Não confundir com número de folha.", False),
    Campo("revisao", "Revisão", "Indicação de revisão/reforma, ex.: 'R-1', 'REV. A'.", False),
    Campo("ano", "Ano", "Ano de 4 dígitos derivado da data lida.", False, do_modelo=False),
    Campo("tipo", "Tipo", "Tipo de desenho, derivado do título por regra (§5.2).", False, do_modelo=False),
)

CAMPOS_DO_MODELO = tuple(c for c in CAMPOS if c.do_modelo)
CAMPOS_POR_NOME = {c.nome: c for c in CAMPOS}
CAMPOS_DO_PROJETO = tuple(c.nome for c in CAMPOS if c.do_projeto)
LUGARES = ("carimbo", "margem", "contorno", "manuscrito", "legenda", "corpo")


@dataclass
class Leitura:
    """Resultado da leitura de UMA prancha."""

    arquivo: str = ""
    valores: dict[str, str] = field(default_factory=dict)
    confiancas: dict[str, float] = field(default_factory=dict)
    # Caixa do carimbo em coordenadas normalizadas 0-1: (x0, y0, x1, y1),
    # já na orientação original do arquivo.
    regiao: tuple[float, float, float, float] | None = None
    # Quantos graus a imagem precisa girar no sentido horário para o carimbo
    # ficar legível (0, 90, 180, 270).
    rotacao: int = 0
    carimbo_encontrado: bool = False
    passes: int = 0
    tokens_entrada: int = 0
    tokens_saida: int = 0
    erro: str = ""
    nota_ia: str = ""
    # Preenchidos pela consolidação por projeto (nucleo/grupos.py).
    grupo: str = ""
    lidos_originais: dict[str, str] = field(default_factory=dict)
    ano_do_projeto: str = ""
    suspeita_grupo: bool = False
    # Pistas tiradas do caminho da pasta (nucleo/caminho.py) — segunda fonte,
    # sempre etiquetada: nunca se confunde com o que foi lido do carimbo.
    pista_projeto: str = ""
    pista_ano: str = ""
    pista_fundo: str = ""
    campos_da_pasta: list[str] = field(default_factory=list)
    divergencias: list[str] = field(default_factory=list)
    campos_do_info: dict[str, str] = field(default_factory=dict)
    # O atalho pelo cache de região foi tentado e não bastou?
    cache_falhou: bool = False
    # Medição do 2º passe: ele dobra o custo da prancha, então precisa provar
    # que serve. Guarda a confiança antes e depois para o relatório contar.
    fez_segundo_passe: bool = False
    confianca_antes_do_2o: float = 0.0
    ganho_de_resolucao: float = 0.0
    # --- Método de leitura (§3.2, §6, §8.1) ---
    modo: str = "prancha"                       # prancha | fotografia
    legivel: bool = True
    alternativas: dict[str, list[str]] = field(default_factory=dict)
    onde: dict[str, str] = field(default_factory=dict)
    transcricao_integral: str = ""
    materiais_citados: list[str] = field(default_factory=list)
    anotacoes_manuscritas: list[str] = field(default_factory=list)
    observacoes_leitura: list[str] = field(default_factory=list)
    foto: dict[str, Any] = field(default_factory=dict)
    # Preparo (§2) e consolidação (§4)
    md5: str = ""
    hash_perceptual: str = ""
    duplicata_de: str = ""
    tipo_duplicata: str = ""
    rotacao_aplicada: int = 0
    orientacao_incerta: bool = False
    espelhada: bool = False
    autoria_divergente: bool = False
    outliers: list[str] = field(default_factory=list)
    ressalvas: list[str] = field(default_factory=list)
    e_documento: bool = False
    # Procedimento padrão: triagem por série (etapa 2), formato de origem
    # (etapa 1), pessoas pelas chaves de identidade (etapa 0), período do fundo.
    serie_incerta: bool = False
    triagem: dict[str, Any] = field(default_factory=dict)
    origem_formato: str = ""
    pessoas_identificadas: list[str] = field(default_factory=list)
    fora_do_periodo: bool = False
    textual: dict[str, Any] = field(default_factory=dict)
    # Tickets CV-01/08/09/12/16/21.
    arquivo_origem: str = ""
    data_lida: str = ""
    data_iso: str = ""
    data_sugerida: str = ""
    data_outlier: bool = False
    endereco_variantes: list[str] = field(default_factory=list)
    conflito_endereco: bool = False
    confianca_rotacao: float = 0.0
    titulo_publicacao: str = ""
    # Ticket 89: com que versão/prompt/modelo esta folha foi lida.
    versao_cv2: str = ""
    versao_prompt: str = ""
    modelo: str = ""

    @property
    def confianca_media(self) -> float:
        """Média das confianças dos campos efetivamente preenchidos."""
        preenchidos = [
            self.confiancas.get(nome, 0.0)
            for nome, valor in self.valores.items()
            if str(valor).strip()
        ]
        if not preenchidos:
            return 0.0
        return sum(preenchidos) / len(preenchidos)

    def para_dict(self) -> dict[str, Any]:
        from dataclasses import asdict

        dados = asdict(self)
        dados["regiao"] = list(self.regiao) if self.regiao else None
        return dados

    @classmethod
    def de_dict(cls, dados: dict[str, Any]) -> "Leitura":
        """Aceita JSON antigo e novo: chave desconhecida é ignorada, faltante usa o padrão."""
        from dataclasses import fields as _campos

        conhecidos = {f.name for f in _campos(cls)}
        limpos = {k: v for k, v in dados.items() if k in conhecidos}
        regiao = limpos.get("regiao")
        limpos["regiao"] = tuple(regiao) if regiao else None
        return cls(**limpos)


def _campo_schema(descricao: str) -> dict[str, Any]:
    return {
        "type": "object",
        "description": descricao,
        "properties": {
            "valor": {"type": ["string", "null"],
                      "description": "Transcrição LITERAL. null se não existe ou está ilegível."},
            "confianca": {"type": "number", "description": "0 a 1."},
            "alternativas": {"type": "array", "items": {"type": "string"},
                             "description": "Outras leituras possíveis (dígito ambíguo 3/5/8, 1/7, 0/6)."},
            "onde": {"type": "string", "enum": [*LUGARES, ""],
                     "description": "Em que parte da folha foi lido. Vazio se o valor é null."},
        },
        "required": ["valor", "confianca"],
    }


def esquema_ferramenta() -> dict[str, Any]:
    """Esquema da leitura de PRANCHA (§3.2). Cada campo: {valor, confianca, alternativas, onde}."""
    propriedades: dict[str, Any] = {
        "legivel": {"type": "boolean", "description": "false se a folha não dá para ler."},
        "tem_carimbo": {"type": "boolean", "description": "true se há carimbo/legenda de título."},
    }
    for campo in CAMPOS_DO_MODELO:
        propriedades[campo.nome] = _campo_schema(campo.descricao)
    propriedades.update({
        "transcricao_integral": {"type": "string",
                                 "description": "Todo o texto legível da folha, corrido, literal."},
        "materiais_citados": {"type": "array", "items": {"type": "string"},
                              "description": "Materiais ESCRITOS na folha (não deduzidos)."},
        "anotacoes_manuscritas": {"type": "array", "items": {"type": "string"}},
        "observacoes_de_leitura": {"type": "array", "items": {"type": "string"}},
    })
    return {
        "name": "registrar_prancha",
        "description": "Registra a transcrição de uma prancha de arquitetura.",
        "input_schema": {
            "type": "object",
            "properties": propriedades,
            "required": ["legivel", "tem_carimbo", "transcricao_integral"]
                        + [c.nome for c in CAMPOS_DO_MODELO],
        },
    }


def esquema_fotografia() -> dict[str, Any]:
    """Esquema da leitura de FOTOGRAFIA (§6.1)."""
    return {
        "name": "registrar_fotografia",
        "description": "Descreve uma fotografia de acervo de arquitetura.",
        "input_schema": {
            "type": "object",
            "properties": {
                "tipo_de_imagem": {"type": "string",
                                   "enum": ["fotografia", "negativo", "verso", "cartela", "folha_de_contato"]},
                "assunto": {"type": "string"},
                "enquadramento": {"type": "string",
                                  "enum": ["externa", "interna", "detalhe", "aérea", "maquete", "pessoas"]},
                "elementos_visiveis": {"type": "array", "items": {"type": "string"}},
                "texto_na_imagem": {"type": "array", "items": {"type": "string"}},
                "pessoas": {
                    "type": "array",
                    "description": "Cada pessoa visível, da esquerda para a direita: só traços "
                                   "visíveis, NUNCA nome.",
                    "items": {"type": "object", "properties": {
                        "posicao": {"type": "string"},
                        "barba": {"type": "boolean"}, "bigode": {"type": "boolean"},
                        "oculos": {"type": "boolean"},
                        "outros_tracos": {"type": "string"}}},
                },
                "legenda_proposta": {"type": "string"},
                "confianca": {"type": "number"},
            },
            "required": ["tipo_de_imagem", "assunto", "legenda_proposta", "confianca"],
        },
    }


def esquema_textual() -> dict[str, Any]:
    """Esquema do DOCUMENTO TEXTUAL, série S02 (procedimento padrão, etapa 3)."""
    return {
        "name": "registrar_documento",
        "description": "Transcreve um documento textual (carta, memorial, ficha, planilha, anotação).",
        "input_schema": {
            "type": "object",
            "properties": {
                "tipo_documental": {"type": "string",
                                    "enum": ["carta", "memorial", "programa", "planilha", "ficha",
                                             "contrato", "orçamento", "anotação", "outro"]},
                "remetente": {"type": ["string", "null"]},
                "destinatario": {"type": ["string", "null"]},
                "data": {"type": ["string", "null"], "description": "Como está escrita."},
                "ano": {"type": ["string", "null"], "description": "Quatro dígitos, só se escrito."},
                "assunto": {"type": ["string", "null"],
                            "description": "Assunto ESCRITO no documento (ref., assunto, título)."},
                "projeto_citado": {"type": ["string", "null"]},
                "transcricao": {"type": "string", "description": "Texto integral, literal."},
                "legivel": {"type": "boolean"},
                "confianca": {"type": "number"},
            },
            "required": ["tipo_documental", "transcricao", "legivel", "confianca"],
        },
    }
