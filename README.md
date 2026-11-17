# docs-agent

Pipeline em Python, LangChain e LangGraph que lê PDFs escaneados (notas fiscais, contratos de prestação de serviços e relatórios de manutenção), classifica cada documento, extrai os campos em JSON estrito e grava os resultados consolidados em JSON e CSV. Foi construído para o Desafio 2 do processo de Engenheiro de IA Pleno da Franq, sobre os 50 PDFs do anexo.

## Como funciona

Cada documento passa por OCR (Tesseract, idioma português), classificação, extração por LLM com saída estruturada, normalização de datas e valores e validação determinística. Um grafo LangGraph decide o caminho de cada arquivo:

| Situação | Resultado |
| --- | --- |
| Sem pendências | `ok` |
| Erro de extração (campo obrigatório ausente, número que não aparece no texto do OCR) | Uma nova tentativa com o modelo maior e a mensagem do erro no prompt; se persistir, `needs_review` |
| Anomalia do próprio documento (por exemplo, soma dos itens diferente do total impresso) | `needs_review`, sem nova tentativa |
| Tipo desconhecido ou confiança de classificação abaixo de 0,7 | `needs_review` |
| Falha de OCR ou do modelo | `failed` |

Cada arquivo é processado de forma isolada, então um documento problemático nunca derruba o lote.

## Requisitos

Python 3.11 ou superior e o Tesseract com o idioma português:

- Ubuntu: `sudo apt install tesseract-ocr tesseract-ocr-por`
- macOS: `brew install tesseract tesseract-lang`
- Windows: o instalador do Tesseract mais o arquivo `por.traineddata` na pasta `tessdata`

O projeto foi testado no Ubuntu (Tesseract 4.1.1) e no Windows (Tesseract 5.4.0). No macOS os passos são os mesmos do Linux, mas não foram testados.

## Instalação e execução

```bash
git clone https://github.com/ncsrdgs/docs-agent.git
cd docs-agent
python -m venv .venv
source .venv/bin/activate   # no Windows (PowerShell): .venv\Scripts\Activate.ps1
pip install -e .
cp .env.example .env        # edite com as suas credenciais (veja abaixo)
python -m pipeline.main
```

O `requirements.lock` traz as versões exatas usadas nos testes (`pip install -r requirements.lock` antes do `pip install -e .` reproduz o ambiente).

O comando principal aceita `--entrada` (pasta dos PDFs, padrão `data/raw`), `--saida` (padrão `data/out`) e `--workers` (documentos em paralelo, padrão 4). Cada etapa também roda isolada, para depuração: `python -m pipeline.ocr`, `python -m pipeline.extract 001_rljd` e `python -m pipeline.graph 001_rljd 005_6zwp`.

## Configuração (`.env`)

| Variável | Descrição |
| --- | --- |
| `GOOGLE_CLOUD_PROJECT` | Projeto do Google Cloud. Quando definida, o pipeline usa o Vertex AI |
| `GOOGLE_CLOUD_LOCATION` | Região do Vertex. Use `global`: neste projeto os modelos só responderam nela |
| `GOOGLE_APPLICATION_CREDENTIALS` | Caminho da chave JSON de uma conta de serviço com o papel "Usuário da Vertex AI" |
| `GOOGLE_API_KEY` | Alternativa: chave do AI Studio, usada quando `GOOGLE_CLOUD_PROJECT` não está definida |
| `GEMINI_MODEL` | Modelo barato, usado em todos os documentos (padrão `gemini-3.5-flash-lite`) |
| `GEMINI_MODEL_FORTE` | Modelo maior, usado só nas novas tentativas (padrão `gemini-3.5-flash`) |
| `TESSERACT_CMD` | Caminho do executável do Tesseract (necessário no Windows) |
| `LLM_CACHE` | `0` desliga o cache de respostas do LLM (padrão: ligado) |
| `LLM_CACHE_DIR` | Pasta do cache de respostas (padrão `data/cache/llm`) |

## Saída e resultados

A saída fica em `data/out/`:

- `results.json`: um registro por arquivo, com status, motivo, avisos, dados normalizados, confiança do OCR, modelo usado, tokens, latência e tentativas.
- `notas_fiscais.csv`, `notas_fiscais_itens.csv`, `contratos.csv` e `relatorios_manutencao.csv`: um CSV por tipo, com as colunas `status` e `motivo`. Os documentos em revisão também entram, para que os dados extraídos não se percam.
- `summary.json`: contagens, tokens e a lista de pendências.

Os resultados da execução sobre os 50 documentos estão versionados nessa pasta: **38 `ok` e 12 `needs_review`, nenhuma falha, em 66 segundos** com 4 documentos em paralelo. Onze dos 12 em revisão são notas fiscais cuja soma dos itens difere do total impresso (dois pares são o mesmo registro em templates diferentes). O outro, `014_yfas`, teve a quantidade de um item lida pelo OCR como "Ki" e ficou em revisão com esse motivo, em vez de o sistema inventar o valor.

