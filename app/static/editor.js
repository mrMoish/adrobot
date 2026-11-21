import {api, fieldFeedback, runOperation} from "./common.js";

const fetchForm = document.querySelector("#fetch");
const streams = document.querySelector("#streams");
const editorStatus = document.querySelector("#status");

const catalogList = document.querySelector("#editor-offers");
const catalogStatus = document.querySelector("#offer-status");
const offerIds = new Map();
const offerNames = new Map();
let catalog;

function offerLabel(id) {
    const label = node("span");
    label.dataset.offerId = String(id);
    label.textContent = formatOffer(id);
    return label;
}

function formatOffer(id) {
    const name = offerNames.get(String(id));
    return name ? name + " (#" + id + ")" : "#" + id;
}

function loadCatalog() {
    if (!catalog) {
        catalogStatus.textContent = "Загружаем офферы…";
        catalog = api("offers").then(offers => {
            populateSuggestions(offers, catalogList, offerIds);
            for (const {id, name} of offers) offerNames.set(String(id), name);
            for (const label of streams.querySelectorAll("[data-offer-id]")) {
                label.textContent = formatOffer(label.dataset.offerId);
            }
            catalogStatus.className = "";
            catalogStatus.textContent = offers.length ? "" : "В каталоге нет доступных офферов.";
        }).catch(error => {
            catalog = undefined;
            catalogStatus.className = "error";
            catalogStatus.textContent = "Не удалось загрузить офферы. " + error.message;
            throw error;
        });
    }
    return catalog;
}

const campaignList = fetchForm.elements.campaign;
const campaignError = fieldFeedback(campaignList);
const campaignStatus = document.querySelector("#campaign-status");
const fetchButton = fetchForm.querySelector("button[type=submit]");
const requestedId = new URLSearchParams(location.search).get("campaign_id");

const campaignIds = new Map();

function populateSuggestions(items, list, ids) {
    for (const {id, name} of items) {
        const label = name + " (#" + id + ")";
        list.append(new Option(label, label));
        ids.set(label, id);
        ids.set(String(id), id);
    }
}

const suggestions = document.querySelector("#editor-campaigns");
const allCampaigns = document.querySelector("#all-campaigns");
const listStatus = document.querySelector("#list-status");
const showCampaigns = document.querySelector("#show-campaigns");

function closeCampaignList() {
    allCampaigns.hidden = true;
    showCampaigns.textContent = "Показать все кампании";
    showCampaigns.setAttribute("aria-expanded", "false");
}

showCampaigns.onclick = async () => {
    if (!allCampaigns.hidden) {
        closeCampaignList();
        return;
    }
    clearTimeout(searchTimer);
    const version = ++searchVersion;
    showCampaigns.disabled = true;
    listStatus.textContent = "";
    campaignError();
    editorStatus.textContent = "";
    listStatus.className = "";
    listStatus.textContent = "Загружаем кампании…";
    try {
        const items = await api("campaigns");
        if (version !== searchVersion) return;
        if (!items.length) {
            listStatus.className = "error";
            listStatus.textContent = "Нет доступных кампаний.";
            return;
        }
        const options = document.createDocumentFragment();
        for (const {id, name} of items) options.append(new Option(name + " (#" + id + ")", id));
        allCampaigns.replaceChildren(options);
        allCampaigns.selectedIndex = -1;
        allCampaigns.hidden = false;
        showCampaigns.textContent = "Скрыть список";
        showCampaigns.setAttribute("aria-expanded", "true");
        listStatus.textContent = "";
        allCampaigns.focus();
    } catch (error) {
        if (version === searchVersion) {
            listStatus.className = "error";
            listStatus.textContent = error.message;
        }
    } finally {
        showCampaigns.disabled = false;
        if (version === searchVersion) campaignStatus.textContent = "";
    }
};

allCampaigns.onchange = () => {
    const option = allCampaigns.selectedOptions[0];
    if (!option) return;
    campaignIds.set(option.text, option.value);
    campaignList.value = option.text;
    campaignError();
    updateSearch();
    fetchButton.focus();
};

let searchTimer;
let searchVersion = 0;

function selectedCampaignId() {
    const value = campaignList.value.trim();
    return /^[1-9][0-9]*$/.test(value) ? value : campaignIds.get(value);
}

