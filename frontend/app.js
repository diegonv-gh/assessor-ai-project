// ============================================================
// Configuração
// ============================================================
// Ajuste para a URL onde o FastAPI está rodando.
const API_BASE =
    window.location.protocol === "file:" ? "http://localhost:8000" : "";
const CHAT_ENDPOINT = `${API_BASE}/chat`;

// ============================================================
// Elementos
// ============================================================
const thread = document.getElementById("thread");
const threadEmpty = document.getElementById("thread-empty");
const composer = document.getElementById("composer");
const inputQuestion = document.getElementById("question");
const botaoEnviar = document.getElementById("enviar");
const sessionIdEl = document.getElementById("session-id");
const resetButton = document.getElementById("reset-session");
const statusDot = document.getElementById("status-dot");
const hint = document.getElementById("hint");

// ============================================================
// Sessão
// ============================================================
const SESSION_STORAGE_KEY = "assistente_session_id";

function gerarSessionId() {
    if (window.crypto && crypto.randomUUID) {
        return crypto.randomUUID();
    }
    return "sess-" + Math.random().toString(36).slice(2, 10);
}

function obterOuCriarSessionId() {
    let id = localStorage.getItem(SESSION_STORAGE_KEY);
    if (!id) {
        id = gerarSessionId();
        localStorage.setItem(SESSION_STORAGE_KEY, id);
    }
    return id;
}

async function obterUserId() {
    const resposta = await fetch(`${API_BASE}/identidade`, { credentials: "include" });
    if (!resposta.ok) throw new Error("Não foi possível obter a identidade do usuário.");
    const dados = await resposta.json();
    return dados.user_id;
}

// Encerra a conversa anterior antes de começar outra. O resumo gerado pelo
// servidor é o que torna a conversa pesquisável pela memória de longo prazo.
async function encerrarSessaoAtual(id) {
    try {
        const resposta = await fetch(
            `${API_BASE}/sessions/${encodeURIComponent(id)}/encerrar`,
            {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ user_id: userId }),
            }
        );
        if (!resposta.ok) return null;
        const dados = await resposta.json();
        return dados.resumo;
    } catch (erro) {
        console.warn("Não foi possível encerrar a sessão anterior:", erro);
        return null;
    }
}

async function iniciarNovaSessao() {
    const anterior = sessionId;

    resetButton.disabled = true;
    setHint("Encerrando a sessão anterior…");
    const resumo = await encerrarSessaoAtual(anterior);

    const id = gerarSessionId();
    localStorage.setItem(SESSION_STORAGE_KEY, id);
    // Atualizar o localStorage não muda a variável usada pelo POST /chat.
    sessionId = id;
    exibirSessionId(id);

    thread.innerHTML = "";
    thread.appendChild(threadEmpty);
    threadEmpty.style.display = "block";
    setHint(
        resumo
            ? `Sessão anterior encerrada. Resumo: ${resumo}`
            : "Nova sessão iniciada."
    );
    resetButton.disabled = false;
}

function exibirSessionId(id) {
    sessionIdEl.textContent = id.length > 12 ? id.slice(0, 8) + "…" : id;
    sessionIdEl.title = id;
}

let userId = null;
let sessionId = obterOuCriarSessionId();
exibirSessionId(sessionId);

obterUserId()
    .then((id) => { userId = id; })
    .catch((erro) => { setHint(erro.message, true); });

resetButton.addEventListener("click", iniciarNovaSessao);

// ============================================================
// Utilidades de UI
// ============================================================
function setHint(texto, comoErro = false) {
    hint.textContent = texto || "";
    hint.classList.toggle("is-error", comoErro);
}

function marcarStatus(online) {
    statusDot.classList.toggle("is-offline", !online);
}

function escaparHtml(texto) {
    const div = document.createElement("div");
    div.textContent = texto;
    return div.innerHTML;
}

function rolarParaFinal() {
    thread.scrollTop = thread.scrollHeight;
}

