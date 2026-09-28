"""Persistência e consulta do perfil financeiro do usuário."""

import os
import uuid
from datetime import datetime, timezone

from langchain_core.runnables import RunnableConfig
from langchain_core.tools import tool
from pymongo import MongoClient
from qdrant_client import models

from app.config import MONGODB_DB, MONGODB_URI
from app.schemas import PerfilRequest
from app.vectorstore import EMBEDDING_DIM, gerar_embedding, qdrant


COLLECTION_PERFIL = os.getenv("QDRANT_COLLECTION_PERFIL", "perfil_preferencias")
_PERFIL_NAMESPACE = uuid.UUID("f2f884b3-9a69-45a9-8f5f-cb0b73e4b7a4")

_mongo = MongoClient(MONGODB_URI)
db = _mongo[MONGODB_DB]
col_perfis = db["perfis"]


def _ponto_id(user_id: str) -> str:
    """Gera um ID estável para substituir o vetor anterior do usuário."""
    return str(uuid.uuid5(_PERFIL_NAMESPACE, user_id))


def _garantir_collection() -> None:
    if not qdrant.collection_exists(COLLECTION_PERFIL):
        qdrant.create_collection(
            collection_name=COLLECTION_PERFIL,
            vectors_config=models.VectorParams(
                size=EMBEDDING_DIM,
                distance=models.Distance.COSINE,
            ),
        )

    qdrant.create_payload_index(
        collection_name=COLLECTION_PERFIL,
        field_name="user_id",
        field_schema=models.PayloadSchemaType.KEYWORD,
    )


def salvar_perfil(perfil: PerfilRequest) -> dict:
    """Substitui o perfil estruturado e o vetor de preferências do usuário."""
    dados = perfil.model_dump()
    agora = datetime.now(timezone.utc)
    vetor = gerar_embedding(perfil.preferencias)

    col_perfis.replace_one(
        {"user_id": perfil.user_id},
        {**dados, "atualizado_em": agora},
        upsert=True,
    )

    _garantir_collection()
    qdrant.upsert(
        collection_name=COLLECTION_PERFIL,
        points=[
            models.PointStruct(
                id=_ponto_id(perfil.user_id),
                vector=vetor,
                payload={
                    "user_id": perfil.user_id,
                    "preferencias": perfil.preferencias,
                    "atualizado_em": agora.isoformat(),
                },
            )
        ],
    )
    return dados


@tool
def consultar_perfil(busca: str, config: RunnableConfig) -> str:
    """Consulta o perfil para contextualizar orientação financeira personalizada.

    Recupera os campos estruturados e as preferências por busca semântica.

    O ``user_id`` vem do contexto da requisição e não é um argumento que o
    modelo possa preencher.
    """
    configuravel = (config or {}).get("configurable", {})
    user_id = configuravel.get("user_id")
    if not user_id:
        return "Não foi possível identificar o usuário para consultar o perfil."

    perfil = col_perfis.find_one({"user_id": user_id}, {"_id": 0})
    if not perfil:
        return "Nenhum perfil cadastrado. Oriente o usuário a usar a tela Perfil."

    try:
        resultados = qdrant.query_points(
            collection_name=COLLECTION_PERFIL,
            query=gerar_embedding(busca),
            query_filter=models.Filter(
                must=[
                    models.FieldCondition(
                        key="user_id",
                        match=models.MatchValue(value=user_id),
                    )
                ]
            ),
            limit=1,
            with_payload=True,
        )
    except Exception:
        return "Não foi possível consultar as preferências do perfil agora."

    preferencias = ""
    if resultados.points:
        preferencias = (resultados.points[0].payload or {}).get("preferencias", "")
    if not preferencias:
        return "Não foi possível recuperar as preferências semânticas do perfil agora."

    return (
        f"renda_mensal: {perfil['renda_mensal']}\n"
        f"objetivo: {perfil['objetivo']}\n"
        f"tolerancia_risco: {perfil['tolerancia_risco']}\n"
        f"preferencias_relevantes: {preferencias}"
    )


TOOLS_PERFIL = [consultar_perfil]
