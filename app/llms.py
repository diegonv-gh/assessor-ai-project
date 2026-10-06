#Modelo Gemini via Langchain

from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_groq import ChatGroq
from app.config import GROQ_API_KEY, GEMINI_API_KEY

GROQ_API = "openai/gpt-oss-120b"
GROQ_FAST_API = "llama-3.3-70b-versatile"


llm_gemini = ChatGoogleGenerativeAI(
    model="gemini-2.5-flash",
    temperature=0.7,
    top_p=0.95,
    google_api_key=GEMINI_API_KEY,
)

llm_groq = ChatGroq( # modelo Groq via Langchain, usado quando o Gemini falha ou não tem resposta
    model=GROQ_API,
    temperature=0.7,
    top_p=0.95,
    max_tokens=768,
    api_key=GROQ_API_KEY,
)

llm_rapido = ChatGroq(
    model=GROQ_FAST_API,
    temperature=0.0,
    max_tokens=512,
    api_key=GROQ_API_KEY,
)

llm_especialista = llm_gemini.with_fallbacks([llm_groq])

# Alias mantido para os módulos que ainda usam o nome histórico.
llm = llm_especialista
