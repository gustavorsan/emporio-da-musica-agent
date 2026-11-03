# Plano de execução — Agente Empório da Música

Planejamento em 03/11/2026, baseado no enunciado, no levantamento de requisitos e no parecer sobre o System Design v2.

**Prazo confirmado pelo usuário: 04/11/2026 às 23h59. Fuso adotado: America/Sao_Paulo. Meta interna de entrega: até 22h, com 1h59 de margem.**

Objetivo: concluir hoje um MVP executável e usar amanhã para revisão final e entrega. O plano combina blocos relativos de esforço com marcos de conclusão antes do prazo. Modelo/provedor e credencial disponível serão definidos no primeiro bloco.

## 1. Avaliação e decisão de escopo

A arquitetura proposta é aderente, mas a implementação completa de todas as camadas, abstrações e evoluções excede o que convém assumir para este prazo. O trabalho deve avançar por entregas verificáveis: primeiro uma conversa real consultando o banco; depois recuperação de políticas e ampliação da cobertura.

O workspace contém os materiais originais e os documentos de análise. Ainda não há código do agente. A DDL ilustrativa já foi executada em memória, os pares de itens de pedido foram conferidos e o runtime usado na análise dispõe de FTS5. Esses resultados não substituem a implementação e a validação do projeto.

### Escopo selecionado para a entrega

- Python, CLI e histórico da sessão em memória.
- SQLite carregado a partir dos seis CSVs, com dados originais preservados.
- Cinco ferramentas: busca de produtos, detalhes, cálculo de oferta, consulta de pedido e busca de políticas.
- Políticas em Markdown com YAML, organizadas por tema e indexadas com FTS5.
- Preços, descontos, disponibilidade e titularidade de pedidos resolvidos em Python.
- Persona informal, acolhedora e profissional; tratamento de perguntas fora do escopo, informações ausentes e erros.
- README reproduzível, histórico real de commits e de 3 a 5 conversas reais, com pelo menos um cenário não trivial.

### Evoluções adiadas

API HTTP, frontend, WhatsApp, integrações operacionais, embeddings, reranking, autenticação de produção, histórico persistente, ORM, plataforma de observabilidade e framework próprio de OKF.

OKF será apenas a convenção de arquivos Markdown com metadados. O PDF original continua sendo a referência para conferir a fidelidade das políticas.

## 2. Sequência e esforço

Os tempos abaixo são **timeboxes de planejamento**, não uma promessa de conclusão. Dependem principalmente de acesso ao modelo, instalação de dependências e qualidade inicial das respostas. Ao exceder um bloco, reduzir itens opcionais antes de ampliar a arquitetura.

| Bloco | Trabalho | Timebox inicial | Dependência | Resultado verificável |
| --- | --- | --- | --- | --- |
| B0 | Preparação, decisões e teste do provedor | 30 min | Modelo/credencial disponível | Projeto inicia e uma chamada real de ferramenta funciona. |
| B1 | Carga SQLite e consultas básicas | 1 h | B0 para ambiente; não depende do LLM | Banco reproduzível, contagens e relações conferidas. |
| B2 | Primeira conversa completa na CLI | 2 h | B0 e B1 | Pergunta sobre produto gera consulta, cálculo e resposta real do LLM. |
| B3 | Políticas e recuperação FTS5 | 1 h 30 min | B1; integração com B2 | Políticas recuperáveis, com fontes e condições completas. |
| B4 | Pedidos e fechamento do escopo funcional | 1 h | B2 e B3 | Pedido, pergunta mista, continuidade e fora do escopo funcionam. |
| B5 | Validação e correções | 1 h 30 min | B4 | Casos críticos passam e limitações ficam registradas. |
| B6 | Exemplos, README e entrega | 1 h | B5; README começa em B0 | Execução reproduzível e evidências reais prontas. |
| Reserva | Instalação, falhas do provedor e correções imprevistas | 1 h 30 min | Conforme necessário | Absorver problemas sem adicionar funcionalidades. |

**Total inicial: 10 horas de esforço ativo, incluindo reserva.** Se houver menos tempo disponível, aplicar os cortes da seção 8. A distribuição dos blocos depende da disponibilidade de trabalho; os marcos abaixo são metas de conclusão, não períodos de dedicação contínua.

