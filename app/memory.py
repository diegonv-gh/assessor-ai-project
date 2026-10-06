"""Persistência e recuperação da memória de longo prazo do assessor.

O checkpointer do LangGraph mantém a conversa atual em memória de processo.
Este módulo mantém as sessões no MongoDB para que uma conversa encerrada possa
ser resumida e consultada em conversas posteriores.
"""

import re
import uuid
from datetime import datetime, timezone

from pymongo import MongoClient

from app.config import MONGODB_DB, MONGODB_URI
from app.llms import llm_rapido

from qdrant_client import models
from app.vectorstore import qdrant, COLLECTION_MEMORIA, gerar_embedding

_mongo = MongoClient(MONGODB_URI)
db = _mongo[MONGODB_DB]
col_sessoes = db["sessoes"]

_llm_resumo = llm_rapido

_PROMPT_RESUMO = """\
Você é um assistente que resume conversas de assessoria financeira e agenda.
Gere um resumo conciso em 2-4 frases capturando:
- O que o usuário fez (transações registradas, eventos agendados)
- O que o usuário perguntou
- Informações relevantes mencionadas (valores, datas, categorias)

Responda APENAS com o resumo, sem introdução ou explicação.

Conversa:
{conversa}
"""

# Tokens de PII não podem virar uma memória aparentemente útil, mas impossível
# de resolver depois que o mapa de anonimização da requisição desaparece.
_TOKEN_PII = re.compile(r"\[PII_[A-Z]+_[0-9a-f]+\]", re.IGNORECASE)


def _agora() -> datetime:
    return datetime.now(timezone.utc)


def _formatar_conversa(mensagens: list[dict]) -> str:
    """Formata mensagens para o prompt de resumo."""
    return "\n".join(
        f"{mensagem.get('role', 'mensagem')}: {mensagem.get('content', '')}"
        for mensagem in mensagens
    )


def _limpar_tokens_pii(texto: str) -> str:
    """Evita salvar no resumo tokens que não poderão ser desanonimizados."""
    return _TOKEN_PII.sub("[DADO PESSOAL OMITIDO]", texto)


def _gerar_resumo(mensagens: list[dict]) -> str:
    """Gera e higieniza o resumo da sessão."""
    resumo = _llm_resumo.invoke(
        _PROMPT_RESUMO.format(conversa=_formatar_conversa(mensagens))
    ).content.strip()
    return _limpar_tokens_pii(resumo)


def _doc_id_da_sessao(session_id: str, user_id: str | None = None) -> str | None:
    """Encontra no MongoDB a sessão em andamento para este usuário."""
    filtro = {
        "session_id": session_id,
        "resumo": {"$in": ["", None]},
    }
    if user_id:
        filtro["user_id"] = user_id
    doc = col_sessoes.find_one(
        filtro,
        {"_id": 1},
        sort=[("iniciada_em", -1)],
    )
    if not doc:
        return None

    return str(doc["_id"])


def iniciar_sessao(session_id: str, user_id: str = "usuario_teste") -> None:
    """Garante um documento aberto para ``session_id``."""
    if _doc_id_da_sessao(session_id, user_id=user_id):
        return

    doc_id = str(uuid.uuid4())
    agora = _agora()
    col_sessoes.insert_one(
        {
            "_id": doc_id,
            "session_id": session_id,
            "user_id": user_id,
            "iniciada_em": agora,
            "atualizada_em": agora,
            "resumo": "",
            "mensagens": [],
        }
    )


def salvar_mensagem(
    session_id: str,
    role: str,
    content: str,
    user_id: str = "usuario_teste",
) -> None:
    """Adiciona uma mensagem à sessão aberta, abrindo-a se necessário."""
    iniciar_sessao(session_id, user_id=user_id)
    doc_id = _doc_id_da_sessao(session_id, user_id=user_id)
    if not doc_id:
        raise RuntimeError("Não foi possível localizar a sessão para salvar a mensagem.")

    col_sessoes.update_one(
        {"_id": doc_id},
        {
            "$push": {"mensagens": {"role": role, "content": content}},
            "$set": {"atualizada_em": _agora()},
        },
    )


