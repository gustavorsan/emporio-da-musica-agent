# Parecer de aderência — System Design v2

Análise em 03/11/2026 da proposta anexada pelo usuário, comparada ao enunciado, ao manual e ao levantamento de requisitos do workspace.

## 1. Parecer

**A proposta tem alta aderência como arquitetura de MVP e pode servir de base para implementação após ajustes pontuais.** Ela contempla o núcleo funcional e os principais casos de borda do material fornecido. A aderência observada é documental: o agente ainda não foi implementado nem avaliado em conversas reais.

O núcleo recomendado é Python + SQLite + ferramentas tipadas + regras em Python + políticas em Markdown/YAML indexadas com FTS5. CLI e memória de sessão são compatíveis com a liberdade técnica do desafio. Nenhuma dessas escolhas exige infraestrutura adicional para o MVP.

Não atribuo percentual de aderência: o enunciado não oferece pesos nem rubrica numérica, e uma pontuação misturaria exigências obrigatórias com recomendações de engenharia.

## 2. Matriz de aderência ao desafio

| Item | Aderência na proposta | Avaliação |
| --- | --- | --- |
| Python como linguagem principal | Explícita em toda a stack. | Aderente. |
| Receber e responder texto | CLI e fluxo conversacional definidos. | Aderente. |
| Persona da loja | Há previsão de persona e system prompt, mas o tom não foi detalhado. | Parcial: explicitar informal, acolhedor e profissional, com exemplos. |
| Responder com contexto fornecido | CSVs no SQLite e manual convertido em documentos recuperáveis. | Aderente, desde que a conversão preserve conteúdo e ambiguidades. |
| Distinguir consulta a dados e políticas | Structured Retrieval e Knowledge Retrieval separados, combináveis nas perguntas mistas. | Aderente. |
| Tratar perguntas fora do escopo | Previsto nos objetivos e guardrails. | Aderente no desenho; incluir cenário explícito de demonstração. |
| Justificar decisões técnicas | ADRs cobrem banco, conhecimento, retrieval, ferramentas e interface. | Aderente; falta escolher e justificar modelo/provedor. |
| README de execução | Previsto no projeto e no plano de entrega. | Planejado; ainda precisa conter comandos e configuração reproduzíveis. |
| Limitações, melhorias e uso de IA | Previstos nas seções de escopo, evolução e entrega. | Aderente como planejamento. |
| De 3 a 5 conversas, com cenário não trivial | Cinco arquivos e captura de conversas reais previstos. | Aderente como planejamento. |
| Repositório público e progresso real em commits | Previstos no plano. | Aderente como planejamento. |
| Entrega por resposta ao e-mail e congelamento após prazo | Não explicitados no System Design v2. | Acrescentar ao checklist de entrega. O prazo concreto depende do e-mail. |

## 3. Decisões que vale manter

| Decisão | Por que está alinhada |
| --- | --- |
| SQLite para os seis CSVs | Representa as relações existentes, permite filtros e joins e centraliza a consulta de fatos operacionais. |
| Ferramentas em vez de SQL livre pelo LLM | Limita a superfície de consulta e mantém filtros, campos expostos e autorização no código. |
| Centavos inteiros e `Decimal` | Torna verificáveis preço final, desconto e parcela mínima. |
| Preservação do total histórico do pedido | Evita substituir o total registrado por uma reconstrução com preços atuais do catálogo. |
| Disponibilidade por status e estoque | Trata corretamente os produtos descontinuados ou em breve com estoque positivo. Essa é uma interpretação documentada para o protótipo. |
| Promoções pela flag de atividade | É coerente com a ausência de datas de vigência nos CSVs. |
| Identificação demonstrativa por sessão | Permite demonstrar titularidade sem fingir autenticação de produção. |
| Markdown com YAML para políticas | Facilita revisão e histórico de alterações. O texto define OKF como convenção local, sem depender de uma plataforma ou biblioteca externa. |
| Índice FTS5 reconstruível | Separa os documentos revisáveis da projeção usada na busca. |
| CLI e histórico em memória | Satisfazem o protótipo conversacional com implementação pequena. |
| Escopo sem operações reais | É consistente com a ausência de integrações de venda, reembolso, transportadora e atendimento humano. |
| Cenários de regressão e logs limitados | São complementos úteis para validar o comportamento, embora não sejam entregáveis obrigatórios do enunciado. |

