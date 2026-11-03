# Agente de atendimento — Empório da Música

Protótipo em Python de um agente de atendimento por texto para a loja fictícia Empório da Música. Em construção: este README será completado ao longo da implementação.

## Como executar

Requer Python 3.11 ou superior (validado em 3.14.6) e uma chave da API da OpenAI.

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -e .[dev]
Copy-Item .env.example .env   # preencha OPENAI_API_KEY e OPENAI_MODEL
```
