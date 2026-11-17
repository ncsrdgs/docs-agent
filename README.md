# docs-agent

Pipeline em Python, LangChain e LangGraph que lê PDFs escaneados (notas fiscais, contratos de prestação de serviços e relatórios de manutenção), classifica cada documento, extrai os campos em JSON estrito e grava os resultados consolidados em JSON, CSV e, opcionalmente, SQLite. Foi construído para o Desafio 2 do processo de Engenheiro de IA Pleno da Franq, sobre os 50 PDFs do anexo.

## Como funciona

Cada documento passa por OCR (Tesseract, idioma português), classificação, extração por LLM com saída estruturada, normalização de datas e valores e validação determinística. Um grafo LangGraph decide o caminho de cada arquivo:

| Situação | Resultado |
| --- | --- |
| Sem pendências | `ok` |
| Erro de extração (campo obrigatório ausente, número que não aparece no texto do OCR) | Uma nova tentativa com o modelo maior e a mensagem do erro no prompt; se persistir, leitura pela imagem e, se ainda persistir, `needs_review` |
| Anomalia do próprio documento (por exemplo, soma dos itens diferente do total impresso) | Leitura pela imagem para confirmar os dados e, em seguida, `needs_review`, sem nova tentativa de texto |
| Tipo desconhecido ou confiança de classificação abaixo de 0,7 | `needs_review` |
| Falha de OCR ou do modelo | `failed` |

Antes de um documento ser fechado como `needs_review` por erro ou anomalia, ele é relido uma vez a partir da imagem da página (fallback de visão). Essa leitura é validada sem a âncora do texto do OCR, porque o OCR pode ser justamente o que errou, e só substitui a leitura do texto se não tiver erros e não tiver mais anomalias que a original. Qualquer falha nessa etapa mantém o resultado do texto, então ela nunca piora um documento.

Cada arquivo é processado de forma isolada, então um documento problemático nunca derruba o lote.

## Requisitos

Python 3.11 ou superior e o Tesseract com o idioma português:

- Ubuntu: `sudo apt install tesseract-ocr tesseract-ocr-por`
- macOS: `brew install tesseract tesseract-lang`
- Windows: o instalador do Tesseract mais o arquivo `por.traineddata` na pasta `tessdata`

O projeto foi testado no Ubuntu (Tesseract 4.1.1) e no Windows (Tesseract 5.4.0). No macOS os passos são os mesmos do Linux, mas não foram testados.

Para reproduzir os resultados dos 50 documentos incluídos, o Tesseract e as credenciais não são necessários: os caches de OCR e de respostas do modelo estão no repositório (veja Reprodutibilidade). Eles só são exigidos para processar documentos novos.

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

O comando principal aceita `--entrada` (pasta dos PDFs, padrão `data/raw`), `--saida` (padrão `data/out`), `--workers` (documentos em paralelo, padrão 4) e `--sqlite` (caminho de um banco SQLite onde gravar os registros, além do JSON e dos CSVs). Cada etapa também roda isolada, para depuração: `python -m pipeline.ocr`, `python -m pipeline.extract 001_rljd` e `python -m pipeline.graph 001_rljd 005_6zwp`.

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
| `VISAO_FALLBACK` | `0` desliga o fallback de visão (padrão: ligado) |
| `PRECO_ENTRADA_USD_POR_MILHAO` e `PRECO_SAIDA_USD_POR_MILHAO` | Opcionais. Preço por milhão de tokens de entrada e de saída, para o resumo estimar o custo. Os preços não estão fixados no código |

## Saída e resultados

A saída fica em `data/out/`:

- `results.json`: um registro por arquivo, com status, motivo, avisos, dados normalizados, confiança do OCR, número de páginas, tempo e versão do OCR, modelo usado, tokens, latência, tentativas e se o documento passou pelo fallback de visão (`visao_tentada`) e se os dados vieram dela (`visao_adotada`).
- `notas_fiscais.csv`, `notas_fiscais_itens.csv`, `contratos.csv` e `relatorios_manutencao.csv`: um CSV por tipo, com as colunas `status` e `motivo`. Os documentos em revisão também entram, para que os dados extraídos não se percam.
- `summary.json`: contagens (inclusive de documentos que passaram pela visão), tokens (totais e médios por documento), tempo e vazão do OCR, versões do Tesseract e a lista de pendências. O custo estimado só aparece se os dois preços do `.env` estiverem definidos, e é uma aproximação: aplica o mesmo preço a todos os tokens, sem separar os dois modelos.
- Com `--sqlite`, uma tabela `documentos` com um registro por arquivo, gravado por upsert pela chave do documento (hash do arquivo). Rodar de novo atualiza as linhas em vez de duplicá-las. O banco não é versionado.