## Acurácia medida

O gabarito (`data/gold/gold.json`) tem 12 documentos (4 notas, 4 contratos e 4 relatórios, incluindo templates diferentes do mesmo registro). Foi transcrito à mão a partir dos PDFs, sem consultar a saída do pipeline. O comando `python eval/evaluate.py` compara `data/out/results.json` com ele, campo a campo.

Na última rodada: 12/12 classificações corretas, 155 de 156 campos corretos (99,4%) e 11 de 12 documentos 100% corretos. Dos 9 documentos com status `ok`, nenhum tinha erro real (precisão do `ok` de 100%).

O único erro de extração foi o item 2 da `014_yfas`, sem quantidade, porque o Tesseract 5.4 no Windows leu "Ki" no lugar de "30". Esse documento saiu como `needs_review`, ou seja, a validação capturou o erro. Os outros dois `needs_review` do gabarito (`001_rljd` e `008_sl0j`) estão extraídos corretamente e foram para revisão porque o total impresso no documento não bate com a soma dos itens.

O gabarito cobre 12 dos 50 documentos e foi montado por uma pessoa só. O número mostra a ordem de grandeza e não substitui uma avaliação com amostra maior e dupla conferência.

## Estrutura do repositório

- `src/pipeline/ocr.py`: PDF para imagem e texto, com confiança do OCR e cache por hash do arquivo.
- `src/pipeline/classify.py`: classificação por regras com fallback para LLM.
- `src/pipeline/llm.py`: cliente do Gemini (Vertex AI ou AI Studio) com saída estruturada, medição de tokens e latência e cache de respostas em disco.
- `src/pipeline/extract.py` e `src/pipeline/prompts/`: extração por tipo de documento.
- `src/pipeline/normalize.py`: conversão de datas, valores e CNPJ/CPF.
- `src/pipeline/validate.py`: validação determinística (erros, anomalias e avisos).
- `src/pipeline/graph.py`: grafo LangGraph com roteamento condicional e limite de recursão.
- `src/pipeline/persist.py` e `src/pipeline/main.py`: execução em lote e gravação dos resultados.
- `eval/evaluate.py` e `data/gold/gold.json`: avaliação de acurácia contra o gabarito manual.
- `tests/`: testes da normalização, da validação, do grafo e do cache.
- `data/raw/` (os 50 PDFs) e `data/out/` (resultados versionados).

## Decisões de arquitetura

A arquitetura combina OCR local, LLM e regras determinísticas, e cada escolha tem uma razão.

- **OCR sobre a imagem original do PDF.** Ao renderizar a página em 200 dpi, o Tesseract chegou a perder linhas inteiras de tabelas. Usar a imagem original embutida no PDF evitou isso.
- **O texto do OCR como âncora da validação.** Todo número extraído precisa aparecer no texto. Isso separa erro de extração (o modelo inventou ou calculou um valor, vale tentar de novo) de anomalia do documento (os números estão lá, mas a conta não fecha, vai para revisão sem nova tentativa).
- **Extração em duas camadas.** O modelo devolve os valores como estão impressos, em modelos Pydantic de texto. Uma camada separada converte datas (`15/03/23`, `14.09.2023`, `15 de junho de 2024`) e valores (`R$ 3899,99`, `BRL 19.499,95`) nos tipos finais. Assim o LLM não decide formatos, e erros de conversão viram avisos, não falhas.
- **Dígito verificador de CNPJ e CPF só como aviso.** Os documentos do conjunto usam números sintéticos que quase nunca passam na conferência.

### Alternativas consideradas

- **Serviço gerenciado (como o Document AI).** Entrega mais rápido, mas cobra por página, dá menos controle sobre as regras de validação e roteamento e esconde as decisões de custo. Os requisitos pedem justamente controle e medição desses pontos.
- **PDF direto para um modelo com visão.** Simplificaria o código, mas tiraria a âncora do texto para validar os números. Não medi a diferença de custo nem de precisão entre texto e imagem. Fica como fallback possível para documentos em que o OCR falha.
- **Regras ou expressões regulares por template.** Quebram com facilidade: os mesmos registros aparecem em templates diferentes, com tabelas em colunas e datas e valores em formatos variados.
- **Agente de LLM com ferramentas decidindo o próprio caminho.** Seria menos previsível e mais difícil de limitar em custo e tentativas. Como o fluxo aqui é conhecido, um grafo com roteamento condicional e limite de recursão dá o mesmo resultado com comportamento auditável.

## Eficiência e escala