function updateSearch() {
    closeCampaignList();
    listStatus.textContent = "";
    clearTimeout(searchTimer);
    const version = ++searchVersion;
    const query = campaignList.value.trim();
    const selected = selectedCampaignId();
    fetchButton.disabled = !selected;
    campaignStatus.className = "";
    campaignStatus.textContent = "";
    if (selected) return;
    suggestions.replaceChildren();
    campaignIds.clear();
    if (!query) return;
    searchTimer = setTimeout(async () => {
        campaignStatus.textContent = "Ищем кампании…";
        try {
            const items = await api("campaigns/search?q=" + encodeURIComponent(query));
            if (version !== searchVersion) return;
            populateSuggestions(items, suggestions, campaignIds);
            campaignStatus.textContent = "";
            if (!items.length) campaignError("Кампании не найдены.");
        } catch (error) {
            if (version !== searchVersion) return;
            campaignStatus.textContent = "";
            campaignError("Не удалось найти кампании. " + error.message);
        }
    }, 250);
}

campaignList.addEventListener("input", updateSearch);
if (requestedId && /^[1-9][0-9]*$/.test(requestedId)) campaignList.value = requestedId;
updateSearch();

function node(tag, content) {
    const element = document.createElement(tag);
    if (content !== undefined) element.textContent = content;
    return element;
}

function groupStatus() {
    const status = node("small");
    status.setAttribute("role", "status");
    return status;
}

function rowStatus(body) {
    const row = body.insertRow();
    row.className = "row-feedback";
    const cell = row.insertCell();
    cell.colSpan = 3;
    const status = groupStatus();
    cell.append(status);
    return status;
}

function button(text, operation, status, disabled = false) {
    const element = node("button", text);
    element.type = "button";
    element.disabled = disabled;
    element.addEventListener("click", () => runOperation(operation, status));
    return element;
}

fetchForm.onsubmit = event => {
    event.preventDefault();
    listStatus.textContent = "";
    runOperation(async () => {
        campaignError();
        const campaignId = selectedCampaignId();
        if (!campaignId) throw new Error("Выберите кампанию из подсказок или введите её ID.");
        const data = await api("campaigns/" + campaignId + "/fetch", {});
        const displayOrder = [...data.filter(stream => stream.editable), ...data.filter(stream => !stream.editable)];
        streams.replaceChildren(...displayOrder.map(renderStream));
        loadCatalog().catch(() => {}); // Ошибка отображается отдельно от результата загрузки потоков.
        return data.length ? "Загружена кампания #" + campaignId : "В кампании нет потоков.";
    }, editorStatus);
};

if (new URLSearchParams(location.search).get("open") === "1" && selectedCampaignId()) {
    fetchForm.requestSubmit();
}