Os resultados da execução sobre os 50 documentos estão versionados nessa pasta: **38 `ok` e 12 `needs_review`, nenhuma falha, em 70 segundos** com 4 documentos em paralelo. Os 12 em revisão são notas fiscais cuja soma dos itens difere do total impresso, conferido no gabarito. Os 12 passaram pelo fallback de visão, que corrigiu leituras erradas do OCR (como a quantidade de um item da `014_yfas` que o Tesseract não leu), mas não muda o motivo da revisão: a inconsistência está impressa no próprio documento.

## Acurácia medida

O gabarito (`data/gold/gold.json`) cobre os 50 documentos (17 notas, 17 contratos e 16 relatórios). Foi transcrito à mão a partir das imagens das páginas, sem consultar a saída do pipeline. Nomes truncados no documento ficam truncados no gabarito, e totais ficam como impressos. O comando `python eval/evaluate.py` compara `data/out/results.json` com ele, campo a campo (617 campos).

- Classificação: 50/50.
- Acurácia por campo: 97,9% exata e 99,7% tolerante. A tolerante aceita pequenas diferenças de grafia em nomes e em textos longos (objeto, problema e solução).
- Documentos 100% corretos (tolerante): 48 de 50.
- Roteamento: 38 `ok`, 12 `needs_review` e 0 `failed`. A precisão do `ok` é de 94,7%, porque 2 documentos `ok` têm erro de extração.
- Inconsistências reais: 12 documentos têm o total impresso diferente da soma dos itens (calculado só a partir do gabarito, de forma independente do pipeline), e os 12 foram sinalizados. Nenhum `needs_review` ficou sem causa real. A taxa de revisão de 24% vem, portanto, de defeitos dos próprios documentos.

O efeito do fallback de visão foi medido sobre a mesma base: sem ele, a acurácia era 96,6% exata e 98,5% tolerante, com 44 de 50 documentos 100% corretos. Com ele, a visão corrigiu leituras erradas do OCR nos documentos em revisão ("Classic |ll" no lugar de "Classic III", "mº" no lugar de "m³", "Cimento CP 1l" no lugar de "CP II", uma quantidade que o OCR não leu e um fornecedor cortado). Nenhum status mudou, e a precisão do `ok` e as 12 inconsistências sinalizadas ficaram iguais.

Os dois erros que restam são o campo `equipamento` dos relatórios `005_3spn` e `016_8s8n` ("Yokogava EJASSOA" no lugar de "Yokogawa EJA530A"). O Tesseract leu errado, o modelo copiou, e como a validação desses relatórios só confere se o campo existe, os dois saem como `ok`. Nenhuma checagem atual os detecta.

O classificador foi medido à parte com `python eval/classificador.py`. As regras acertam 50/50 e o modelo sozinho, sem as regras, também acerta 50/50. Em um conjunto separado de 12 textos (`data/holdout/`), com 3 de cada tipo e 3 de tipo desconhecido, o acerto é 12/12. Esses 12 textos foram escritos por mim para testar casos de borda, como um recibo que cita "nota fiscal" e que as regras poderiam confundir com uma nota. Medem robustez, não generalização para documentos reais de outras fontes.

O gabarito foi montado por uma pessoa só e cobre os mesmos 50 documentos usados para ajustar as regras, os prompts e o fallback de visão. Os números mostram a ordem de grandeza e não substituem uma avaliação com documentos novos e dupla conferência.

## Estrutura do repositório