## 4. Ajustes prioritários antes da implementação

### 4.1. Fechar os contratos das ferramentas

O contrato ilustrativo de `search_products` exige `query` com pelo menos dois caracteres. Isso cria um obstáculo para consultas apenas por categoria e orçamento ou para continuações como “tem mais barato?”. Recomendo texto opcional, exigindo pelo menos algum critério útil ou contexto resolvido pela aplicação.

Os objetivos também mencionam faixa de preço e atributos, mas o exemplo de contrato só contém preço máximo e não contém atributos, preço mínimo, ordenação ou disponibilidade. Definir quais filtros pertencem ao MVP e fazê-los aparecer no contrato. Para “mais barato”, a ordenação deve ser explícita ou feita deterministicamente no código.

Definir ainda se o orçamento filtra preço de tabela, promocional ou preço para o pagamento informado. Essa decisão muda resultados e precisa ficar clara para o usuário quando relevante; o desafio não determina uma única interpretação.

O diagrama cita busca por SKU, mas a origem fornece `product_id`, sem uma coluna SKU separada. Usar nome/ID ou definir explicitamente um alias, sem inventar um identificador comercial.

O contexto de cliente de `get_order_status` deve ser injetado pela aplicação. Não deve ser um campo editável pelo LLM como prova de autorização. A seção de segurança já prevê a intenção correta; falta torná-la inequívoca no contrato.

### 4.2. Tornar a recuperação FTS5 executável e avaliável

“Normalizar consulta” e “recuperação fraca” ainda são descrições abstratas. Especificar extração de termos, aliases e sinônimos de domínio, tratamento de caracteres da linguagem MATCH, ordenação e critérios observáveis para tentar nova busca ou pedir esclarecimento.

Exemplo de aceite: “me arrependi”, “quero devolver” e “não gostei da compra” devem recuperar a política pertinente ou provocar uma pergunta de esclarecimento, sem depender da expressão exata do manual.