function adicionarMensagem({ tipo, texto, agentes }) {
    threadEmpty.style.display = "none";

    const wrapper = document.createElement("div");
    wrapper.className = `message message--${tipo}`;

    const label = document.createElement("div");
    label.className = "message__label";
    label.textContent = tipo === "user" ? "você" : tipo === "error" ? "erro" : "assistente";
    wrapper.appendChild(label);

    const bubble = document.createElement("div");
    bubble.className = "message__bubble";
    bubble.innerHTML = escaparHtml(texto);
    wrapper.appendChild(bubble);

    if (agentes && agentes.length > 0) {
        const agentesEl = document.createElement("div");
        agentesEl.className = "message__agents";
        agentes.forEach((nome) => {
            const tag = document.createElement("span");
            tag.className = "agent-tag";
            tag.textContent = nome;
            agentesEl.appendChild(tag);
        });
        wrapper.appendChild(agentesEl);
    }

    thread.appendChild(wrapper);
    rolarParaFinal();
    return wrapper;
}

function adicionarIndicadorDigitando() {
    const wrapper = document.createElement("div");
    wrapper.className = "message message--assistant";
    wrapper.id = "typing-indicator";

    const label = document.createElement("div");
    label.className = "message__label";
    label.textContent = "assistente";
    wrapper.appendChild(label);

    const bubble = document.createElement("div");
    bubble.className = "message__bubble";
    bubble.innerHTML = `<span class="typing"><span></span><span></span><span></span></span>`;
    wrapper.appendChild(bubble);

    thread.appendChild(wrapper);
    rolarParaFinal();
}

function removerIndicadorDigitando() {
    const el = document.getElementById("typing-indicator");
    if (el) el.remove();
}

// ============================================================
// Textarea: auto-resize + enviar com Enter
// ============================================================
inputQuestion.addEventListener("input", () => {
    inputQuestion.style.height = "auto";
    inputQuestion.style.height = Math.min(inputQuestion.scrollHeight, 140) + "px";
});

inputQuestion.addEventListener("keydown", (evento) => {
    if (evento.key === "Enter" && !evento.shiftKey) {
        evento.preventDefault();
        composer.requestSubmit();
    }
});

// ============================================================
// Envio da pergunta
// ============================================================
composer.addEventListener("submit", async (evento) => {
    evento.preventDefault();

    if (!userId) {
        setHint("A identidade do usuário ainda não foi carregada.", true);
        return;
    }

    const question = inputQuestion.value.trim();
    if (!question) return;

    adicionarMensagem({ tipo: "user", texto: question });
    inputQuestion.value = "";
    inputQuestion.style.height = "auto";
    setHint("");

    botaoEnviar.disabled = true;
    inputQuestion.disabled = true;
    adicionarIndicadorDigitando();

    try {
        const resposta = await fetch(CHAT_ENDPOINT, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                pergunta: question,
                user_id: userId,
                session_id: sessionId
            })
        });

        if (!resposta.ok) {
            const detalhe = await resposta.text();
            throw new Error(
                `A API respondeu com status ${resposta.status}. ${detalhe || "Sem detalhe no corpo da resposta."}`
            );
        }

        const dados = await resposta.json();
        marcarStatus(true);
        removerIndicadorDigitando();

        adicionarMensagem({
            tipo: "assistant",
            texto: dados.resposta ?? "(resposta vazia)",
            agentes: dados.agentes_chamados,
        });
    } catch (erro) {
        marcarStatus(false);
        removerIndicadorDigitando();
        adicionarMensagem({
            tipo: "error",
            texto: erro.message.includes("Failed to fetch")
                ? `Não foi possível falar com a API em ${API_BASE}. Verifique se o servidor está rodando e se o CORS está liberado.`
                : erro.message,
        });
        setHint(erro.message, true);
        console.error(erro);
    } finally {
        botaoEnviar.disabled = false;
        inputQuestion.disabled = false;
        inputQuestion.focus();
    }
});

// Foco inicial
inputQuestion.focus();