- `src/pipeline/ocr.py`: PDF para imagem e texto, com confiança do OCR, tempo, versão do Tesseract e cache por hash do arquivo. Também entrega as páginas como imagem para o fallback de visão.
- `src/pipeline/classify.py`: classificação por regras com fallback para LLM.
- `src/pipeline/llm.py`: cliente do Gemini (Vertex AI ou AI Studio) com saída estruturada, medição de tokens e latência e cache de respostas em disco.
- `src/pipeline/extract.py` e `src/pipeline/prompts/`: extração por tipo de documento, a partir do texto ou da imagem.
- `src/pipeline/normalize.py`: conversão de datas, valores e CNPJ/CPF, e limpeza do marcador de lista nas descrições dos itens.
- `src/pipeline/validate.py`: validação determinística (erros, anomalias e avisos), com ou sem a âncora do texto do OCR.
- `src/pipeline/graph.py`: grafo LangGraph com roteamento condicional e limite de recursão.
- `src/pipeline/persist.py` e `src/pipeline/main.py`: execução em lote e gravação dos resultados.
- `src/pipeline/repositorio.py`: interface `Repositorio` e implementação em SQLite.
- `eval/evaluate.py` e `data/gold/gold.json`: avaliação de acurácia contra o gabarito manual.
- `eval/classificador.py` e `data/holdout/`: medição do classificador (regras, modelo sozinho e conjunto à parte).
- `tests/`: testes da normalização, da validação, do grafo, dos caches, do classificador, do resumo, do repositório e de ponta a ponta.
- `data/raw/` (os 50 PDFs), `data/out/` (resultados versionados) e `data/cache/` (caches de OCR e de respostas do modelo).

## Decisões de arquitetura

A arquitetura combina OCR local, LLM e regras determinísticas, e cada escolha tem uma razão.

- **OCR sobre a imagem original do PDF.** Ao renderizar a página em 200 dpi, o Tesseract chegou a perder linhas inteiras de tabelas. Usar a imagem original embutida no PDF evitou isso.
- **O texto do OCR como âncora da validação.** Todo número extraído precisa aparecer no texto. Isso separa erro de extração (o modelo inventou ou calculou um valor, vale tentar de novo) de anomalia do documento (os números estão lá, mas a conta não fecha, vai para revisão sem nova tentativa).
- **Extração em duas camadas.** O modelo devolve os valores como estão impressos, em modelos Pydantic de texto. Uma camada separada converte datas (`15/03/23`, `14.09.2023`, `15 de junho de 2024`) e valores (`R$ 3899,99`, `BRL 19.499,95`) nos tipos finais. Assim o LLM não decide formatos, e erros de conversão viram avisos, não falhas.
- **Fallback de visão só para quem iria para revisão.** A leitura pela imagem custa mais que a do texto, então não roda em todos os documentos: só em quem a validação já reprovou. A troca é conservadora (só substitui se a leitura nova não tiver erros e não tiver mais anomalias) e as chaves do cache de respostas de texto não mudam, então tudo o que já estava versionado continua valendo. O marcador de lista que a visão copia do documento ("- Cabo HDMI") é removido na normalização, e não pelo prompt.
- **Dígito verificador de CNPJ e CPF só como aviso.** Os documentos do conjunto usam números sintéticos que quase nunca passam na conferência.
- **Persistência atrás de uma interface.** O pipeline grava por meio de `Repositorio`, e o SQLite é a primeira implementação. A sintaxe de upsert (`ON CONFLICT ... DO UPDATE`) é a mesma do Postgres, então trocar de banco não mexe no resto do código.

### Alternativas consideradas

- **Serviço gerenciado (como o Document AI).** Entrega mais rápido, mas cobra por página, dá menos controle sobre as regras de validação e roteamento e esconde as decisões de custo. Os requisitos pedem justamente controle e medição desses pontos.
- **PDF direto para um modelo com visão em todos os documentos.** Simplificaria o código, mas tiraria a âncora do texto para validar os números e custaria mais por documento. Por isso a visão entra só como fallback, e o custo foi medido: nos 12 documentos em que rodou, consumiu cerca de 26,5 mil tokens de entrada e 17,5 mil de saída a mais.
- **Regras ou expressões regulares por template.** Quebram com facilidade: os mesmos registros aparecem em templates diferentes, com tabelas em colunas e datas e valores em formatos variados.
- **Agente de LLM com ferramentas decidindo o próprio caminho.** Seria menos previsível e mais difícil de limitar em custo e tentativas. Como o fluxo aqui é conhecido, um grafo com roteamento condicional e limite de recursão dá o mesmo resultado com comportamento auditável.

