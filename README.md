# \# docs-agent

# 

# Pipeline em Python, LangChain e LangGraph que lê PDFs escaneados (notas fiscais, contratos de prestação de serviços e relatórios de manutenção), classifica cada documento, extrai os campos em JSON estrito e grava os resultados consolidados em JSON e CSV. Foi construído para o Desafio 2 do processo de Engenheiro de IA Pleno da Franq, sobre os 50 PDFs do anexo.

# 

# Cada documento passa por OCR (Tesseract, idioma português), classificação, extração por LLM com saída estruturada, normalização de datas e valores e validação determinística. Um grafo LangGraph decide o caminho: documento sem pendências termina como `ok`; erro de extração volta uma vez para a etapa de extração, agora com um modelo maior e a mensagem do erro no prompt; anomalia do próprio documento (por exemplo, soma dos itens diferente do total impresso), tipo desconhecido ou confiança baixa terminam como `needs\_review`; falha de OCR ou do modelo termina como `failed`. Cada arquivo é processado de forma isolada, então um documento problemático nunca derruba o lote.

# 

# Para rodar, é preciso Python 3.11 ou superior e o Tesseract com o idioma português (no Ubuntu, `sudo apt install tesseract-ocr tesseract-ocr-por`; no Windows, o instalador do Tesseract mais o arquivo `por.traineddata` na pasta `tessdata`). Depois:

# 

# ```bash

# git clone https://github.com/ncsrdgs/docs-agent.git

# cd docs-agent

# python -m venv .venv \&\& source .venv/bin/activate   # no Windows: .venv\\Scripts\\Activate.ps1

# pip install -e .

# cp .env.example .env

# python -m pipeline.main

# ```

# 

# O `requirements.lock` traz as versões exatas usadas nos testes (`pip install -r requirements.lock` antes do `pip install -e .` reproduz o ambiente). No `.env`, a forma recomendada de acessar o Gemini é pelo Vertex AI: defina `GOOGLE\_CLOUD\_PROJECT`, `GOOGLE\_CLOUD\_LOCATION=global` e `GOOGLE\_APPLICATION\_CREDENTIALS` apontando para a chave JSON de uma conta de serviço com o papel "Usuário da Vertex AI". Sem `GOOGLE\_CLOUD\_PROJECT`, o pipeline usa a chave do AI Studio em `GOOGLE\_API\_KEY`. Os modelos são configuráveis: `GEMINI\_MODEL` (padrão `gemini-3.5-flash-lite`, usado em todos os documentos) e `GEMINI\_MODEL\_FORTE` (padrão `gemini-3.5-flash`, usado só nas novas tentativas). Neste projeto esses modelos responderam apenas na região `global`. No Windows, defina também `TESSERACT\_CMD` com o caminho do executável. O comando aceita `--entrada` (pasta dos PDFs, padrão `data/raw`), `--saida` (padrão `data/out`) e `--workers` (documentos em paralelo, padrão 4). Cada etapa também roda isolada para depuração: `python -m pipeline.ocr`, `python -m pipeline.extract 001\_rljd` e `python -m pipeline.graph 001\_rljd 005\_6zwp`.

# 

# A saída fica em `data/out/`: `results.json` com um registro por arquivo (status, motivo, avisos, dados normalizados, confiança do OCR, modelo, tokens, latência e tentativas), um CSV por tipo (`notas\_fiscais.csv`, `notas\_fiscais\_itens.csv`, `contratos.csv`, `relatorios\_manutencao.csv`, com as colunas `status` e `motivo`, incluindo os documentos em revisão para que os dados extraídos não se percam) e `summary.json` com contagens, tokens e a lista de pendências. Os resultados da execução sobre os 50 documentos estão versionados nessa pasta: 38 `ok` e 12 `needs\_review`, nenhuma falha, em 60 segundos com 4 documentos em paralelo. Onze dos 12 em revisão são notas fiscais cuja soma dos itens difere do total impresso (dois pares são o mesmo registro em templates diferentes). O outro, `014\_yfas`, teve a quantidade de um item lida pelo OCR como "Ki" e ficou em revisão com esse motivo, em vez de o sistema inventar o valor.

# 

# A arquitetura combina OCR local, LLM e regras determinísticas, e cada escolha tem uma razão. O OCR vem da imagem original embutida no PDF, e não de uma nova renderização: ao renderizar a página em 200 dpi, o Tesseract chegou a perder linhas inteiras de tabelas, e a imagem original evitou isso. O texto do OCR serve de âncora para a validação: todo número extraído precisa aparecer no texto, e isso separa erro de extração (o modelo inventou ou calculou um valor, vale tentar de novo) de anomalia do documento (os números estão lá, mas a conta não fecha, vai para revisão sem nova tentativa). O modelo devolve os valores como estão impressos, em modelos Pydantic de texto, e uma camada separada converte datas (`15/03/23`, `14.09.2023`, `15 de junho de 2024`) e valores (`R$ 3899,99`, `BRL 19.499,95`) nos tipos finais; assim o LLM não decide formatos e erros de conversão viram avisos, não falhas. Dígitos verificadores de CNPJ e CPF são só avisos informativos, porque os documentos do conjunto usam números sintéticos que quase nunca passam na conferência.

# 

# Contra as alternativas: um serviço gerenciado como o Document AI entrega mais rápido, mas cobra por página, dá menos controle sobre as regras de validação e roteamento e esconde as decisões de custo; aqui os requisitos pedem justamente controle e medição desses pontos. Enviar o PDF direto a um modelo com visão simplificaria o código, mas tiraria a âncora do texto para validar os números, e eu não medi a diferença de custo nem de precisão entre texto e imagem; fica como fallback possível para documentos em que o OCR falha. Regras ou expressões regulares por template quebram com facilidade: os mesmos registros aparecem em templates diferentes, com tabelas em colunas, datas e valores em formatos variados. Um agente de LLM com ferramentas decidindo o próprio caminho seria menos previsível e mais difícil de limitar em custo e tentativas; como o fluxo aqui é conhecido, um grafo com roteamento condicional e limite de recursão dá o mesmo resultado com comportamento auditável.

# 

# Sobre eficiência e escala, a classificação usa primeiro regras por palavras-chave no cabeçalho (custo zero) e só chama o LLM quando nenhuma regra casa ou mais de uma casa; nos 50 documentos as regras resolveram todos, mas elas foram escritas a partir desses mesmos documentos, então isso não prova generalização. A extração começa sempre no modelo barato (`gemini-3.5-flash-lite`), e o modelo maior entra só quando a validação detecta erro de extração: foram 8 documentos de 50. Essa escalada veio de um caso medido: em faturas com a tabela desmontada em colunas, o modelo pequeno deixava o total de cada item em branco mesmo com prompt reforçado e feedback de erro, e o modelo maior resolveu de forma estável. A execução completa consumiu 62.790 tokens de entrada e 23.444 de saída (cerca de 1.250 de entrada por documento). Para milhões de documentos, o desenho se mantém e o que muda é a infraestrutura: workers sem estado consumindo uma