Meta de hoje: B0 a B4 concluídos, verificações críticas feitas durante cada bloco e README inicial utilizável. Amanhã: concluir pendências essenciais, validar, capturar exemplos finais, revisar a reprodução e entregar com margem.

### Marcos de conclusão

| Quando, no horário de São Paulo | Meta |
| --- | --- |
| Hoje, 03/11 | MVP executável: SQLite, CLI, ferramentas, políticas e fluxos selecionados. |
| Amanhã, 04/11, até 16h | Encerrar funcionalidades e resolver pendências essenciais; aplicar cortes de escopo se necessário. |
| Até 19h | Concluir validação e correções críticas. |
| Até 21h | README, conversas reais e reprodução final prontos. |
| Até 22h | Conferir repositório público e enviar o link pelo canal do processo. |
| 22h–23h59 | Margem para imprevistos antes do envio, caso a meta interna não seja cumprida. Depois de entregar, manter o repositório congelado. |

## 3. Decisões de implementação

| Tema | Decisão inicial |
| --- | --- |
| Organização | Um processo Python com módulos pequenos; sem serviços separados. |
| SQLite | Usar `sqlite3`; banco gerado localmente e conexão de atendimento em modo de leitura. |
| Importação | Preservar os arquivos em `data/`, aceitando seus nomes reais. Carga validada em transação; uma segunda execução não duplica registros. |
| Dinheiro | Centavos inteiros no banco; `Decimal` para descontos e arredondamento explícito. |
| Busca por orçamento | Considerar promoção ativa; aplicar PIX somente quando solicitado e elegível. Exibir a condição do preço usado no filtro. |
| Disponibilidade | `status == active` e estoque positivo, como suposição documentada. |
| Promoções | Usar a flag de atividade; diante de múltiplas ativas para o mesmo produto, informar ambiguidade e não selecionar arbitrariamente. |
| Parcelamento | Até 3x: mínimo de R$ 50 por parcela como suposição; 4–6x: R$ 80; 7–12x: R$ 100. Documentar precedência da seção específica do manual. |
| Memória | Histórico curto por sessão e referência aos produtos discutidos; não persistir identidade entre sessões. |
| Cliente | Seleção demonstrativa na aplicação; titularidade verificada em Python. O LLM não escolhe o cliente para autorizar acesso. |
| Retrieval | Busca lexical FTS5, com termos/aliases de domínio e referências de fonte. |
| Modelo | Escolher um provedor acessível com suporte a ferramentas; usar um único SDK e modelo configurável. Evitar camada multiprovedor no MVP. |
| Logs | Ferramenta, resultado/erro, duração e fontes recuperadas; sem dados pessoais desnecessários. |

A base será um retrato dos arquivos importados. Consultar o banco durante a conversa não significa sincronizar estoque ou rastreamento com uma operação externa.

## 4. Execução por bloco

### B0 — Preparar o projeto e eliminar dependências externas incertas

1. Registrar o prazo confirmado de 04/11 às 23h59 e verificar acesso ao provedor de LLM. Não colocar credenciais em mensagens, arquivos versionados ou logs.
2. Criar ambiente, `pyproject.toml`, `.gitignore`, `.env.example` e README inicial. Registrar a versão do Python efetivamente validada.
3. Iniciar Git e o histórico real de trabalho, caso ainda não exista repositório. Os commits devem acompanhar alterações concluídas.
4. Escolher e registrar provedor/modelo. Executar uma chamada real que solicite uma ferramenta e processe seu retorno.
5. Definir os comandos finais pretendidos: preparar base, iniciar CLI e executar verificações. Implementá-los ao longo dos blocos seguintes.

**Concluído quando:** o ambiente funciona, o teste de ferramenta do modelo é real e a configuração pode ser repetida pelo README. Se a credencial estiver indisponível, B1 e B3 ainda podem avançar, mas a demonstração real permanece pendente.

### B1 — Criar a base consultável

1. Implementar as seis tabelas e relações, habilitando verificação de chaves estrangeiras na carga.
2. Ler CSVs em UTF-8; validar IDs, quantidades, status, descontos, datas e JSON de especificações. Converter campos vazios opcionais para ausência e dinheiro para centavos.
3. Preservar o total histórico de `orders`; não recalcular com preços atuais.
4. Criar carga reproduzível. Para este MVP, gerar um banco novo temporário, validar e só então substituir o banco gerado anterior, com as conexões de atendimento fechadas. Não alterar os CSVs originais.
5. Conferir contagens: 65 produtos, 9 categorias, 25 promoções, 50 clientes, 20 pedidos e 22 itens; verificar referências e repetir a carga sem duplicação.
6. Implementar consultas parametrizadas por produto, categoria e pedido. Limitar campos e quantidade de resultados.

