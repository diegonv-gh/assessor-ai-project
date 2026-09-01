import psycopg2

from app.config import DATABASE_URL

def get_conn():
    """Estabelece uma conexão com o banco de dados PostgreSQL."""
    if not DATABASE_URL or not DATABASE_URL.strip():
        raise RuntimeError("DATABASE_URL não está configurada.")
    return psycopg2.connect(DATABASE_URL)
