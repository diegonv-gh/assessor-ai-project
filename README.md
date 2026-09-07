# Assessor.AI

Assistente financeiro com FastAPI, LangGraph, LangChain, PostgreSQL, MongoDB e Qdrant.

O sistema conversa com o usuário, consulta e registra transações financeiras, responde dúvidas com base em um FAQ e recupera informações relevantes de sessões anteriores.

## Principais recursos

- Roteamento entre agentes especializados.
- Consulta, cadastro, atualização e cálculo de saldo financeiro.
- Busca semântica no FAQ em PDF.
- Histórico de curto prazo por sessão.
- Resumos de sessões encerradas armazenados no MongoDB e indexados no Qdrant.
- Interface web incluída em `frontend/`.

## Serviços utilizados

- PostgreSQL: dados financeiros.
- MongoDB: mensagens e resumos das sessões.
- Qdrant: embeddings do FAQ e do histórico.
- Gemini e Groq: modelos de linguagem e embeddings.

Collections do Qdrant:

- `faq_chunks`
- `historico_resumos`

## Requisitos

- Python 3.10 ou superior.
- PostgreSQL, MongoDB e Qdrant configurados.
- Chaves de API do Gemini, Groq e Qdrant.

## Configuração

Crie um arquivo `.env` na raiz do projeto. O arquivo já é ignorado pelo Git e não deve conter valores publicados no repositório:

```env
GEMINI_API_KEY=<sua-chave>
GROQ_API_KEY=<sua-chave>
DATABASE_URL=<sua-connection-string-do-postgresql>
MONGODB_URI=<sua-url-do-mongodb>
MONGODB_DB=assessor
QDRANT_URL=<sua-url-do-qdrant>
QDRANT_API_KEY=<sua-chave>
```

Nunca substitua os placeholders por chaves reais no `README.md` ou em qualquer arquivo versionado.

## Instalação e execução

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install fastapi "uvicorn[standard]" pydantic python-dotenv psycopg2-binary pymongo qdrant-client langchain langgraph langchain-google-genai langchain-groq langchain-community langchain-text-splitters pypdf
uvicorn app.main:app --reload
```

Com o servidor em execução:

- Aplicação: http://localhost:8000
- Documentação da API: http://localhost:8000/docs
- Verificação de configuração: `GET /health`

## Indexação do FAQ

Depois de configurar o Qdrant, indexe o PDF oficial:

```powershell
python -m app.ingest_faq
```

O comando gera embeddings e atualiza a collection `faq_chunks`. Execute-o novamente quando o PDF for alterado.

## Uso da API

### Conversa

`POST /chat`

```json
{
  "user_id": "usuario-exemplo",
  "session_id": "sessao-exemplo",
  "pergunta": "Qual é o meu saldo total?"
}
```

### Encerrar uma sessão

`POST /sessions/{session_id}/encerrar`

```json
{
  "user_id": "usuario-exemplo"
}
```

O encerramento gera o resumo que será usado pela memória semântica em conversas futuras. O mesmo `user_id` deve ser mantido entre sessões do mesmo usuário; o `session_id` deve mudar a cada nova conversa.

## Teste rápido da memória

1. Envie uma mensagem sobre um plano ou preferência usando um `session_id`.
2. Encerre essa sessão pela rota acima.
3. Use outro `session_id`, mantendo o mesmo `user_id`.
4. Pergunte sobre o assunto anterior.

O histórico só fica disponível para busca semântica depois que a sessão é encerrada e resumida.

## Segurança

- Não versione `.env`, chaves, senhas ou connection strings reais.
- Revise o `git diff` antes de publicar alterações.
- A configuração de CORS atual é voltada para desenvolvimento; restrinja as origens antes de usar em produção.

## Status

Projeto acadêmico em desenvolvimento.