- **Classificação sem custo na maioria dos casos.** Regras por palavras-chave no cabeçalho do texto; o LLM só é chamado quando nenhuma regra casa ou mais de uma casa. Nos 50 documentos as regras resolveram todos, mas elas foram escritas a partir desses mesmos documentos, então isso não prova generalização, e o fallback para LLM não foi exercitado neste conjunto.
- **Escalada de modelo por custo.** A extração começa sempre no modelo barato (`gemini-3.5-flash-lite`), e o maior entra só quando a validação detecta erro de extração: foram 14 documentos de 50 na execução versionada (nas rodadas de teste variou entre 8 e 14, porque o modelo barato oscila nesse layout). Isso veio de um caso medido: em faturas com a tabela desmontada em colunas, o modelo pequeno deixava o total de cada item em branco mesmo com prompt reforçado e feedback de erro, e o modelo maior resolveu de forma estável.
- **Consumo e custo.** A execução completa usou 69.304 tokens de entrada e 31.313 de saída, em média cerca de 1.390 de entrada e 630 de saída por documento. O custo por documento é tokens de entrada × preço de entrada + tokens de saída × preço de saída, com o preço vigente de cada modelo; a maior parte do custo vem do modelo barato, que processa todos os documentos, e o modelo maior só entra nas novas tentativas. Para um milhão de documentos, isso dá na ordem de 1,4 bilhão de tokens de entrada e 0,6 bilhão de saída.
- **Limite de taxa.** O limite do Vertex (erro 429) apareceu numa rodada de teste com 4 documentos em paralelo e derrubou uma chamada ao modelo maior. As chamadas agora repetem até 5 vezes com espera crescente, e em escala o paralelismo precisa respeitar a cota do projeto.
- **Para milhões de documentos**, o desenho se mantém e o que muda é a infraestrutura: workers sem estado consumindo uma fila (Pub/Sub ou Cloud Tasks), cache do OCR e das respostas do LLM e idempotência pelo hash do arquivo (já existem os dois caches em disco), chamadas em lote e com backoff, persistência em banco (Postgres ou BigQuery) no lugar de arquivos, um classificador leve treinado com os documentos já classificados e observabilidade por documento (tokens, latência, taxa de revisão) em uma ferramenta como o LangFuse.

## Reprodutibilidade

O ambiente é fixado por `requirements.lock`, o OCR é cacheado por hash do arquivo e os resultados da execução estão versionados. As respostas do LLM também são guardadas em disco (`data/cache/llm`), com chave formada por modelo, instruções, texto e schema: reprocessar o mesmo documento devolve a mesma resposta, sem nova chamada e sem custo. Isso compensa o fato de os modelos usados ignorarem a temperatura. `LLM_CACHE=0` desliga o cache, e é o modo certo para medir o comportamento real do modelo. Há duas ressalvas honestas:

- Sem cache, a saída do LLM não é estritamente determinística: o mesmo prompt já deu resultados diferentes entre execuções, e a validação com nova tentativa existe em parte por isso. Nas rodadas de teste os mesmos 12 documentos foram para `needs_review` em todas; o que variou foi o número de novas tentativas (entre 8 e 14 de 50).
- O OCR depende da versão do Tesseract: o mesmo scan do `014_yfas` foi lido de forma diferente na versão 4.1.1 (Ubuntu) e na 5.4.0 (Windows), o que mudou o resultado desse documento (os outros 49 coincidiram). Para reprodução exata, convém fixar a versão do Tesseract, por exemplo em um contêiner.

## Testes

Os testes automatizados cobrem a normalização e a validação (determinísticas), o roteamento do grafo e o cache de respostas:

```bash
pip install -e ".[dev]"
python -m pytest
```

Os testes do grafo (`tests/test_graph.py`) simulam OCR, classificação, extração e validação e cobrem o caminho ok, a nova tentativa com o erro como feedback, o erro persistente (sem loop), a anomalia do documento, a classificação incerta e as falhas de OCR e de LLM. Os testes do cache (`tests/test_llm_cache.py`) usam um modelo simulado. Nenhum teste chama o LLM de verdade.

A acurácia contra o gabarito se mede com `python eval/evaluate.py`.

## Limitações e próximos passos

- **Taxa de revisão.** 12 dos 50 documentos (24%) vão para revisão manual. Onze deles não são falhas de extração: o total impresso no documento não bate com a soma dos itens, e o sistema sinaliza isso em vez de aceitar um dado inconsistente. No gabarito, nenhum documento `ok` tinha erro real, então essa taxa mede a qualidade dos documentos e o rigor da validação. Em produção, o passo seguinte seria uma fila de revisão com o motivo de cada caso, tolerâncias configuráveis por cliente e monitoramento dessa taxa ao longo do tempo.
- O gabarito cobre 12 documentos e foi feito por uma pessoa; a acurácia medida indica a ordem de grandeza, não um valor definitivo.
- Nomes cortados ou com erro de leitura no próprio scan (como "Passagei" ou "Yokogava") são copiados como estão, sem correção.
- A persistência é em arquivos (JSON e CSV), sem uma camada de abstração para banco de dados.
- Ficam de fora o fallback de visão para documentos em que o OCR falha e a validação do fallback de classificação por LLM sobre documentos de outros tipos.
- Os textos dos documentos são enviados ao Vertex AI na região `global`. Com dados reais e sensíveis, seria preciso escolher uma região fixa e avaliar as exigências da LGPD.