def _data_para_payload(valor) -> str:
    """Converte datetime do Mongo ou texto legado para o payload do Qdrant."""
    if hasattr(valor, "isoformat"):
        return valor.isoformat()
    return str(valor)


def encerrar_sessao(session_id: str, user_id: str | None = None) -> str:
    """Resume e fecha a sessão aberta; retorna vazio se nada houver para fechar."""
    doc_id = _doc_id_da_sessao(session_id, user_id=user_id)
    if not doc_id:
        return ""

    doc = col_sessoes.find_one({"_id": doc_id})
    if not doc or not doc.get("mensagens"):
        return ""

    resumo = _gerar_resumo(doc["mensagens"])
    col_sessoes.update_one(
        {"_id": doc_id},
        {"$set": {"resumo": resumo, "atualizada_em": _agora()}},
    )

    # Salva o embedding do resumo no Qdrant para busca semântica futura.
    # O filtro de multitenancy usa user_id (estável entre sessões), não session_id.
    user_id = doc.get("user_id", "usuario_teste")
    vetor = gerar_embedding(resumo)
    qdrant.upsert(
        collection_name=COLLECTION_MEMORIA,
        points=[
            models.PointStruct(
                id=doc_id,
                vector=vetor,
                payload={
                    "user_id":     user_id,
                    "session_id":  session_id,
                    "resumo":      resumo,
                    "iniciada_em": _data_para_payload(doc["iniciada_em"]),
                },
            )
        ],
    )

    return resumo


def recuperar_historico(user_id: str, busca: str = "", limite: int = 3) -> list[dict]:
    if busca:
        try:
            vetor = gerar_embedding(busca)
            resultados = qdrant.query_points(
                collection_name=COLLECTION_MEMORIA,
                query=vetor,
                query_filter=models.Filter(
                    must=[
                        models.FieldCondition(
                            key="user_id",
                            match=models.MatchValue(value=user_id),
                        )
                    ]
                ),
                limit=limite,
            )
        except Exception:
            # Mongo continua sendo a fonte de verdade. Se o índice semântico
            # estiver indisponível, a consulta recente ainda funciona.
            resultados = None

        if resultados and resultados.points:
            encontrados = []
            for ponto in resultados.points:
                payload = ponto.payload or {}
                resumo = payload.get("resumo")
                if resumo:
                    encontrados.append(
                        {
                            "doc_id": ponto.id,
                            "iniciada_em": payload.get("iniciada_em", ""),
                            "resumo": resumo,
                        }
                    )
            if encontrados:
                return encontrados

    filtro = {"user_id": user_id, "resumo": {"$nin": ["", None]}}
    docs = (
        col_sessoes
        .find(filtro, {"resumo": 1, "iniciada_em": 1})
        .sort("iniciada_em", -1)
        .limit(limite)
    )

    return [
        {"doc_id": d["_id"], "iniciada_em": d["iniciada_em"], "resumo": d["resumo"]}
        for d in docs
    ]


def recuperar_mensagens(doc_id: str) -> list[dict]:
    """Recupera as mensagens completas de uma sessão específica."""
    doc = col_sessoes.find_one({"_id": doc_id}, {"mensagens": 1})
    return doc.get("mensagens", []) if doc else []


def recuperar_mensagens_sessao(session_id: str, user_id: str) -> list[dict]:
    """Recupera mensagens pelo identificador público da sessão e pelo usuário."""
    doc = col_sessoes.find_one(
        {"session_id": session_id, "user_id": user_id},
        {"_id": 1},
        sort=[("iniciada_em", -1)],
    )
    return recuperar_mensagens(str(doc["_id"])) if doc else []


def salvar_turno(
    session_id: str,
    pergunta: str,
    resposta: str,
    user_id: str | None = None,
) -> None:
    """Compatibilidade para consumidores antigos que salvam um turno inteiro."""
    salvar_mensagem(session_id, "user", pergunta, user_id=user_id)
    salvar_mensagem(session_id, "assistant", resposta, user_id=user_id)
