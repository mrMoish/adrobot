export async function api(path, body) {
    const options = body === undefined ? {} : {
        method: "POST",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify(body),
    };
    const response = await fetch("/api/" + path, options);
    let data;
    try {
        data = await response.json();
    } catch {
        throw new Error("Сервер вернул нечитаемый ответ (HTTP " + response.status + ").");
    }
    if (!response.ok) {
        const error = new Error(typeof data.detail === "string" ? data.detail : "Проверьте введённые значения.");
        error.field = response.headers.get("X-Error-Field") || data.detail?.[0]?.loc?.[1];
        error.status = response.status;
        throw error;
    }
    return data;
}

let fieldNumber = 0;
export function fieldFeedback(input) {
    const message = document.createElement("small");
    message.id = "field-error-" + ++fieldNumber;
    message.className = "field-error error";
    message.setAttribute("aria-live", "polite");
    input.after(message);
    input.setAttribute("aria-describedby", message.id);
    function show(text = "") {
        message.textContent = text;
        if (text) input.setAttribute("aria-invalid", "true");
        else input.removeAttribute("aria-invalid");
    }
    input.addEventListener("input", () => show());
    input.addEventListener("invalid", () => show(input.validationMessage));
    return show;
}

export let busy = false;
export async function runOperation(operation, output, pending = "Загрузка…") {
    if (busy) return;
    busy = true;
    document.querySelector("main").setAttribute("aria-busy", "true");
    delete output.dataset.offerSearch;
    output.className = "";
    output.textContent = pending;
    try {
        output.textContent = await operation() || "";
        output.className = "success";
    } catch (error) {
        output.className = "error";
        output.textContent = error.message;
    } finally {
        busy = false;
        document.querySelector("main").removeAttribute("aria-busy");
    }
}
