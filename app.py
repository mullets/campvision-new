"""CAMP Vision 2 — janela principal.

Duas fases, dois botões:
  Fase 1 — Ler: lê os carimbos e escreve a planilha. Não toca nos arquivos.
  Fase 2 — Aplicar: pega a planilha que você revisou e organiza os arquivos.
"""

from __future__ import annotations

import logging
import queue
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from nucleo import grupos, lote, planilha, registro
from nucleo.aplicar import executar as aplicar_executar, planejar
from nucleo.config import VERSAO_BUILD, Config
from nucleo.visao import ClienteAnthropic

PASTA_APP = Path.home() / ".campvision2"
CAMINHO_CONFIG = PASTA_APP / "config.json"


class FilaDeLog(logging.Handler):
    """Manda o log para a caixa de texto sem travar a UI."""

    def __init__(self, fila: queue.Queue) -> None:
        super().__init__()
        self.fila = fila

    def emit(self, registro_log: logging.LogRecord) -> None:
        try:
            self.fila.put_nowait(self.format(registro_log))
        except queue.Full:  # pragma: no cover
            pass


class Janela(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        PASTA_APP.mkdir(parents=True, exist_ok=True)
        self.config_app = Config.carregar(CAMINHO_CONFIG)
        self.title(f"CAMP Vision 2 — build {VERSAO_BUILD}")
        self.geometry("880x620")
        self.minsize(760, 520)

        self.pasta = tk.StringVar()
        self.status = tk.StringVar(value="Escolha a pasta com as pranchas.")
        self.modelo = tk.StringVar(value=self.config_app.modelo)
        self.trabalhadores = tk.IntVar(value=self.config_app.trabalhadores)
        self.consolidar = tk.BooleanVar(value=self.config_app.consolidar_por_projeto)
        self.cancelar = threading.Event()
        self.fila_log: queue.Queue = queue.Queue(maxsize=2000)
        self._rodando = False

        self._montar()
        logger = registro.configurar(PASTA_APP / "campvision2.log")
        manipulador = FilaDeLog(self.fila_log)
        manipulador.setFormatter(logging.Formatter("%(asctime)s %(message)s", "%H:%M:%S"))
        manipulador.addFilter(registro.FiltroArquivo())
        logger.addHandler(manipulador)
        self.after(120, self._drenar_log)

    # ------------------------------------------------------------------ UI
    def _montar(self) -> None:
        topo = ttk.Frame(self, padding=10)
        topo.pack(fill="x")
        ttk.Label(topo, text="Pasta:").pack(side="left")
        ttk.Entry(topo, textvariable=self.pasta).pack(side="left", fill="x", expand=True, padx=6)
        ttk.Button(topo, text="Escolher…", command=self._escolher).pack(side="left")

        opcoes = ttk.LabelFrame(self, text="Opções", padding=10)
        opcoes.pack(fill="x", padx=10)
        ttk.Label(opcoes, text="Modelo:").grid(row=0, column=0, sticky="w")
        ttk.Combobox(
            opcoes, textvariable=self.modelo, width=22,
            values=["claude-sonnet-5", "claude-opus-5", "claude-haiku-4-5"],
        ).grid(row=0, column=1, padx=6)
        ttk.Label(opcoes, text="Pranchas em paralelo:").grid(row=0, column=2, sticky="w", padx=(16, 0))
        ttk.Spinbox(opcoes, from_=1, to=12, width=4, textvariable=self.trabalhadores).grid(row=0, column=3, padx=6)
        ttk.Checkbutton(
            opcoes, text="Consolidar campos por projeto no fim", variable=self.consolidar
        ).grid(row=0, column=4, padx=(16, 0))

        botoes = ttk.Frame(self, padding=10)
        botoes.pack(fill="x")
        self.botao_ler = ttk.Button(botoes, text="Fase 1 — Ler carimbos", command=self._iniciar_leitura)
        self.botao_ler.pack(side="left")
        self.botao_aplicar = ttk.Button(botoes, text="Fase 2 — Aplicar planilha…", command=self._aplicar)
        self.botao_aplicar.pack(side="left", padx=8)
        self.botao_cancelar = ttk.Button(botoes, text="Cancelar", command=self._cancelar, state="disabled")
        self.botao_cancelar.pack(side="left")

        self.barra = ttk.Progressbar(self, mode="determinate")
        self.barra.pack(fill="x", padx=10)
        ttk.Label(self, textvariable=self.status, anchor="w").pack(fill="x", padx=10, pady=(6, 0))

        quadro = ttk.LabelFrame(self, text="Log", padding=6)
        quadro.pack(fill="both", expand=True, padx=10, pady=10)
        self.caixa_log = tk.Text(quadro, height=14, wrap="none", state="disabled")
        rolagem = ttk.Scrollbar(quadro, command=self.caixa_log.yview)
        self.caixa_log.configure(yscrollcommand=rolagem.set)
        rolagem.pack(side="right", fill="y")
        self.caixa_log.pack(fill="both", expand=True)

    def _escolher(self) -> None:
        caminho = filedialog.askdirectory(title="Pasta com as pranchas digitalizadas")
        if caminho:
            self.pasta.set(caminho)

    def _drenar_log(self) -> None:
        linhas = []
        while True:
            try:
                linhas.append(self.fila_log.get_nowait())
            except queue.Empty:
                break
        if linhas:
            self.caixa_log.configure(state="normal")
            self.caixa_log.insert("end", "\n".join(linhas) + "\n")
            self.caixa_log.see("end")
            self.caixa_log.configure(state="disabled")
        self.after(120, self._drenar_log)

    def _cancelar(self) -> None:
        self.cancelar.set()
        self.status.set("Cancelando… terminando as pranchas já em andamento.")

    # -------------------------------------------------------------- Fase 1
    def _iniciar_leitura(self) -> None:
        if self._rodando:
            return
        pasta = Path(self.pasta.get())
        if not pasta.is_dir():
            messagebox.showerror("CAMP Vision 2", "Escolha uma pasta válida.")
            return

        self.config_app.modelo = self.modelo.get()
        self.config_app.trabalhadores = int(self.trabalhadores.get())
        self.config_app.consolidar_por_projeto = bool(self.consolidar.get())
        self.config_app.salvar(CAMINHO_CONFIG)

        try:
            cliente = ClienteAnthropic(self.config_app)
        except RuntimeError as erro:
            messagebox.showerror("CAMP Vision 2", str(erro))
            return

        self.cancelar.clear()
        self._rodando = True
        self.botao_ler.configure(state="disabled")
        self.botao_cancelar.configure(state="normal")
        threading.Thread(target=self._rodar_leitura, args=(pasta, cliente), daemon=True).start()

    def _rodar_leitura(self, pasta: Path, cliente) -> None:
        try:
            def progresso(p: lote.Progresso) -> None:
                self.barra.configure(maximum=max(1, p.total), value=p.concluidos)
                custo = self.config_app.custo_estimado_usd(p.tokens_entrada, p.tokens_saida)
                self.status.set(
                    f"{p.concluidos}/{p.total} — carimbo em {p.com_carimbo}, "
                    f"{p.erros} erro(s) — US$ {custo:.2f} — {p.arquivo_atual}"
                )

            resultado = lote.executar(
                pasta, self.config_app, cliente, ao_progredir=progresso, cancelar=self.cancelar
            )
            t_in = resultado.progresso.tokens_entrada
            t_out = resultado.progresso.tokens_saida

            if self.config_app.consolidar_por_projeto and resultado.leituras:
                self.status.set("Consolidando campos por projeto…")
                _, c_in, c_out = grupos.consolidar(resultado.leituras, cliente, modo=self.config_app.consolidacao)
                t_in += c_in
                t_out += c_out

            custo = self.config_app.custo_estimado_usd(t_in, t_out)
            saida = planilha.escrever_csv(resultado.leituras, pasta / "catalogacao.csv")
            planilha.escrever_relatorio(resultado.leituras, pasta / "relatorio.txt", custo)
            self.status.set(f"Pronto. Planilha: {saida.name} — custo estimado US$ {custo:.2f}")
        except Exception as erro:  # noqa: BLE001
            logging.getLogger("cv2").exception("Falha no lote")
            self.status.set(f"Falhou: {erro}")
        finally:
            self._rodando = False
            self.botao_ler.configure(state="normal")
            self.botao_cancelar.configure(state="disabled")

    # -------------------------------------------------------------- Fase 2
    def _aplicar(self) -> None:
        pasta = Path(self.pasta.get())
        if not pasta.is_dir():
            messagebox.showerror("CAMP Vision 2", "Escolha a pasta das pranchas primeiro.")
            return
        caminho = filedialog.askopenfilename(
            title="Planilha revisada",
            initialdir=str(pasta),
            filetypes=[("Planilha CSV", "*.csv")],
        )
        if not caminho:
            return
        acoes = planejar(pasta, Path(caminho))
        if not acoes:
            messagebox.showwarning("CAMP Vision 2", "Nenhuma linha utilizável na planilha.")
            return
        amostra = "\n".join(m for m in aplicar_executar(acoes[:5], simular=True))
        confirma = messagebox.askyesno(
            "Confirmar",
            f"{len(acoes)} prancha(s) serão COPIADAS para a pasta catalogada "
            f"(os originais ficam onde estão).\n\nPrimeiras:\n{amostra}\n\nProsseguir?",
        )
        if not confirma:
            return
        mensagens = aplicar_executar(acoes, simular=False, identidade=self.config_app.identidade())
        for m in mensagens:
            logging.getLogger("cv2.aplicar").info(m)
        self.status.set(f"Aplicado: {len(acoes)} prancha(s) organizadas.")


def main() -> None:
    Janela().mainloop()


if __name__ == "__main__":
    main()