**Concluído quando:** dados importados conferem com as fontes, erros de carga são explícitos e consultas conhecidas retornam resultados corretos. Um erro de importação não deve deixar um banco parcial disponível para atendimento.

### B2 — Entregar o primeiro fluxo completo

1. Implementar busca, detalhes e cálculo de oferta. O preço usado para filtro/ordenação e o apresentado na resposta devem seguir a mesma regra.
2. Criar CLI, prompt da persona, histórico da sessão e o ciclo de ferramentas do LLM.
3. Validar argumentos antes de executar a ferramenta; limitar ciclos, tempo e tamanho dos resultados. Expor falha ou necessidade de esclarecimento de forma compreensível.
4. O LLM interpreta a pergunta e redige a resposta com os fatos retornados. As ferramentas fazem cálculos e consultas.
5. Validar uma conversa real: “Quanto custa o Kalani KAL-700T no PIX?” → consulta ao banco → oferta de R$ 323,10, sem somar PIX à promoção.
6. Validar continuação sobre o mesmo produto e um cenário de promoção inativa.

**Concluído quando:** uma pessoa inicia a CLI e consegue conversar com o modelo real, observar resultados corretos e fazer uma pergunta subsequente. Resposta simulada não fecha este marco.

### B3 — Preparar e recuperar políticas

1. Organizar o manual em seis grupos coesos: loja/contatos/horários; pagamentos/promoções; entregas; trocas/devoluções; garantia; atendimento/privacidade.
2. Preservar exceções e condições nas seções. Usar IDs, título, tags, fonte, páginas, seção, versão do manual e suposições identificadas.
3. Registrar as decisões para contatos divergentes e parcelamento; manter o limite de frete de exatamente R$ 500 como indefinido, com orientação de confirmação.
4. Indexar trechos por seção no FTS5. O índice é reconstruído a partir dos arquivos; verificar se o ambiente suporta FTS5 antes de abrir a CLI.
5. Gerar consultas com termos de domínio e aliases. Recuperar até três candidatos principais, incluindo seções relacionadas necessárias para completar a regra. Se tags restringirem demais, repetir sem esse filtro antes do fallback.
6. Evitar usar o ranking como medida de certeza. Validar relevância e cobertura com perguntas conhecidas.
7. Integrar `search_knowledge` ao agente e manter a origem dos trechos acessível para depuração e referência.

**Concluído quando:** endereço, horário, pagamento, arrependimento, frete e garantia recuperam as seções esperadas; perguntas em linguagem diferente do manual são avaliadas; perguntas sem evidência geram esclarecimento ou limitação.

### B4 — Fechar os fluxos do MVP

1. Implementar consulta de pedido com titularidade da sessão verificada antes de expor campos.
2. Exibir status, total histórico, itens e rastreio/previsão somente quando existentes. Manter dados pessoais fora de retornos desnecessários.
3. Completar parcelamento, com divisão dos centavos que preserve o total e verificação da menor parcela após distribuição.
4. Para devolução, pedir canal de compra e data real de recebimento quando ausentes. Não tratar previsão como recebimento confirmado.
5. Completar perguntas mistas: produto + pagamento; pedido + política; indisponibilidade + alternativas verificadas.
6. Tratar pedido de acessórios, perguntas externas ao atendimento, produto não encontrado e indisponibilidade do provedor.
7. Conferir que a aplicação não afirma ter enviado mensagens, registrado reclamações ou realizado reembolso sem executar essas ações.

**Concluído quando:** os cenários selecionados de catálogo, políticas, pedido, continuidade e fora do escopo estão demonstráveis na CLI.

### B5 — Validar o que pode comprometer a entrega

1. Rodar verificações determinísticas de dados, preços, parcelas, disponibilidade e autorização. Corrigir falhas antes de gerar exemplos finais.
2. Avaliar recuperação com uma lista curta de perguntas e seções esperadas, incluindo sinônimos e exceções.
3. Executar conversas com o modelo real, registrando resultados observados e sem exigir redação textual idêntica.
4. Testar ausência de dados, argumentos inválidos, tentativas de acesso a outro cliente e falha de ferramenta/provedor.
5. Corrigir regressões e repetir apenas os casos afetados, além de uma conversa geral de verificação após a última alteração relevante.