function renderStream(stream) {
    const article = node("article");
    const title = stream.editable && stream.name === "Офферы" ? "Поток" : stream.name;
    const heading = node("h3", title + " · #" + stream.id);
    if (!stream.editable) {
        article.className = "readonly-stream";
        article.append(heading);
        return article;
    }
    const details = node("div");
    details.append(heading);
    if (stream.conflict) details.append(node("p", "Поток изменился в Keitaro. Сбросьте черновик."));
    if (stream.dirty) details.classList.add("draft-stream");

    async function change(endpoint, payload = {}) {
        const state = await api("streams/" + stream.id + "/" + endpoint, payload);
        article.replaceWith(renderStream({...stream, ...state}));
    }
    const addForm = node("form");
    addForm.className = "actions";
    const label = node("label", "Добавление нового оффера");
    const offerInput = node("input");
    offerInput.setAttribute("list", "editor-offers");
    offerInput.placeholder = "Название или ID";
    offerInput.addEventListener("focus", () => loadCatalog().catch(() => {}));
    offerInput.required = true;
    label.append(offerInput);
    const add = node("button", "Добавить");
    add.disabled = stream.conflict;
    const status = groupStatus();
    const allOffers = node("select");
    allOffers.id = "all-offers-" + stream.id;
    allOffers.size = 7;
    allOffers.setAttribute("aria-label", "Все доступные офферы");
    const showOffers = node("button");
    showOffers.type = "button";
    showOffers.setAttribute("aria-controls", allOffers.id);
    function setOffersOpen(open) {
        allOffers.hidden = !open;
        showOffers.setAttribute("aria-expanded", String(open));
        showOffers.textContent = open ? "Скрыть список" : "Показать все офферы";
    }
    setOffersOpen(false);
    showOffers.onclick = async () => {
        if (!allOffers.hidden) {
            setOffersOpen(false);
            return;
        }
        showOffers.disabled = true;
        try {
            await loadCatalog();
            if (!offerNames.size) return;
            if (!allOffers.length) {
                const options = document.createDocumentFragment();
                for (const id of offerNames.keys()) options.append(new Option(formatOffer(id), id));
                allOffers.append(options);
            }
            allOffers.value = String(offerIds.get(offerInput.value.trim()) ?? "");
            setOffersOpen(true);
            allOffers.focus();
        } catch {
            // loadCatalog displays the error and allows retry.
        } finally {
            showOffers.disabled = false;
        }
    };
    allOffers.onchange = () => {
        if (!allOffers.value) return;
        offerInput.value = formatOffer(allOffers.value);
        status.textContent = "";
        setOffersOpen(false);
        if (add.disabled) offerInput.focus();
        else add.focus();
    };
    addForm.append(label, add, showOffers, status, allOffers);
    offerInput.addEventListener("invalid", () => {
        status.className = "error";
        status.textContent = offerInput.validationMessage;
    });
    offerInput.addEventListener("input", () => { status.textContent = ""; });
    addForm.onsubmit = event => {
        event.preventDefault();
        runOperation(async () => {
            await loadCatalog();
            const id = offerIds.get(offerInput.value.trim());
            if (!id) throw new Error("Выберите оффер из подсказок или введите его ID.");
            await change("edit", {action: "add", offer_id: id});
        }, status);
    };

    const table = node("table");
    table.className = "offer-table";
    const head = table.createTHead().insertRow();
    for (const text of ["Оффер", "Доля", "Действия"]) {
        const cell = node("th", text);
        cell.scope = "col";
        head.append(cell);
    }
    const body = table.createTBody();
    const isActive = ({state = "active"}) => state === "active";
    const activeCount = stream.offers.filter(isActive).length;
    for (const offer of stream.offers) {
        const id = offer.offer_id;
        const pin = stream.pins[id];
        const row = body.insertRow();
        const feedback = rowStatus(body);
        row.insertCell().append(offerLabel(id), offer.state === "disabled" ? " · отключён" : "");
        row.insertCell().textContent = offer.share + "%" + (pin ? " · закреплено" : "");
        const cell = row.insertCell();
        const actions = node("div");
        actions.className = "actions";
        cell.append(actions);
        if (pin) {
            actions.append(button("Открепить", () => change("edit", {action: "unpin", offer_id: id}), feedback, stream.conflict));
        } else if (isActive(offer) && offer.share >= 1 && offer.share <= 100) {
            actions.append(button("Закрепить", () => change("edit", {
                action: "pin", offer_id: id, share: offer.share,
            }), feedback, stream.conflict));
        }
        if (activeCount > 1 || (activeCount === 1 && !isActive(offer))) {
            actions.append(button("Remove", () => change("edit", {action: "remove", offer_id: id}), feedback, stream.conflict));
        }
    }
    for (const id of stream.archive) {
        const row = body.insertRow();
        const feedback = rowStatus(body);
        row.className = "archived";
        row.insertCell().append(offerLabel(id), " · архив");
        row.insertCell().textContent = "0%";
        row.insertCell().append(button("Bring Back", () => change("edit", {action: "restore", offer_id: id}), feedback, stream.conflict));
    }
    details.append(table);
    const operations = node("div");
    operations.className = "actions publish-actions";
    const operationStatus = groupStatus();
    operations.append(
        button("Push", () => change("push"), operationStatus, !stream.dirty || stream.conflict),
        button("Отмена", () => change("cancel"), operationStatus, !stream.dirty),
    );
    if (stream.dirty) {
        const notice = node("strong", "Есть неопубликованные изменения");
        notice.className = "draft-notice";
        operations.append(notice);
    }
    operations.append(operationStatus);
    details.append(operations);
    article.append(details, addForm);
    return article;
}
