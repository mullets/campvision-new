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


CAMPOS: tuple[Campo, ...] = (
    Campo("projeto", "Projeto", "Nome da obra/projeto como escrito no carimbo.", True),
    Campo("cliente", "Cliente", "Proprietário ou contratante.", True),
    Campo("arquiteto", "Arquiteto", "Autor do projeto (pessoa física).", True),
    Campo("escritorio", "Escritório", "Escritório/empresa responsável.", True),
    Campo("endereco", "Endereço", "Endereço da obra, com número se houver.", True),
    Campo("cidade", "Cidade", "Município da obra.", True),
    Campo("uf", "UF", "Sigla do estado, 2 letras.", True),
    Campo("ano", "Ano", "Ano da prancha, 4 dígitos. Só o ano.", False),
    Campo("data", "Data", "Data completa como escrita, se houver.", False),
    Campo("escala", "Escala", "Escala, ex: 1:50, 1:100, S/ESC.", False),
    Campo("tipo", "Tipo", "Tipo de desenho: planta, corte, fachada, situação, "
                          "implantação, detalhe, elétrica, hidráulica, estrutural...", False),
    Campo("titulo_prancha", "Título da prancha", "Título/descrição desta prancha.", False),
    Campo("folha", "Folha", "Número da folha/prancha, ex: 03 ou 03/12.", False),
    Campo("total_folhas", "Total de folhas", "Total de folhas do conjunto, se indicado.", False),
    Campo("desenhista", "Desenhista", "Quem desenhou, se indicado.", False),
    Campo("aprovacao", "Aprovação", "Nº de processo/alvará/aprovação municipal.", False),
    Campo("observacoes", "Observações", "Qualquer texto relevante do carimbo "
                                        "que não coube nos campos acima.", False),
)

CAMPOS_POR_NOME = {c.nome: c for c in CAMPOS}
CAMPOS_DO_PROJETO = tuple(c.nome for c in CAMPOS if c.do_projeto)


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
    # Consenso em código (nucleo/consenso.py): campo -> valor LIDO que ficou fora do consenso do grupo, e as ressalvas anexadas.
    outliers: dict[str, str] = field(default_factory=dict)
    ressalvas: list[str] = field(default_factory=list)
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
        return {
            "arquivo": self.arquivo,
            "valores": self.valores,
            "confiancas": self.confiancas,
            "regiao": list(self.regiao) if self.regiao else None,
            "rotacao": self.rotacao,
            "carimbo_encontrado": self.carimbo_encontrado,
            "passes": self.passes,
            "tokens_entrada": self.tokens_entrada,
            "tokens_saida": self.tokens_saida,
            "erro": self.erro,
            "nota_ia": self.nota_ia,
            "grupo": self.grupo,
            "lidos_originais": self.lidos_originais,
            "ano_do_projeto": self.ano_do_projeto,
            "suspeita_grupo": self.suspeita_grupo,
            "outliers": self.outliers,
            "ressalvas": self.ressalvas,
            "pista_projeto": self.pista_projeto,
            "pista_ano": self.pista_ano,
            "pista_fundo": self.pista_fundo,
            "campos_da_pasta": self.campos_da_pasta,
            "divergencias": self.divergencias,
            "campos_do_info": self.campos_do_info,
            "cache_falhou": self.cache_falhou,
            "fez_segundo_passe": self.fez_segundo_passe,
            "confianca_antes_do_2o": self.confianca_antes_do_2o,
            "ganho_de_resolucao": self.ganho_de_resolucao,
        }

    @classmethod
    def de_dict(cls, dados: dict[str, Any]) -> "Leitura":
        regiao = dados.get("regiao")
        return cls(
            arquivo=dados.get("arquivo", ""),
            valores=dados.get("valores", {}),
            confiancas=dados.get("confiancas", {}),
            regiao=tuple(regiao) if regiao else None,  # type: ignore[arg-type]
            rotacao=dados.get("rotacao", 0),
            carimbo_encontrado=dados.get("carimbo_encontrado", False),
            passes=dados.get("passes", 0),
            tokens_entrada=dados.get("tokens_entrada", 0),
            tokens_saida=dados.get("tokens_saida", 0),
            erro=dados.get("erro", ""),
            nota_ia=dados.get("nota_ia", ""),
            grupo=dados.get("grupo", ""),
            lidos_originais=dados.get("lidos_originais", {}),
            ano_do_projeto=dados.get("ano_do_projeto", ""),
            outliers=dados.get("outliers", {}),
            ressalvas=dados.get("ressalvas", []),
            suspeita_grupo=dados.get("suspeita_grupo", False),
            pista_projeto=dados.get("pista_projeto", ""),
            pista_ano=dados.get("pista_ano", ""),
            pista_fundo=dados.get("pista_fundo", ""),
            campos_da_pasta=dados.get("campos_da_pasta", []),
            divergencias=dados.get("divergencias", []),
            campos_do_info=dados.get("campos_do_info", {}),
            cache_falhou=dados.get("cache_falhou", False),
            fez_segundo_passe=dados.get("fez_segundo_passe", False),
            confianca_antes_do_2o=dados.get("confianca_antes_do_2o", 0.0),
            ganho_de_resolucao=dados.get("ganho_de_resolucao", 0.0),
        )


