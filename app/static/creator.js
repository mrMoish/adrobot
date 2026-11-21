import {api, fieldFeedback, busy, runOperation} from "./common.js";

const create = document.querySelector("#create");
const createStatus = document.querySelector("#create-status");
const createButton = create.querySelector("button");

const campaignOffer = create.elements.offer;
const country = create.elements.country;
const campaignOfferList = document.querySelector("#campaign-offers");
const offerLoading = document.querySelector("#offer-loading");
const offerIds = new Map();
let offerCatalog;

const catalogError = document.querySelector("#offer-catalog-error");
fieldFeedback(campaignOffer);
create.addEventListener("invalid", event => {
    createStatus.className = "error";
    createStatus.textContent = event.target.validationMessage;
}, true);

function loadOffers() {
    if (!offerCatalog) {
        catalogError.textContent = "";
        offerLoading.textContent = "Загружаем офферы…";
        offerCatalog = api("offers").then(offers => {
            const counts = new Map();
            for (const {name} of offers) counts.set(name, (counts.get(name) || 0) + 1);
            for (const {id, name} of offers) {
                let value = name;
                if (counts.get(name) > 1) {
                    value = name + " (#" + id + ")";
                    while (counts.has(value) || offerIds.has(value)) value += " (#" + id + ")";
                }
                offerIds.set(value, id);
                campaignOfferList.append(new Option(value));
            }
            return true;
        }).catch(error => {
            offerCatalog = undefined;
            catalogError.textContent = "Не удалось загрузить офферы. " + error.message;
            return false;
        }).finally(() => {
            offerLoading.textContent = "";
        });
    }
    return offerCatalog;
}

loadOffers();
campaignOffer.addEventListener("focus", loadOffers);

for (const input of [campaignOffer, country]) {
    input.addEventListener("input", () => input.setCustomValidity(""));
}

const historyKey = "adrobot.createdCampaigns";
let createdCampaigns = [];
try {
    const saved = JSON.parse(sessionStorage.getItem(historyKey));
    if (Array.isArray(saved)) createdCampaigns = saved.filter(item =>
        Number.isSafeInteger(item?.id) && item.id > 0 && typeof item.name === "string");
} catch {
    // History still works in memory if browser storage is unavailable.
}

function renderCreatedCampaigns() {
    const list = document.querySelector("#created-campaigns");
    list.replaceChildren();
    for (const {id, name} of createdCampaigns) {
        const row = document.createElement("li");
        const title = document.createElement("span");
        title.textContent = name + " · #" + id;
        const edit = document.createElement("a");
        edit.href = "/editor?campaign_id=" + id + "&open=1";
        edit.textContent = "Редактировать";
        edit.setAttribute("aria-label", "Редактировать " + name + " (#" + id + ")");
        row.append(title, edit);
        list.append(row);
    }
    document.querySelector("#created-section").hidden = !createdCampaigns.length;
}

renderCreatedCampaigns();

create.onsubmit = async event => {
    event.preventDefault();
    if (busy) return;
    const codes = country.value.split(",").map(code => code.trim());
    country.setCustomValidity(codes.every(code => /^[A-Za-z]{2}$/.test(code))
        ? "" : "Введите двухбуквенные коды стран через запятую, например MX,AU,RO.");
    if (!country.reportValidity()) return;
    country.value = [...new Set(codes.map(code => code.toUpperCase()))].join(",");
    createButton.disabled = true;
    try {
        await runOperation(async () => {
            if (!await loadOffers()) return;
            const offerId = offerIds.get(campaignOffer.value);
            if (!offerId) throw new Error("Выберите существующий оффер из списка.");
            const name = create.elements.namedItem("name").value;
            const result = await api("campaigns", {
                name,
                country: country.value,
                offer_id: offerId,
            });
            if (!Number.isSafeInteger(Number(result?.id)) || Number(result.id) <= 0) {
                throw new Error("Сервер не подтвердил ID кампании. Проверьте список в Keitaro перед повторной отправкой.");
            }
            const id = Number(result.id);
            createdCampaigns = [{id, name}, ...createdCampaigns.filter(item => item.id !== id)];
            renderCreatedCampaigns();
            try {
                sessionStorage.setItem(historyKey, JSON.stringify(createdCampaigns));
            } catch {
                // Storage limits do not affect a successful campaign creation.
            }
            document.querySelector("#editor-link").href = "/editor?campaign_id=" + result.id;
            return "Кампания #" + result.id + " успешно создана.";
        }, createStatus, "Создаём кампанию…");
    } finally {
        createButton.disabled = false;
    }
};

createStatus.textContent = "";
createButton.disabled = false;
