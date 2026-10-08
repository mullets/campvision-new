"""Passada 4 (docs/metodo-de-leitura.md §5): tipo de desenho por regras e filtro "documentação não é obra".

Os casos vêm dos documentos do projeto (regra validada em produção no F023) e dos erros que o back-test pegou.
"""
import unittest

from nucleo import derivacao as d


class TipoDeDesenho(unittest.TestCase):
    def igual(self, titulo, esperado):
        self.assertEqual(d.tipo_de_desenho(titulo), esperado, f"{titulo!r}")

    def test_regra_de_supressao_implantacao_vence_planta(self):
        self.igual("Planta de Situação", "Implantação")        # não "Implantação / Planta"
        self.igual("Implantação e planta térrea", "Implantação")

    def test_erros_que_o_backtest_pegou_antes_de_gravar(self):
        self.igual("Detalhes do Piso do Palco", "Detalhe")      # `piso` puxava Planta: gatilho removido
        self.igual("Planta de quadras / loteamento", "Implantação")   # `loteamento` não puxava Implantação: gatilho acrescentado

    def test_abstencao_nunca_chuta(self):
        for titulo in ("Prancha 12", "Documento 87", "Luncheonete", "Cabine de Transformação",
                       "Estação de Tratamento de Água", "", None, "   "):
            self.igual(titulo, None)

    def test_asterisco_e_prefixo_e_sem_asterisco_e_palavra_inteira(self):
        self.igual("Esquadrias de Ferro", None)   # gatilho `esquadria` (singular, palavra inteira): fica entre os 254 sem tipo
        self.igual("Esquadria de ferro", "Detalhe")
        self.igual("Detalhes construtivos", "Detalhe")          # `detalhe*` pega o plural
        self.igual("Fotos da obra", "Fotografia")               # `foto, fotos` são listadas separadas
        self.igual("Foto", "Fotografia")
        self.igual("Fotômetro", None)                           # `foto` não pega "fotômetro" (palavra inteira)
        self.igual("Formas do 2º pavimento", "Planta / Estrutura")   # `formas` (Estrutura) + `pavimento` (Planta)

    def test_ordem_canonica_e_limite_de_tres(self):
        self.igual("Cortes e Fachadas (hotel)", "Corte / Fachada")
        self.igual("Fachadas, cortes e planta", "Planta / Corte / Fachada")      # ordem canônica, não a do texto
        self.igual("Planta, Corte, Fachada e Detalhe", "Planta / Corte / Fachada")   # 4 detectados: só 3 (os primeiros da ordem)

    def test_ignora_acento_e_caixa(self):
        self.igual("ELEVAÇÃO FRONTAL", "Elevação")
        self.igual("Elevacao frontal", "Elevação")
        self.igual("INSTALAÇÕES ELÉTRICAS E HIDRÁULICAS", "Instalações")

    def test_cada_um_dos_15_detectores_dispara_pela_primeira_palavra(self):
        casos = {"Implantação": "Implantação geral", "Planta": "Planta baixa", "Corte": "Corte AA", "Elevação": "Elevação sul",
                 "Fachada": "Fachada principal", "Detalhe": "Detalhe da escada", "Estrutura": "Estrutura metálica",
                 "Instalações": "Instalações sanitárias", "Mobiliário": "Mobiliário fixo", "Perspectiva": "Perspectiva externa",
                 "Croqui": "Croqui inicial", "Fotografia": "Fotografia aérea", "Levantamento": "Levantamento topográfico",
                 "Documento": "Memorial descritivo", "Tabela": "Quadro de áreas"}
        self.assertEqual(len(d.ORDEM), 15)
        self.assertEqual(set(casos), set(d.ORDEM))
        for termo, titulo in casos.items():
            self.assertIn(termo, d.tipos_detectados(titulo), titulo)

    def test_termos_com_mais_de_uma_palavra(self):
        self.igual("Ar  condicionado", "Instalações")
        self.igual("Curvas de nível", "Levantamento")
        self.igual("Quadro de áreas", "Tabela")

    def test_gatilhos_e_ordem_batem(self):
        self.assertEqual(set(d.GATILHOS), set(d.ORDEM))


class DocumentacaoNaoEObra(unittest.TestCase):
    def test_erro_publicado_que_motivou_o_filtro(self):
        self.assertTrue(d.e_ficha_de_documentacao("Documentos do escritório"))       # F023-P0181

    def test_titulos_de_documento(self):
        for t in ("Currículo", "Cadernos de obra", "Recortes de jornal", "Correspondência 1975", "Memorial descritivo",
                  "Contrato de projeto", "Pasta de clientes"):
            self.assertTrue(d.e_ficha_de_documentacao(t), t)

    def test_so_a_lista_da_especificacao(self):
        # "Documentação" não está na lista da §5.5; estender a regra é decisão do Rafa (e exige back-test).
        self.assertFalse(d.e_ficha_de_documentacao("Documentação geral"))

    def test_obra_nao_e_documentacao(self):
        for t in ("Casa de Veraneio", "Residência Banco Cidade - Agência Matriz", "Edifício Pastorinho", "Santa Casa de Misericórdia", "", None):
            self.assertFalse(d.e_ficha_de_documentacao(t), t)


if __name__ == "__main__":
    unittest.main()
