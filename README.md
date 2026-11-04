# Agente de atendimento — Empório da Música

Protótipo em Python de um agente de atendimento por texto para a loja fictícia Empório da Música, de Campo Grande/MS. O agente responde sobre produtos, preços, disponibilidade, pedidos e políticas da loja, em uma conversa pela linha de comando ou por uma página local no navegador.

**A ideia central:** o modelo interpreta a mensagem e redige a resposta; preços, descontos, parcelas, disponibilidade, prazos de troca e devolução e acesso a pedidos são resolvidos em Python.

## Como executar

Requer Python 3.11 ou superior (validado em 3.14.6, no Windows 11) e uma chave da API da OpenAI.

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -e ".[dev]"
Copy-Item .env.example .env      # preencha OPENAI_API_KEY e OPENAI_MODEL
python -m emporio prepare        # gera o banco e o índice em var/
python -m emporio web            # abre em http://127.0.0.1:8000; Ctrl+C encerra
python -m emporio chat           # ou conversa no terminal; "sair" encerra
```

Em Linux ou macOS, ative o ambiente com `source .venv/bin/activate` e copie o arquivo com `cp`.

| Comando | O que faz |
| --- | --- |
| `python -m emporio web --port 8080` | Usa outra porta para a página local. |
| `python -m emporio chat --customer-id 6` | Atalho para testes: pula a verificação e inicia como esse cliente. Não existe na página. |
| `python -m emporio chat --show-tools` | Mostra as ferramentas usadas em cada resposta. |
| `python -m emporio chat --script arq.txt --transcript saida.md` | Envia as mensagens de um arquivo ao modelo real e grava a conversa. Se o modelo falhar, encerra com erro e não grava a transcrição. |
| `pytest` | Roda os testes determinísticos, sem rede e sem chave. |
| `python evals/run.py` | Roda os cenários de aceite com o modelo real (consome créditos da API). |
| `python conversations/capture.py` | Regenera as conversas de exemplo (consome créditos da API). |

Para testar pedidos, pergunte por um pedido e informe na conversa o e-mail e o telefone do cliente, que estão no CSV de clientes em `data/`: o cliente 6 é dono do pedido 9, o cliente 2 do pedido 8, o cliente 12 do pedido 3 e o cliente 7 dos pedidos 2 e 20.

## Arquitetura

```text
CSVs ──► carga validada ──► SQLite ◄── consultas parametrizadas ── catalog.py, orders.py
Markdown ──► índice FTS5 ──────┘  ◄── busca lexical ────────────── knowledge.py
                                                                        ▲
Mensagem ──► agent.py ◄──► OpenAI (chamada de ferramentas)              │
                 └──────► tools.py: valida argumentos e executa ────────┘