## Eficiência e escala

- **Classificação sem custo na maioria dos casos.** Regras por palavras-chave nos primeiros 100 caracteres do texto; o LLM só é chamado quando nenhuma regra casa. Nos 50 documentos as regras resolveram todos, mas foram escritas a partir desses mesmos documentos, então isso não prova generalização. Por isso medi o caminho do LLM separadamente: sem as regras ele também acerta os 50, e no conjunto à parte, que inclui textos de tipo desconhecido, acerta 12 de 12.
- **Escalada de modelo por custo.** A extração começa sempre no modelo barato (`gemini-3.5-flash-lite`), e o maior entra só quando a validação detecta erro de extração: foram 15 documentos de 50 na execução versionada (nas rodadas de teste variou entre 8 e 15, porque o modelo barato oscila nesse layout). Isso veio de um caso medido: em faturas com a tabela desmontada em colunas, o modelo pequeno deixava o total de cada item em branco mesmo com prompt reforçado e feedback de erro, e o modelo maior resolveu de forma estável.
- **Consumo e vazão medidos.** A execução completa usou 97.728 tokens de entrada e 50.636 de saída, em média cerca de 1.955 de entrada e 1.013 de saída por documento, em 70 segundos com 4 documentos em paralelo. Desses, 71.196 e 33.165 vieram da extração por texto, e o restante, dos 12 documentos que passaram pela visão. O OCR leva em média 1.429 ms por documento, ou 0,7 página por segundo por worker (Tesseract 5.4.0). O custo por documento é tokens de entrada × preço de entrada + tokens de saída × preço de saída, com o preço vigente de cada modelo. O `summary.json` calcula essa estimativa quando os preços estão no `.env`, mas eu não fixei valores no código. A maior parte do custo vem do modelo barato, que processa todos os documentos, e o modelo maior só entra nas novas tentativas. Para um milhão de documentos, isso dá na ordem de 2 bilhões de tokens de entrada e 1 bilhão de saída com a taxa de revisão deste conjunto, e a parcela da visão sobe ou cai junto com ela.
- **Limite de taxa.** O limite do Vertex (erro 429) apareceu numa rodada de teste com 4 documentos em paralelo e derrubou uma chamada ao modelo maior. As chamadas agora repetem até 5 vezes com espera crescente, e em escala o paralelismo precisa respeitar a cota do projeto.

O que já existe e o que mudaria para milhões de documentos:

| Tema | Hoje | Em produção |
| --- | --- | --- |
| Execução | documentos em paralelo (`--workers`) em uma máquina | workers sem estado consumindo uma fila (Pub/Sub ou Cloud Tasks) |
| Idempotência e custo | cache de OCR por hash do arquivo e cache de respostas do LLM em disco | os mesmos caches em armazenamento compartilhado |
| Persistência | JSON, CSV e SQLite com upsert por hash do arquivo, atrás da interface `Repositorio` | Postgres ou BigQuery, com a mesma interface |
| Modelos | modelo barato em todos, modelo maior só na nova tentativa e na visão, repetição com espera em caso de 429 | chamadas em lote, cota respeitada e um classificador leve treinado com os documentos já classificados |
| Revisão | status `needs_review` com o motivo | fila de revisão com retorno para o gabarito |
| Observabilidade | tokens, latência, tempo de OCR e taxa de revisão no `summary.json` | as mesmas métricas por documento em uma ferramenta como o LangFuse |

## Reprodutibilidade

O ambiente é fixado por `requirements.lock`, os resultados da execução estão versionados e os dois caches, de OCR (`data/cache/ocr`, chave pelo hash do arquivo) e de respostas do modelo (`data/cache/llm`, chave formada por modelo, instruções, texto e schema, mais o hash do PDF quando há imagem), também estão no repositório. Por isso, um clone limpo, sem `.env`, sem credenciais e sem Tesseract, reproduz o resultado em poucos segundos (a visão lê as imagens direto dos PDFs, sem precisar do Tesseract): `pip install -e ".[dev]"`, `python -m pipeline.main` e `python eval/evaluate.py` devolvem os mesmos 38 `ok` e 12 `needs_review` (12 deles pela visão) e as mesmas métricas desta página, sem nova chamada e sem custo. Isso compensa o fato de os modelos usados ignorarem a temperatura. `LLM_CACHE=0` desliga o cache de respostas, e é o modo certo para medir o comportamento real do modelo. Mudar qualquer item da chave (modelo, instruções, texto ou schema) gera uma nova chamada. Há duas ressalvas honestas:

- Sem cache, a saída do LLM não é estritamente determinística: o mesmo prompt já deu resultados diferentes entre execuções, e a validação com nova tentativa existe em parte por isso. Nas rodadas de teste os mesmos 12 documentos foram para `needs_review` em todas; o que variou foi o número de novas tentativas (entre 8 e 15 de 50).
- O OCR depende da versão do Tesseract: o mesmo scan do `014_yfas` foi lido de forma diferente na versão 4.1.1 (Ubuntu) e na 5.4.0 (Windows), o que mudou o resultado desse documento (os outros 49 coincidiram). O cache de OCR versionado foi gerado na 5.4.0, e a versão fica registrada em cada resultado. Para reprodução exata de documentos novos, convém fixar a versão do Tesseract, por exemplo em um contêiner.

## Testes

Os testes automatizados cobrem a normalização e a validação (determinísticas), o roteamento do grafo, os caches, o classificador, o resumo, o repositório e o fluxo de ponta a ponta:

```bash
pip install -e ".[dev]"
python -m pytest
```

São 91 testes (num clone limpo sem Tesseract, 87 passam e o módulo de ponta a ponta é pulado). Os do grafo (`tests/test_graph.py`) simulam OCR, classificação, extração e validação e cobrem o caminho ok, a nova tentativa com o erro como feedback, o erro persistente (sem loop), a anomalia do documento, a classificação incerta e as falhas de OCR e de LLM. Os do cache (`tests/test_llm_cache.py`) usam um modelo simulado. Os da visão (`tests/test_visao.py`) cobrem o roteamento, a regra de adoção, a falha silenciosa, a execução única, o cache por imagem e a validação sem âncora. Os de ponta a ponta (`tests/test_integracao.py`) rodam 4 PDFs reais pelo pipeline com um modelo falso e gravam os resultados, e são pulados quando o Tesseract não está instalado (num clone limpo sem Tesseract, o módulo inteiro aparece como 1 pulado). Nenhum teste chama o LLM de verdade.

A acurácia contra o gabarito se mede com `python eval/evaluate.py`, e o classificador com `python eval/classificador.py` (`--sem-llm` mede só as regras, sem credenciais).

## Limitações e próximos passos

- **Taxa de revisão.** 12 dos 50 documentos (24%) vão para revisão manual, e todos têm uma inconsistência real do próprio documento: o total impresso não bate com a soma dos itens. O sistema sinaliza isso em vez de aceitar um dado inconsistente. Em produção, o passo seguinte seria uma fila de revisão com o motivo de cada caso, tolerâncias configuráveis por cliente e monitoramento dessa taxa ao longo do tempo.
- **Erros de OCR em documentos aprovados.** O fallback de visão só roda em quem já iria para revisão. Quando o Tesseract lê errado, o modelo copia e a validação não tem como discordar (como o `equipamento` dos relatórios `005_3spn` e `016_8s8n`), o documento sai `ok` com o erro. Um gatilho possível seria a confiança do OCR por palavra nos campos que não têm âncora numérica; não está implementado.
- A visão melhora os dados, mas não resolve inconsistências impressas no próprio documento, que continuam indo para revisão.
- O gabarito foi feito por uma pessoa e cobre os mesmos 50 documentos usados para ajustar as regras, os prompts e o fallback de visão, então a acurácia indica a ordem de grandeza, não o desempenho em documentos novos. O conjunto de 12 textos do classificador foi escrito por mim.
- Nomes cortados ou com erro de leitura no próprio scan (como "Passagei" ou "Yokogava") são copiados como estão, sem correção.
- O repositório SQLite é um exemplo de implementação da interface; não há migração de esquema nem versão do banco em produção.
- Os textos dos documentos são enviados ao Vertex AI na região `global`. Com dados reais e sensíveis, seria preciso escolher uma região fixa e avaliar as exigências da LGPD.
