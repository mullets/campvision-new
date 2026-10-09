"""Cliente HTTP do painel de administração (contrato: docs/contrato-painel.md).

Regras:
- Os arquivos no QNAP são a fonte da verdade. HTTP só informa e acelera.
- Nada aqui pode travar o vigia: timeout curto, nenhuma exceção vaza.
- Avisos que não chegaram ficam numa fila em disco e são reenviados; basta o
  mais recente de cada projeto.
- Só `/reservar` é bloqueante para o fluxo (sem número P não há projeto novo),
  e mesmo assim o lote só ESPERA — nunca recebe código inventado.

Sem dependência externa: urllib da biblioteca padrão.
"""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

_log = logging.getLogger("cv2.painel")


class Painel:
    def __init__(self, url: str, token: str = "", pasta_estado: Path | None = None,
                 timeout: float = 8.0) -> None:
        self.url = (url or "").rstrip("/")
        self.token = token
        self.timeout = timeout
        self.fila = (pasta_estado / "painel_pendentes.json") if pasta_estado else None
        self.ultimo_erro = ""
        self.ultima_reserva: dict = {"pendente": False, "existente": False, "mensagem": ""}
        self.ultimo_ok: str = ""

    @property
    def ligado(self) -> bool:
        return bool(self.url)

    # ------------------------------------------------------------ baixo nível
    def _chamar(self, metodo: str, caminho: str, corpo: dict | None = None):
        """Devolve (status_http, json|None). Status 0 = sem conexão."""
        if not self.ligado:
            return 0, None
        dados = json.dumps(corpo, ensure_ascii=False).encode("utf-8") if corpo is not None else None
        pedido = urllib.request.Request(self.url + caminho, data=dados, method=metodo)
        pedido.add_header("Accept", "application/json")
        if dados is not None:
            pedido.add_header("Content-Type", "application/json; charset=utf-8")
        if self.token:
            pedido.add_header("X-Camp-Token", self.token)
        try:
            with urllib.request.urlopen(pedido, timeout=self.timeout) as resposta:
                bruto = resposta.read().decode("utf-8") or "null"
                self.ultimo_ok = datetime.now().isoformat(timespec="seconds")
                self.ultimo_erro = ""
                try:
                    return resposta.status, json.loads(bruto)
                except json.JSONDecodeError:
                    return resposta.status, None
        except urllib.error.HTTPError as erro:
            self.ultimo_erro = f"{metodo} {caminho}: HTTP {erro.code}"
            try:
                return erro.code, json.loads(erro.read().decode("utf-8") or "null")
            except (json.JSONDecodeError, OSError):
                return erro.code, None
        except (urllib.error.URLError, OSError, TimeoutError) as erro:
            self.ultimo_erro = f"{metodo} {caminho}: {getattr(erro, 'reason', erro)}"
            return 0, None

    # ------------------------------------------------------------ contrato
    def contexto(self, fundo: str = "") -> dict | None:
        caminho = "/api/estacoes/contexto" + (f"?fundo={fundo}" if fundo else "")
        status, dados = self._chamar("GET", caminho)
        return dados if status == 200 else None

    def reservar(self, fundo_codigo: str, titulo: str, chave_reserva: str, **extra) -> str | None:
        """Reserva o projeto no painel e devolve o código (F002-P0002) ou None.

        A mesma chave devolve o mesmo projeto: repetir depois de timeout é seguro.
        """
        corpo = {"fundo_codigo": fundo_codigo, "titulo": titulo, "chave_reserva": chave_reserva}
        corpo.update({k: v for k, v in extra.items() if v not in (None, "")})
        status, dados = self._chamar("POST", "/api/estacoes/projetos/reservar", corpo)
        # Contrato §6.2 do painel: 202 = achou projeto parecido e abriu decisão para
        # uma pessoa (sem código; tentar de novo com a mesma chave); 200 com
        # existente=true = "é o mesmo projeto", o material entra no que já existe.
        self.ultima_reserva = {"pendente": False, "existente": False, "mensagem": ""}
        if status == 202 and isinstance(dados, dict):
            mensagem = str(dados.get("mensagem") or "aguardando decisão no painel (projeto parecido)")
            self.ultima_reserva = {"pendente": True, "existente": False, "mensagem": mensagem,
                                   "decisao_id": dados.get("decisao_id")}
            self.ultimo_erro = mensagem
            _log.info("Reserva pendente: %s", mensagem)
            return None
        if status not in (200, 201) or not isinstance(dados, dict):
            if status:
                _log.warning("Reserva recusada (%s): %s", status, dados)
            return None
        codigo = dados.get("codigo") or (dados.get("projeto") or {}).get("codigo")
        if not codigo and dados.get("numero"):
            codigo = f"{fundo_codigo}-P{int(dados['numero']):04d}"
        self.ultima_reserva["existente"] = bool(dados.get("existente"))
        return str(codigo) if codigo else None

    def pedidos_releitura(self, estacao_id: str) -> list[dict]:
        """Pedidos de releitura abertos no painel. Painel sem o recurso (404) = lista vazia."""
        status, dados = self._chamar(
            "GET", f"/api/estacoes/pedidos-releitura?estacao={estacao_id}&estacao_id={estacao_id}")
        if status != 200:
            return []
        if isinstance(dados, dict):
            dados = dados.get("pedidos") or []
        return [d for d in dados if isinstance(d, dict) and d.get("id")] if isinstance(dados, list) else []

    def releitura_iniciada(self, pedido_id, estacao_id: str) -> bool:
        """True = pode ler. 409 = pedido já encerrado (cancelado): NÃO ler."""
        status, _ = self._chamar("POST", f"/api/estacoes/pedidos-releitura/{pedido_id}/iniciado",
                                 {"estacao": estacao_id})
        if status == 409:
            return False
        return True  # painel antigo sem a rota (404) ou fora do ar: lê assim mesmo

    def releitura_concluida(self, pedido_id, resumo: dict, ok: bool = True, mensagem: str = "") -> bool:
        corpo = {"ok": ok, "mensagem": (mensagem or "")[:500], **resumo}
        status, _ = self._chamar("POST", f"/api/estacoes/pedidos-releitura/{pedido_id}/concluido", corpo)
        return 200 <= status < 300

    def heartbeat(self, dados: dict) -> bool:
        status, _ = self._chamar("POST", "/api/estacoes/heartbeat", dados)
        return 200 <= status < 300

    def aviso(self, codigo: str, pasta: str, status_projeto: str) -> bool:
        """Pede ao painel para reler só esta pasta. Falhou: guarda para depois."""
        corpo = {"codigo": codigo, "pasta": pasta, "status": status_projeto,
                 "em": datetime.now().isoformat(timespec="seconds")}
        status, _ = self._chamar("POST", "/api/campvision/aviso", corpo)
        if 200 <= status < 300:
            return True
        self._enfileirar(corpo)
        return False

    # ------------------------------------------------------------ fila
    def _ler_fila(self) -> dict:
        if not self.fila or not self.fila.exists():
            return {}
        try:
            dados = json.loads(self.fila.read_text(encoding="utf-8"))
            return dados if isinstance(dados, dict) else {}
        except (OSError, json.JSONDecodeError):
            return {}

    def _gravar_fila(self, fila: dict) -> None:
        if not self.fila:
            return
        try:
            self.fila.parent.mkdir(parents=True, exist_ok=True)
            self.fila.write_text(json.dumps(fila, ensure_ascii=False, indent=1), encoding="utf-8")
        except OSError as erro:
            _log.warning("Fila de avisos não gravada: %s", erro)

    def _enfileirar(self, corpo: dict) -> None:
        fila = self._ler_fila()
        fila[corpo["codigo"]] = corpo  # só o mais recente de cada projeto
        self._gravar_fila(fila)

    def pendentes(self) -> int:
        return len(self._ler_fila())

    def reenviar(self) -> int:
        """Tenta de novo os avisos guardados. Devolve quantos entraram."""
        fila = self._ler_fila()
        if not fila or not self.ligado:
            return 0
        enviados = 0
        for codigo, corpo in list(fila.items()):
            status, _ = self._chamar("POST", "/api/campvision/aviso", corpo)
            if 200 <= status < 300:
                fila.pop(codigo)
                enviados += 1
            elif status == 0:
                break  # painel fora: não insiste nos outros agora
        self._gravar_fila(fila)
        return enviados


class PainelDesligado(Painel):
    """Quando não há URL configurada. Tudo vira no-op."""

    def __init__(self) -> None:
        super().__init__("")