def esquema_ferramenta() -> dict[str, Any]:
    """Schema JSON entregue à API para forçar saída estruturada.

    Cada campo vem como objeto {valor, confianca} — a confiança por campo é o
    que permite revisar só o que está fraco, em vez de reler o lote inteiro.
    """
    propriedades: dict[str, Any] = {
        "carimbo_encontrado": {
            "type": "boolean",
            "description": "true se você localizou um carimbo/legenda de título "
                           "nesta imagem. false se a imagem não tem carimbo visível.",
        },
        "regiao_carimbo": {
            "type": "array",
            "items": {"type": "number"},
            "minItems": 4,
            "maxItems": 4,
            "description": "Caixa do carimbo em coordenadas normalizadas de 0 a 1 "
                           "na imagem COMO ELA FOI ENVIADA: [x0, y0, x1, y1]. "
                           "Omita se não encontrou.",
        },
        "rotacao": {
            "type": "integer",
            "enum": [0, 90, 180, 270],
            "description": "Graus no sentido horário para o texto do carimbo ficar "
                           "na horizontal e legível. 0 se já está legível.",
        },
        "nota": {
            "type": "string",
            "description": "Uma frase curta sobre a qualidade da leitura ou algo "
                           "atípico. Vazio se nada a dizer.",
        },
    }
    for campo in CAMPOS:
        propriedades[campo.nome] = {
            "type": "object",
            "description": campo.descricao,
            "properties": {
                "valor": {
                    "type": "string",
                    "description": "Transcrição literal do que está escrito. "
                                   "String vazia se o campo não existe no carimbo "
                                   "ou está ilegível.",
                },
                "confianca": {
                    "type": "number",
                    "description": "0 a 1. Sua confiança na transcrição. Use abaixo "
                                   "de 0.6 quando o texto está borrado, cortado ou "
                                   "você teve que adivinhar caracteres.",
                },
            },
            "required": ["valor", "confianca"],
        }

    return {
        "name": "registrar_carimbo",
        "description": "Registra os dados lidos do carimbo de uma prancha de arquitetura.",
        "input_schema": {
            "type": "object",
            "properties": propriedades,
            "required": ["carimbo_encontrado", "rotacao"] + [c.nome for c in CAMPOS],
        },
    }


def esquema_consolidacao() -> dict[str, Any]:
    """Schema da 2ª etapa: normalizar os campos de projeto de um grupo."""
    propriedades = {
        nome: {
            "type": "string",
            "description": f"Grafia canônica de '{CAMPOS_POR_NOME[nome].rotulo}' para "
                           "todo o grupo. String vazia se nenhuma prancha trouxe "
                           "informação confiável.",
        }
        for nome in CAMPOS_DO_PROJETO
    }
    propriedades["ano_do_projeto"] = {
        "type": "string",
        "description": "Ano que melhor representa o conjunto (4 dígitos). "
                       "Vazio se indeterminado.",
    }
    propriedades["justificativa"] = {
        "type": "string",
        "description": "Uma ou duas frases explicando as escolhas, citando as "
                       "divergências que você resolveu.",
    }
    propriedades["pranchas_fora_do_grupo"] = {
        "type": "array",
        "items": {"type": "string"},
        "description": "Nomes de arquivo que aparentam NÃO pertencer a este projeto. "
                       "Lista vazia se todas pertencem.",
    }
    return {
        "name": "consolidar_projeto",
        "description": "Define os dados canônicos de um projeto a partir das leituras "
                       "individuais das suas pranchas.",
        "input_schema": {
            "type": "object",
            "properties": propriedades,
            "required": list(CAMPOS_DO_PROJETO) + ["ano_do_projeto", "justificativa"],
        },
    }