Não encaminhar automaticamente a frase inteira ao MATCH: no FTS5, termos separados podem ser combinados com AND implícito, restringindo demais os resultados. O ranking BM25 ordena relevância textual; não deve ser apresentado como probabilidade de resposta correta. As particularidades de busca precisam ser avaliadas em um pequeno conjunto de perguntas. Fonte: [documentação oficial FTS5](https://www.sqlite.org/fts5.html).

Também limitar tags sugeridas pelo LLM a filtros opcionais: uma classificação errada não pode impedir nova busca em outras seções. “Retornar poucos trechos” deve preservar as exceções e condições necessárias; PIX e não cumulatividade precisam ser recuperados juntos.

### 4.3. Separar fonte original, interpretação e regra executável

Os OKFs são uma fonte derivada e revisada. O PDF original deve permanecer a referência para verificar fidelidade. Metadados recomendados: página(s), seção, versão do manual, versão do documento derivado e identificação das suposições adotadas.

O exemplo usa `version: 1.0.0`, mas não explicita que o manual é versão 2.1. Manter os dois conceitos distintos. Divergências de WhatsApp/e-mail e de parcelamento precisam de decisões explícitas; não podem desaparecer durante a curadoria.

Há duas representações das regras: textos OKF e código de domínio. Vincular a regra em Python à seção e à suposição correspondente e criar verificações de consistência para as regras implementadas. Uma edição no Markdown não altera automaticamente o cálculo do agente.

### 4.4. Definir a fronteira de leitura e escrita

O design proíbe SQL livre, mas ainda precisa explicitar conexões de atendimento em modo de leitura. Importação de CSVs e reconstrução do índice são operações separadas, executadas antes de abrir a sessão ou por comando de manutenção.

Registrar versão/hash das entradas ajuda a detectar quando os OKFs ou CSVs mudaram e o banco ficou desatualizado. Essa medida pode ser simples, sem criar um serviço de sincronização. A interface `sqlite3` permite abrir banco por URI em modo de leitura: [documentação do Python](https://docs.python.org/3/library/sqlite3.html).

### 4.5. Escolher modelo/provedor e completar a entrega

O documento ainda não escolhe o LLM. Definir uma opção com suporte às ferramentas necessárias, modo de configuração e justificativa de custo/qualidade. Isso é uma decisão pendente, não um erro de aderência.

Acrescentar ao checklist: versão do Python, instalação, geração do banco/índice, inicialização da CLI, variáveis necessárias, execução das verificações, exemplos reais, resposta ao e-mail e congelamento após a data/horário de entrega.

## 5. Complementos menores

- **DDL:** validar os valores aceitos de status, intervalo de desconto e total não negativo na carga e/ou em constraints. `discount_percent TEXT` pode preservar decimal exato, desde que haja validação antes da conversão para `Decimal`; não é um erro por si só.
- **Parcelas:** especificar como os centavos de diferença são distribuídos e como a parcela mínima é verificada após o arredondamento.
- **Origem dos dados:** os nomes de arquivos reais têm o prefixo `desafio_tecnico_ai_eng - `. Os scripts devem aceitar esses caminhos ou documentar a reorganização para `data/raw/`.
- **Publicação:** excluir o `.db` não resolve sozinho a exposição se os CSVs brutos também forem publicados. Definir uma política consistente para os dados fornecidos e preservar a reprodução do projeto.
- **Testes de agente:** separar cenários determinísticos de domínio de avaliações com LLM. Não exigir texto idêntico para respostas semanticamente corretas. Incluir persona, fora do escopo, continuidade e pergunta mista.
- **Garantias de comportamento:** prompt e testes demonstram controles e resultados observados; não comprovam ausência absoluta de alucinação ou prompt injection. Autorização, cálculos e bloqueio de escrita devem ser aplicados em código.
- **Contatos e horários:** documentar decisões sobre contatos divergentes, fuso da loja e ausência de calendário de feriados/horários especiais.

## 6. O que simplificar ou adiar

O número de caixas no diagrama não precisa virar o mesmo número de classes ou serviços. Orquestrador, definição de ferramentas e composição da resposta podem ser módulos pequenos do mesmo processo Python.

- Preferir inicialmente `sqlite3` entre as alternativas apresentadas; introduzir ORM apenas se houver benefício concreto para a implementação.
- Manter OKFs como Markdown/YAML simples, com poucos metadados necessários. Não criar um framework próprio de gestão de conhecimento.
- Dividir o manual por regras coesas, preservando contexto, sem criar chunks adicionais apenas para preencher uma etapa do pipeline.
- Começar com logs locais de ferramentas e fontes; métricas de custo podem ser adicionadas conforme o provedor escolhido.
- Adiar API HTTP, embeddings, reranking, autenticação real, ERP e tracing especializado conforme o próprio documento propõe.

## 7. Verificações realizadas e limites

- Leitura integral da proposta anexada e comparação com os requisitos já levantados dos materiais originais.
- Execução da DDL ilustrativa em um SQLite em memória: as seis tabelas e os índices foram criados sem erro; a verificação de chaves estrangeiras estava habilitada.
- Conferência do CSV de itens: não há pares repetidos `(order_id, product_id)` no recorte atual, portanto a chave composta sugerida é compatível com esses dados.
- FTS5 disponível no Python de runtime utilizado nesta análise. O ambiente de quem executar o projeto ainda deverá ser verificado/documentado.
- Não foi realizada importação completa, execução de agente, teste de recuperação do manual nem chamada ao LLM. A sintaxe válida da DDL não comprova os comportamentos descritos na proposta.

## 8. Encaminhamento recomendado

Adotar o System Design v2 como base, incorporando os ajustes de contratos, rastreabilidade, FTS5, conexões de leitura e modelo/provedor. O desenho central atende ao desafio; o próximo trabalho deve produzir um fluxo completo funcionando, incluindo pergunta mista e caso não trivial, antes de ampliar a estrutura de componentes.