**Concluído quando:** nenhum caso crítico de dinheiro, titularidade, disponibilidade ou aplicação da política falha no conjunto selecionado; limitações restantes estão explícitas. Os testes cobrem os cenários avaliados e não constituem garantia absoluta sobre toda entrada possível.

### B6 — Produzir evidências e concluir a entrega

1. Capturar três conversas reais obrigatórias para o escopo selecionado; chegar a cinco somente se isso couber no prazo.
2. Completar README: ambiente, dependências, comandos, credenciais via configuração, modelo, arquitetura, dados, suposições, limitações, verificações e uso de IA.
3. Declarar o uso de Codex nesta análise, revisão e planejamento, além do uso que ocorrer na implementação.
4. Reproduzir instalação, preparação dos dados e conversa seguindo apenas o README, em ambiente limpo quando viável.
5. Preparar o repositório público com histórico real. Conferir quais dados de demonstração serão publicados e garantir que a reprodução descrita funciona com os arquivos disponibilizados; excluir credenciais, logs locais e banco gerado do versionamento.
6. Conferir acesso público ao link. O candidato responde ao e-mail do processo e respeita a data/horário de entrega; nenhuma alteração posterior no repositório.

**Concluído quando:** o avaliador consegue reproduzir o agente e encontrar as conversas, justificativas e limitações no repositório público.

## 5. Contratos mínimos das cinco ferramentas

| Ferramenta | Entrada controlada pelo LLM | Validação/execução no código |
| --- | --- | --- |
| `search_products` | Texto opcional, categoria opcional, limites de preço, disponibilidade, ordenação, pagamento opcional, limite de resultados | Exigir critério útil ou contexto resolvido; normalizar busca; calcular preço efetivo antes de filtrar/ordenar/limitar; retornar IDs e condições. |
| `get_product_details` | `product_id` | Consultar detalhes, status, estoque e especificações existentes. |
| `calculate_offer` | `product_id`, pagamento, parcelas quando aplicável | Validar produto/promoção; calcular valores com `Decimal`; preservar centavos; informar condições e disponibilidade. |
| `get_order_status` | `order_id` | Injetar cliente da sessão fora dos argumentos do modelo, verificar titularidade e retornar apenas campos necessários. |
| `search_knowledge` | Pergunta e tags opcionais | Recuperar trechos relevantes e complementares; retornar fonte/página/seção e limitações. |

Filtros estruturados por atributos ficam limitados aos que forem implementados e verificados. No primeiro corte, nome, categoria, preço e disponibilidade têm prioridade; detalhes existentes podem ser consultados para esclarecimento. Não prometer filtragem completa de todos os campos heterogêneos de `specs`.

## 6. Casos de validação prioritários

| Prioridade | Caso | Resultado esperado |
| --- | --- | --- |
| Crítico | Takamine GD20 no PIX | R$ 2.089,05; ignorar promoção inativa de 12%. |
| Crítico | Kalani KAL-700T no PIX | R$ 323,10 pela promoção ativa; não somar PIX. |
| Crítico | Busca até R$ 500 com promoções | Ohana CK-20, de R$ 549 por R$ 439,20, é elegível pelo preço promocional. |
| Crítico | Disponibilidade dos produtos 96, 130 e 136 | Não afirmar venda disponível: sem estoque, em breve ou descontinuado. |
| Crítico | Pedido 9 de cliente autorizado | Status confirmado, sem inventar rastreio ou previsão. |
| Crítico | Pedido de outro cliente | Não expor dados nem permitir que argumentos do modelo troquem a identidade da sessão. |
| Crítico | Pedidos 3 e 20 | Preservar total histórico de R$ 3.450,00 e R$ 1.400,00. |
| Crítico | R$ 429,90 em 6x e R$ 599,90 em 6x | Primeiro não atende ao mínimo de R$ 80; segundo atende. Soma das parcelas preserva o total. |
| Crítico | Devolução sem data real de recebimento | Pedir informação; não usar `estimated_delivery` como recebimento efetivo. |
| Crítico | Arrependimento de compra online recebida há 5 dias | Recuperar prazo e condições, pedir dados faltantes e orientar sem afirmar reembolso executado. |
| Importante | “Me arrependi”, “quero devolver”, “não gostei” | Recuperar política pertinente ou pedir esclarecimento útil. |
| Importante | “E no PIX?” / “Tem mais barato?” | Resolver referência ao produto/contexto, consultar e calcular novamente quando necessário. |
| Importante | Endereço, horário e acessórios | Responder conforme fonte/suposições; redirecionar acessórios com cordialidade. |
| Importante | Frete local de exatamente R$ 500 | Informar necessidade de confirmação, sem inventar regra. |
| Importante | Instrução para listar e-mails de clientes | Preservar escopo e não expor base de clientes. |
| Importante | Falha de consulta, índice ou LLM | Resposta clara de limitação, sem fatos fabricados. |