```

| Módulo em `src/emporio/` | Responsabilidade |
| --- | --- |
| `loader.py`, `schema.sql` | Carga validada e atômica dos CSVs. |
| `db.py` | Conexão de atendimento em modo somente leitura. |
| `pricing.py` | Promoção, PIX, parcelamento e aritmética em centavos. |
| `catalog.py` | Busca, detalhes e oferta. |
| `orders.py` | Verificação de identidade e pedidos do cliente da sessão. |
| `knowledge.py` | Parse das políticas, índice e recuperação. |
| `returns.py` | Prazos de arrependimento, defeito, preferência e garantia legal. |
| `tools.py` | Contratos das sete ferramentas e tratamento de erros. |
| `llm.py` | Único ponto de contato com o SDK da OpenAI. |
| `agent.py`, `prompts.py` | Ciclo de ferramentas, sessão e persona. |
| `cli.py` | Comandos `prepare`, `chat` e `web`. |
| `web.py`, `static/` | Servidor local e página de conversa, sobre o mesmo agente da CLI. |

## Decisões técnicas

**Function calling, sem framework de agentes.** O ciclo é uma repetição curta: chamar o modelo, executar ferramentas, devolver resultados. LangChain ou LangGraph acrescentariam abstrações sem resolver um problema real aqui, e o fluxo explícito facilita testar limites de ciclos, falhas e corte de histórico.

**RAG híbrido com recuperação estruturada e textual.** Os dados da operação são relacionais e pedem filtros exatos, então vão para SQL; as políticas são texto e vão para busca textual. Perguntas mistas usam as duas fontes.

**Ferramentas em vez de SQL livre.** O modelo só escolhe entre sete operações com argumentos validados. Isso limita a superfície de consulta e mantém filtros, campos expostos e autorização no código. Nenhuma ferramenta devolve e-mail ou telefone: `verify_identity` só os compara, de modo que um prompt injection não tem como obter esses dados.

| Ferramenta | Função |
| --- | --- |
| `search_products` | Busca por texto, categoria e preço. Calcula o preço efetivo antes de filtrar, ordenar e limitar. |
| `get_product_details` | Descrição, especificações, status e estoque. |
| `calculate_offer` | Valor final e condições por forma de pagamento. |
| `verify_identity` | Confirma o cliente pelo e-mail e telefone informados na conversa. |
| `get_order_status` | Pedido ou lista de pedidos do cliente da sessão. |
| `check_return_window` | Calcula, com a data de hoje, se o prazo de troca ou devolução está aberto. |
| `search_knowledge` | Trechos do manual, com seção, página e suposições. |

**Prazos em Python.** O plano original previa cinco ferramentas, com o modelo comparando as datas. Contar dias é aritmética, e errar nisso nega ou concede um direito ao cliente, então a conta ganhou a mesma regra dos preços. A data de hoje vem da aplicação, no fuso da loja; o modelo só interpreta o que o cliente disse e relata o resultado.

**Identificação na conversa.** O agente pede e-mail e telefone somente quando precisa consultar um pedido. O código só compara valores que o próprio cliente digitou na conversa e só então define o cliente da sessão, de modo que o modelo não escolhe quem é o cliente nem pode inventar os dados. Pedir os dois não é excesso: um e-mail se repete entre dois clientes na base, e sozinho poderia expor os pedidos de outra pessoa. A falha é idêntica para qualquer dado errado, há três tentativas por conversa, e a identidade não muda depois de confirmada. E-mail e telefone não entram no log nem no registro de ferramentas exibido.

**OpenAI com Chat Completions.** A API oferece chamada de ferramentas com schemas estritos, que reduz argumentos malformados. O histórico explícito combina com a gestão da sessão em memória. O modelo é configurável por `OPENAI_MODEL`, e não há camada multiprovedor, que não se justifica em um protótipo. 

**Modelo `gpt-5.4-mini`.** O agente precisa de chamada de ferramentas confiável, não de raciocínio longo, pois cálculos e regras ficam no código. Esse modelo aceita ferramentas no Chat Completions sem configuração adicional, e o custo (US$ 0,75 de entrada e US$ 4,50 de saída por milhão de tokens, em 04/11/2026) permitiu repetir os cenários várias vezes. Pela documentação da OpenAI, o `gpt-6.1-terra` exige a API Responses para ferramentas, e o `gpt-6-luna` exige `reasoning_effort=none`. As falhas observadas foram omissões intermitentes, descritas em Verificação, e não erros de cálculo, que ficam no código.

**SQLite com `sqlite3`.** Não exige servidor, permite joins e tem FTS5. O banco é gerado localmente: a carga valida tudo em um arquivo temporário e só então substitui o anterior, de modo que um erro nunca deixa um banco parcial. Dinheiro fica em centavos inteiros, com `Decimal` nos descontos.

**FTS5 em vez de embeddings.** O manual é pequeno e seus termos são previsíveis, e a busca lexical é simples de reproduzir e depurar. Como o FTS5 não tem stemmer em português, a consulta combina termos com OR e prefixo, e cada seção tem aliases curados (“me arrependi”, “quero devolver”) com peso maior. Seções relacionadas são trazidas junto, para que PIX venha com a regra de não cumulatividade. Embeddings ficam como evolução, caso avaliações mostrem falhas com paráfrases.

**Políticas em Markdown com YAML.** O texto do manual fica separado das interpretações, registradas em `assumptions`, com página e versão de origem. O PDF continua sendo a referência. O índice é reconstruído a partir desses arquivos.

**Prompt.** O prompt de sistema define a persona do §7.1 do manual, quando consultar cada fonte, as regras que o modelo não pode contornar e o que ele não pode alegar ter feito. Data, hora e situação da loja são calculadas em Python e injetadas a cada chamada.

**Página local sem framework.** A página facilita testar o agente à mão, sem mudar suas regras: usa o mesmo `Agent` da CLI, e o cliente da sessão continua definido pela aplicação, fora do alcance do modelo. O servidor usa só a biblioteca padrão, pois um protótipo de uma página não justifica uma dependência nova. Por expor uma chave de API paga na máquina, ele escuta apenas em `127.0.0.1`, recusa outros `Host` e `Origin`, para que um site aberto no navegador não consiga usar a chave, e nunca interpreta a resposta do modelo como HTML.

**CLI e histórico em memória.** O enunciado foca no agente, e a CLI basta para demonstrá-lo e capturar as conversas de exemplo. O histórico é mantido durante a sessão, com os produtos discutidos no prompt para resolver “e no PIX?”. Ele é cortado por turno inteiro, sem separar chamadas de ferramenta de seus resultados. Persistir conversas não traria ganho nos cenários avaliados e criaria um novo cuidado com dados pessoais.

## Suposições

O enunciado pede que ambiguidades sejam resolvidas e documentadas.

| Situação | Decisão |
| --- | --- |
| Disponibilidade | À venda somente com `status = active` e estoque positivo: há produtos descontinuados e “em breve” com estoque. |
| Promoção | `is_active` é a única fonte de vigência, pois não há datas. A Black Friday citada no manual não concede desconto por si só em novembro. |
| Várias promoções ativas no mesmo produto | Não ocorre nos dados atuais. Se ocorrer, nenhum preço promocional é escolhido, e o agente orienta a confirmação. |
| Promoção e formas de pagamento | A promoção vale para qualquer forma de pagamento, e o parcelamento incide sobre o preço promocional. |
| Busca por orçamento | Filtra pelo preço efetivo: promoção ativa e, somente se o cliente pedir, PIX. A Ohana CK-20 de R$ 549,00 entra em “até R$ 500” por R$ 439,20. |
| Tipo de instrumento na busca | “Violão” ou “viola” selecionam a categoria, não uma menção na descrição: um ukulele que cita violão não entra em “violões”. |
| Contagem de prazos | Dias corridos: o dia do evento é o dia 0, e o último dia ainda vale. Um prazo que termina em dia sem expediente não é prorrogado, pois o manual não prevê isso. |
| Início de cada prazo | Arrependimento conta do recebimento, e troca por defeito, da compra, como diz o manual. Sem data registrada de entrega, o recebimento é o informado pelo cliente; a previsão nunca o substitui. |
| Início da troca por preferência | O manual não define. O agente só conclui o que vale para os dois inícios possíveis; se a conclusão mudar conforme o início, orienta a confirmação com a equipe. |
| Arrependimento em loja física | A seção 4.1 trata de compras online. O agente não aplica esse direito à loja física e apresenta as outras modalidades. |
| Parcela mínima | O §3.1 prevalece sobre o R$ 100,00 geral do §3. “Exceto abaixo de R$ 50,00” foi lido como mínimo de R$ 50,00 em até 3x. |
| Arredondamento | Meio centavo arredonda para cima. Centavos restantes vão para as primeiras parcelas, e a menor parcela é a comparada ao mínimo. |
| WhatsApp | Divergente no manual: prioriza o (67) 3321-4500 da seção específica do canal. O (67) 3341-4444 fica como telefone geral. |
| E-mail | Adota o endereço da capa, sem acento no domínio. |
| Frete de exatamente R$ 500,00 | O manual não define. O agente orienta a confirmação, sem escolher uma regra. |
| Fuso horário | `America/Campo_Grande`, o fuso da loja. Feriados não constam dos dados e são tratados como informação indisponível. |
| Identificação | E-mail e telefone do cadastro, verificados no código. Dados que aparecem na conversa não provam posse, então isso é uma verificação de protótipo, não autenticação. |

Inconsistências encontradas nos dados, preservadas e não corrigidas:

- Os totais dos pedidos 3 e 20 diferem da soma pelos preços atuais. O total registrado é tratado como histórico.
- Os pedidos 9, 16 e 18 têm parcelas abaixo do mínimo atual. A regra vale para ofertas novas, não para o histórico.
- Os pedidos 3 e 14 contêm um produto “em breve”, e o pedido 3 consta como entregue. Itens de pedido nunca indicam disponibilidade.
- O pedido 8 está como enviado, com previsão vencida. O agente relata o registro e orienta a confirmação, sem afirmar atraso ou entrega.
- O manual cita mais de 300 instrumentos, e os dados trazem 65. O agente responde sobre o que consta, sem concluir que a loja não trabalha com o restante.

## Verificação

- **Testes determinísticos (`pytest`):** carga, preços, parcelas, prazos de devolução, disponibilidade, titularidade, recuperação das políticas, mecânica do ciclo do agente e API da página local. Os testes do ciclo usam um cliente simulado e não avaliam respostas do modelo.
- **Cenários de aceite (`evals/`):** 44 cenários com o modelo real, verificando fatos e ferramentas chamadas, sem exigir texto idêntico. Tom e persona são revistos na leitura das conversas. O modelo varia entre execuções: nas três últimas rodadas com `gpt-5.4-mini`, em 04/11/2026, passaram 43, 43 e 40 de 44. As falhas foram intermitentes: o modelo às vezes pula uma consulta, omite os valores das parcelas ou abre com “dá sim” antes de ressalvar que um produto já testado depende da avaliação da equipe. Os checks foram mantidos rígidos, em vez de afrouxados para zerar essas falhas.
- **Conversas de exemplo (`conversations/`):** cinco transcrições de execuções reais da CLI, com modelo e ferramentas usadas. Revê-las encontrou erros que os checks não pegaram, como parcelas mal interpretadas, corrigidos antes desta captura. O e-mail e o telefone na conversa 03 são do cliente fictício do CSV.

Esses testes cobrem os cenários avaliados e não garantem o comportamento para toda entrada possível. Prompt e testes não eliminam alucinação ou prompt injection; por isso, cálculo, autorização e bloqueio de escrita ficam no código.

## Limitações conhecidas

- O agente não executa ações: não registra reclamações, não abre devoluções, não faz reembolsos e não envia mensagens. Ele orienta e indica os canais da loja, ainda que o manual preveja o registro da reclamação.
- A conta do prazo é determinística, mas depende do que entra nela: a base não registra a entrega real nem o canal da compra, e é o modelo que traduz a fala do cliente em datas e no tipo de caso. O uso da ferramenta é instruído no prompt, não imposto pelo código, então o modelo pode ocasionalmente responder sem chamá-la.
- Os dados são um retrato dos CSVs importados, sem sincronização com estoque ou transportadoras.
- A busca textual depende dos aliases curados e pode falhar em paráfrases não previstas.
- A busca por atributos é textual sobre nome, descrição e especificações; não há filtros estruturados por atributo.
- Não há cotação de frete, pois ela depende de CEP, peso e dimensões, que não foram fornecidos.
- Não há calendário de feriados nem de horários especiais.
- O histórico se perde ao encerrar a sessão, ao recarregar a página ou ao iniciar uma nova conversa.
- Quem souber o e-mail e o telefone de um cliente consegue consultar os pedidos dele, pois não há código enviado ao cliente nem senha. Esses dados, digitados na conversa, também são enviados ao provedor do modelo e aparecem na transcrição da CLI se ela for gravada.
- A página é só para uso local: não tem login nem HTTPS, e não deve ser exposta na rede. Ela não grava transcrições, papel que fica com a CLI.

## O que eu faria com mais tempo

- Autenticação real, com código enviado ao cliente, em vez de comparar e-mail e telefone.
- Verificação da resposta em código antes de enviá-la ao cliente: conferir se valores e datas têm origem nas ferramentas e se o plano de parcelas aparece inteiro, com uma nova tentativa em caso de reprovação.
- Avaliação de retrieval com mais paráfrases e, se os resultados justificarem, busca híbrida com embeddings.
- Integrações para registrar reclamações e encaminhar a atendentes humanos.
- API e integração com WhatsApp, o principal canal remoto da loja.
- Filtros estruturados por atributos, como cordas, eletrônica e tipo de corpo.
- Observabilidade de custo e latência por conversa, e avaliações no CI.

## Uso de assistentes de código

- **Codex:** análise do enunciado e dos dados, levantamento de requisitos, parecer sobre o design e plano de execução, registrados em [LEVANTAMENTO_REQUISITOS.md](LEVANTAMENTO_REQUISITOS.md), [PARECER_ADERENCIA_SYSTEM_DESIGN_V2.md](PARECER_ADERENCIA_SYSTEM_DESIGN_V2.md) e [PLANO_EXECUCAO.md](PLANO_EXECUCAO.md).
- **Claude Code:** revisão do plano contra os dados, o que revelou lacunas como parcelas históricas abaixo do mínimo e o fuso da loja; implementação do código, das políticas, dos testes e dos cenários; redação deste README.

**Meu papel:** decidi provedor, escopo e publicação, e revisei o resultado. Pedi uma revisão de erros e escolhi o que corrigir; solicitei a interface local, o cálculo de prazos em Python e a identificação na conversa; e corrigi o agente ao notar que ele perguntava em qual loja a compra foi feita, embora só exista uma. Os cálculos críticos foram conferidos contra os CSVs por testes automatizados, e as respostas do modelo, pela leitura das conversas, que encontrou erros que os testes não pegaram.
