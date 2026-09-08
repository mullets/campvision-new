"""Configuração do CAMP Vision 2.

Tudo que o usuário pode ajustar mora aqui, com defaults sensatos. O arquivo
config.json fica ao lado do executável e é criado na primeira execução.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field, asdict, fields
from pathlib import Path
from typing import Any

# Suba o número a cada release. Sem isso não dá para saber qual versão está
# rodando numa máquina — foi assim que um caminho errado sobreviveu a três
# atualizações do código.
VERSAO_BUILD = "2026-09-08-05"

_log = logging.getLogger("cv2.config")

# Modelo padrão. Sonnet 5 é o melhor custo/qualidade para leitura de carimbo:
# lê manuscrito, texto girado e tabela institucional sem detector nenhum.
MODELO_PADRAO = "claude-sonnet-5"

# Tabela de preços (USD por milhão de tokens) usada só para ESTIMAR custo na UI.
# Não é cobrança: se a Anthropic mudar o preço, ajuste aqui.
PRECOS_USD_POR_MTOK: dict[str, tuple[float, float]] = {
    "claude-sonnet-5": (2.0, 10.0),
    "claude-opus-5": (5.0, 25.0),
    "claude-haiku-4-5": (1.0, 5.0),
}


@dataclass
class Config:
    """Parâmetros de execução do lote."""

    # --- API ---
    modelo: str = MODELO_PADRAO
    # A chave NUNCA é gravada no config.json por padrão; lida de ANTHROPIC_API_KEY.
    # Preencha aqui só se você quiser mesmo persistir em disco.
    api_key: str = ""
    max_tentativas_api: int = 4
    timeout_segundos: int = 120

    # --- Imagem ---
    # Lado maior enviado à API. 1568px é o limite acima do qual a API reduz
    # sozinha; mandar mais que isso só gasta banda, não melhora leitura.
    lado_maximo_envio: int = 1568
    qualidade_jpeg_envio: int = 85
    # Margem aplicada em volta da região do carimbo ao recortar (fração da caixa).
    margem_recorte: float = 0.06
    extensoes: tuple[str, ...] = (".jpg", ".jpeg", ".tif", ".tiff", ".png")

    # --- Estratégia de leitura ---
    # Abaixo desta confiança média, faz o 2º passe com recorte em alta resolução.
    confianca_minima_para_aceitar: float = 0.75
    # Reaproveita a região do carimbo achada na prancha anterior da mesma pasta.
    usar_cache_de_regiao: bool = True
    # Consolida os campos por projeto no fim do lote (1 chamada de texto por grupo).
    consolidar_por_projeto: bool = True
    # A pasta (projeto/ano) preenche campo que o carimbo não deu. A pasta NUNCA
    # é mostrada ao modelo: entra depois da leitura, marcada como outra fonte.
    usar_pasta_como_pista: bool = True

    # --- Lote ---
    trabalhadores: int = 4
    # Retoma de onde parou usando o checkpoint .jsonl; desligue para reprocessar tudo.
    retomar_checkpoint: bool = True

    # --- Identidade institucional (vai DENTRO de cada imagem) ---
    # Preencha antes de publicar qualquer coisa: o app avisa se estiver vazio.
    identidade_nome: str = "CAMP - Casa da Arquitetura Moderna Paulista"
    identidade_site: str = ""
    identidade_licenca: str = "Todos os direitos reservados"
    identidade_contato: str = ""

    # --- Vigia (modo automático) ---
    # Raiz montada por SMB onde os projetos chegam do Windows/QNAP.
    pasta_vigiada: str = ""
    intervalo_varredura_segundos: int = 60
    # Só processa pasta com status.json marcado como pronta. Desligue para
    # processar qualquer pasta com imagens e sem catalogacao.xlsx.
    exigir_status_json: bool = True
    # Pasta com imagens e SEM status.json ganha um, criado pelo vigia.
    criar_status_ausente: bool = True
    status_pronto: str = "enviado_windows"
    status_concluido: str = "campvision_concluido"
    # Marcador da fase nova de organização. Fica numa chave PRÓPRIA do
    # status.json (`fase`), não no `status` — assim o watcher do QNAP, que
    # procura por status_concluido, continua reconhecendo o arquivo.
    fase_organizacao: str = "organizado_v2"
    # Busca recursiva: projeto pode estar em subpasta de subpasta.
    profundidade_maxima: int = 5
    # Onde a planilha única do acervo é escrita, relativo à raiz vigiada.
    pasta_acervo: str = "_catalogacao"
    # Aplica a Fase 2 sozinho ao terminar a leitura. Padrão FALSE de propósito:
    # o ponto do redesenho é você revisar a planilha antes de mexer em arquivo.
    aplicar_automaticamente: bool = False

    # --- Auto-atualização pelo GitHub ---
    auto_atualizar: bool = True
    # Só atualiza entre projetos, nunca no meio de um lote.
    intervalo_atualizacao_minutos: int = 60

    # --- Relatório diário ---
    hora_relatorio: str = "18:00"
    email_ativo: bool = False
    email_para: str = ""
    email_de: str = ""
    smtp_servidor: str = ""
    smtp_porta: int = 587
    smtp_usuario: str = ""
    # Senha lida de CAMPVISION_SMTP_SENHA no ambiente; não fica no config.json.

    @classmethod
    def carregar(cls, caminho: Path) -> "Config":
        """Lê o config.json. Erro de sintaxe é RUIDOSO, nunca silencioso."""
        cfg = cls()
        if not caminho.exists():
            return cfg
        try:
            dados = json.loads(caminho.read_text(encoding="utf-8"))
        except json.JSONDecodeError as erro:
            _log.error(
                "config.json inválido (%s) — usando valores padrão. "
                "Corrija o arquivo em %s", erro, caminho,
            )
            cfg.erro_leitura = f"{erro}"  # type: ignore[attr-defined]
            return cfg
        validos = {f.name for f in fields(cls)}
        for chave, valor in dados.items():
            if chave in validos:
                setattr(cfg, chave, tuple(valor) if chave == "extensoes" else valor)
            else:
                _log.warning("config.json: chave desconhecida ignorada: %s", chave)

        # Conserta caminho gravado por versão antiga, que colava a URL smb://
        # no diretório atual em vez de traduzir para /Volumes.
        if "smb:" in cfg.pasta_vigiada or "cifs:" in cfg.pasta_vigiada:
            from .caminho import de_url_smb

            corrigido = str(de_url_smb(cfg.pasta_vigiada))
            _log.warning(
                "pasta_vigiada estava malformada e foi corrigida:\n  antes: %s\n  agora: %s",
                cfg.pasta_vigiada, corrigido,
            )
            cfg.pasta_vigiada = corrigido
        return cfg

    def salvar(self, caminho: Path, incluir_chave: bool = False) -> None:
        dados: dict[str, Any] = asdict(self)
        dados["extensoes"] = list(self.extensoes)
        if not incluir_chave:
            dados.pop("api_key", None)
        caminho.write_text(
            json.dumps(dados, indent=2, ensure_ascii=False), encoding="utf-8"
        )

    @classmethod
    def modelo_json(cls) -> str:
        """Config completo com os padrões, para servir de ponto de partida.

        Gerado dos campos da dataclass, nunca escrito à mão: assim o
        config.exemplo.json não envelhece quando alguém acrescenta uma opção.
        A chave de API fica de fora de propósito — ela mora no ambiente.
        """
        padroes = cls()
        dados: dict[str, Any] = {}
        for campo in fields(cls):
            if campo.name == "api_key":
                continue
            valor = getattr(padroes, campo.name)
            dados[campo.name] = list(valor) if isinstance(valor, tuple) else valor
        return json.dumps(dados, indent=2, ensure_ascii=False)

    @classmethod
    def criar_se_faltar(cls, caminho: Path) -> bool:
        """Escreve o config padrão se não houver um. Nunca sobrescreve."""
        if caminho.exists():
            return False
        caminho.parent.mkdir(parents=True, exist_ok=True)
        caminho.write_text(cls.modelo_json() + "\n", encoding="utf-8")
        _log.info("Config criado em %s", caminho)
        return True

    def identidade(self):
        """Identidade institucional para os metadados."""
        from .metadados import Identidade

        return Identidade(
            nome=self.identidade_nome,
            site=self.identidade_site,
            licenca=self.identidade_licenca,
            contato=self.identidade_contato,
        )

    def custo_estimado_usd(self, tokens_entrada: int, tokens_saida: int) -> float:
        entrada, saida = PRECOS_USD_POR_MTOK.get(self.modelo, (0.0, 0.0))
        return tokens_entrada / 1e6 * entrada + tokens_saida / 1e6 * saida