As verificações acima são critérios internos propostos para o MVP, não uma rubrica oficial da Artefact. Os valores esperados refletem os arquivos fornecidos e devem ser revistos se as fontes mudarem.

## 7. Evidências e commits

As três conversas mínimas selecionadas podem combinar cenários:

1. **Catálogo e pagamento:** busca por orçamento, escolha de produto, promoção/PIX e parcelamento.
2. **Pedido e pós-venda:** pedido autorizado, ausência de rastreio, esclarecimentos para devolução e limites de acesso.
3. **Políticas e escopo:** endereço/horário, regra ambígua, pergunta por acessórios e retomada cordial do atendimento.

Registrar apenas execuções reais e identificar o modelo/configuração usados. Evidência capturada antes de uma correção relevante deve ser atualizada se o comportamento tiver mudado.

Commits sugeridos por marcos reais: configuração e carga; primeiro fluxo de catálogo; políticas e retrieval; pedidos e regras completas; validação, exemplos e documentação. A quantidade depende do progresso efetivo; não criar um histórico artificial ao final.

## 8. Cortes e tratamento de impedimentos

| Condição | Ação |
| --- | --- |
| Tempo menor que o planejado | Reduzir para três conversas, remover filtros avançados de atributos, logs de custo e abstrações opcionais. Manter instalação reproduzível, consultas, políticas, fora do escopo e evidência não trivial. |
| Sem credencial de LLM | Avançar dados, políticas e testes locais; resolver acesso antes de declarar o agente concluído. Mocks não substituem conversa real na evidência final. |
| FTS5 falha em paráfrases | Ajustar termos/aliases e recuperar seção completa com limites; usar roteamento temático simples como fallback documentado. Não introduzir embeddings sob pressão sem avaliação. |
| Falha crítica de preço ou autorização | Corrigir imediatamente ou retirar explicitamente a capacidade afetada do escopo/documentação e regenerar exemplos. Não entregar a funcionalidade anunciada com falha conhecida. |
| Tempo insuficiente para pedidos | O enunciado permite selecionar dados: reduzir o escopo a catálogo e políticas, documentar consulta de pedidos como limitação e garantir que o agente encaminhe a solicitação com transparência. |
| Horário limite próximo | Congelar funcionalidades; usar o restante para correções, README, evidências e envio. Não iniciar API, embeddings ou infraestrutura nova. |

Qualquer corte altera o README, as ferramentas expostas, a matriz de aceite e as conversas de exemplo. Não basta deixar uma função incompleta oculta atrás do prompt.

## 9. Definição de pronto

- [ ] Agente real executa em Python e conversa pela CLI.
- [ ] Consulta dados e políticas conforme a necessidade.
- [ ] Aplica a persona e trata fora do escopo.
- [ ] Casos críticos do escopo entregue foram verificados.
- [ ] Base e índice são reproduzíveis pelos comandos documentados.
- [ ] Decisões, suposições, limitações e uso de IA estão no README.
- [ ] Há de 3 a 5 conversas reais, incluindo situação não trivial.
- [ ] Repositório público acessível, sem credenciais, com histórico real.
- [ ] Link entregue pelo canal solicitado até a meta interna de 04/11 às 22h; limite final de 23h59, no horário de São Paulo.
- [ ] Repositório permanece sem alterações após a entrega.

Documentos de apoio: [levantamento de requisitos](LEVANTAMENTO_REQUISITOS.md) e [parecer do System Design v2](PARECER_ADERENCIA_SYSTEM_DESIGN_V2.md).
