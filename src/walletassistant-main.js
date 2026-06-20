import QRCode from 'qrcode';
import JsBarcode from 'jsbarcode';
import { BrowserMultiFormatReader } from '@zxing/browser';
import styleContent from './wallet-assistant-style.css' assert { type: 'text' };

const DIGIT_ONLY_FORMATS = new Set([
  "EAN", "EAN13", "EAN8", "UPC", "ITF", "ITF14",
  "MSI", "MSI10", "MSI11", "MSI1010", "MSI1110", "pharmacode"
]);
const LOGO_DEV_PUBLISHABLE_KEY = "pk_Svpm4b4MRmC5bvOmTw9jdg";
const API_PATH = "wallet_assistant";
const TYPE_LABELS = {
  loyalty: "Card",
  voucher: "Voucher",
  promotion: "Promo"
};
const CARD_TYPE = "wallet-assistant-card";
const DEFAULT_VIEW = "barcode";
const CODE_VIEWS = new Set([DEFAULT_VIEW, "qr"]);
const DEFAULT_CARD_VIEW_MODE = "grid";
const CARD_VIEW_MODES = new Set([DEFAULT_CARD_VIEW_MODE, "list"]);
const CARD_LOAD_RETRY_MS = 30_000;
const DEFAULT_PRICE_WATCH_SERVICES = [
  {
    name: "Google Shopping",
    url_template: "https://www.google.com/search?tbm=shop&q={query}",
    enabled: true
  },
  {
    name: "Hagglezon",
    url_template: "https://www.hagglezon.com/en/s/{query}",
    enabled: true
  },
  {
    name: "Tweakers Pricewatch",
    url_template: "https://tweakers.net/pricewatch/zoeken/?keyword={query}",
    enabled: true
  },
  {
    name: "MaxSpar",
    url_template: "https://nl.maxspar.de/suche/?q={query}",
    enabled: true
  },
  {
    name: "Idealo France",
    url_template: "https://www.idealo.fr/resultats.html?q={query}",
    enabled: true
  },
  {
    name: "Geizhals",
    url_template: "https://geizhals.eu/?fs={query}",
    enabled: true
  },
  {
    name: "Kieskeurig",
    url_template: "https://www.kieskeurig.be/zoeken/index.html?q={query}",
    enabled: true
  }
];

window.customCards = window.customCards || [];
if (!window.customCards.some((card) => card.type === CARD_TYPE)) {
  window.customCards.push({
    type: CARD_TYPE,
    name: "Wallet Assistant",
    description: "Manage loyalty cards, vouchers, and promotions.",
    preview: false,
    documentationURL: "https://github.com/myTselection/Wallet-Assistant",
  });
}

function sanitizeCode(value, fmt) {
  let s = String(value ?? "")
    .normalize("NFD").replace(/[\u0300-\u036f]/g, "")
    .replace(/[^a-zA-Z0-9]/g, "");
  if (DIGIT_ONLY_FORMATS.has(fmt)) s = s.replace(/\D/g, "");
  return s;
}

function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>"']/g, (char) => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    '"': "&quot;",
    "'": "&#39;"
  })[char]);
}

function getCardInitial(name) {
  return String(name || "?").trim().charAt(0).toUpperCase() || "?";
}

function getLogoUrl(slug, size = 64) {
  const cleanSlug = String(slug ?? "").trim();
  if (!cleanSlug) return "";
  return `https://img.logo.dev/${encodeURIComponent(cleanSlug)}?token=${LOGO_DEV_PUBLISHABLE_KEY}&size=${size}&format=webp&retina=true`;
}

function getItemId(item) {
  return item?.item_id;
}

function getItemType(item) {
  return item?.item_type || "loyalty";
}

function getTypeLabel(item) {
  return TYPE_LABELS[getItemType(item)] || "Item";
}

function compareItemsByName(a, b) {
  return String(a?.name || "").localeCompare(String(b?.name || ""), undefined, {
    numeric: true,
    sensitivity: "base"
  });
}

function getExpiryTime(item) {
  if (!item?.expires_on) return Number.POSITIVE_INFINITY;
  const time = new Date(`${item.expires_on}T00:00:00`).getTime();
  return Number.isNaN(time) ? Number.POSITIVE_INFINITY : time;
}

function compareItemsByExpiryThenName(a, b) {
  const expiryDiff = getExpiryTime(a) - getExpiryTime(b);
  return expiryDiff || compareItemsByName(a, b);
}

function normalizeDefaultView(value) {
  return CODE_VIEWS.has(value) ? value : DEFAULT_VIEW;
}

function normalizeCardViewMode(value) {
  return CARD_VIEW_MODES.has(value) ? value : DEFAULT_CARD_VIEW_MODE;
}

function normalizePriceWatchServices(value) {
  const services = Array.isArray(value) ? value : DEFAULT_PRICE_WATCH_SERVICES;
  const normalized = services
    .filter(service => service && service.enabled !== false)
    .map(service => ({
      name: String(service.name || "").trim(),
      url_template: String(service.url_template || service.urlTemplate || service.url || "").trim()
    }))
    .filter(service =>
      service.name &&
      service.url_template.includes("{query}") &&
      /^https?:\/\//i.test(service.url_template)
    );

  return normalized;
}

function getPriceWatchUrl(service, query) {
  return service.url_template.split("{query}").join(encodeURIComponent(query));
}

function formatExpiry(value) {
  if (!value) return "";
  const date = new Date(`${value}T00:00:00`);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleDateString(undefined, { year: "numeric", month: "short", day: "numeric" });
}

function normalizeItem(item) {
  return {
    ...item,
    item_id: getItemId(item),
    item_type: getItemType(item),
    format: item.format || "CODE128",
    default_view: normalizeDefaultView(item.default_view),
    code: item.code || "",
    expires_on: item.expires_on || "",
    notes: item.notes || ""
  };
}

function normalizePromotion(promotion) {
  return {
    promotion_id: String(promotion?.promotion_id || ""),
    platform_id: String(promotion?.platform_id || ""),
    platform_name: String(promotion?.platform_name || ""),
    title: String(promotion?.title || ""),
    promotion: String(promotion?.promotion || ""),
    description: String(promotion?.description || ""),
    image_url: String(promotion?.image_url || ""),
    item_url: String(promotion?.item_url || ""),
    voucher_code: String(promotion?.voucher_code || ""),
    valid_until: String(promotion?.valid_until || ""),
    categories: Array.isArray(promotion?.categories) ? promotion.categories : []
  };
}

function getCameraScanErrorMessage(error = null) {
  const protocol = window.location?.protocol || "";
  const hostname = window.location?.hostname || "";
  const isLocalhost = hostname === "localhost" || hostname === "127.0.0.1" || hostname === "[::1]";

  if (window.isSecureContext === false && protocol === "http:" && !isLocalhost) {
    return "Camera scanning requires a secure connection. Open Home Assistant with HTTPS, use the Home Assistant companion app, or access it from localhost.";
  }

  if (!navigator.mediaDevices?.getUserMedia) {
    return "Camera scanning is not available in this browser or webview. Try HTTPS or the Home Assistant companion app.";
  }

  if (error?.name === "NotAllowedError" || error?.name === "PermissionDeniedError") {
    return "Camera permission was denied. Allow camera access for this site and try again.";
  }

  if (error?.name === "NotFoundError" || error?.name === "DevicesNotFoundError") {
    return "No camera was found on this device.";
  }

  if (error?.name === "NotReadableError" || error?.name === "TrackStartError") {
    return "The camera is already in use by another app or browser tab.";
  }

  return error?.message || "Unable to start the camera scanner.";
}

function renderBarcodeCentral(target, code, fmt, opts = {}, errorTarget = null) {
  const clean = sanitizeCode(code, fmt);
  if (target instanceof HTMLCanvasElement) {
    const ctx = target.getContext("2d");
    if (ctx) ctx.clearRect(0, 0, target.width, target.height);
  } else {
    while (target.firstChild) target.removeChild(target.firstChild);
  }
  if (errorTarget) errorTarget.style.display = "none";

  try {
    JsBarcode(target, clean, {
      format: fmt,
      width: 5,
      height: 80,
      ...opts
    });
    return { ok: true };
  } catch (err) {
    const msg = `Invalid input for ${fmt}.` + (err?.message ? ` (${err.message})` : "");
    if (errorTarget) {
      errorTarget.textContent = msg;
      errorTarget.style.display = "block";
    } else if (target && target.parentElement) {
      const div = document.createElement("div");
      div.className = "error";
      div.style.cssText = "color:red;font-size:0.9em;";
      div.textContent = msg;
      target.replaceWith(div);
    }

    //console.error("Barcode render error:", err);
    return { ok: false, message: msg };
  }
}

class WalletAssistantCard extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });

    this.viewModes = {};
    this.ownCards = [];
    this.otherCards = [];
    this.selectedCard = null;
    this.filtersEnabled = false;
    this.activeOwner = "all";
    this.activeType = "all";
    this._inputState = { name: "", code: "", logo_slug: "", item_type: "loyalty", expires_on: "", notes: "" };

    this.toolbarContainer = document.createElement("div");
    this.toolbarContainer.className = "toolbar";
    this.dynamicContainer = document.createElement("div");

    this.filterText = "";
    this.showAddDialog = false;
    this.cardViewMode = DEFAULT_CARD_VIEW_MODE;
    this.priceWatchServices = normalizePriceWatchServices();
    this.promotionMatches = [];
    this.promotionSearchQuery = "";
    this.promotionsLoading = false;
    this._promotionSearchTimer = null;
    this._promotionSearchPromise = null;
    this._settingsLoaded = false;
    this._settingsLoadPromise = null;
    this._cardsLoaded = false;
    this._cardsLoadPromise = null;
    this._lastCardsLoadAttempt = 0;

    const style = document.createElement("style");
    style.textContent = styleContent;
    this.shadowRoot.appendChild(style);
    this.shadowRoot.appendChild(this.toolbarContainer);
    this.shadowRoot.appendChild(this.dynamicContainer);

    this.toolbarContainer.innerHTML = `
      <div class="action-row">
        <div class="filter-input-wrap">
          <input id="filter" class="filter-input" placeholder="Filter items..." value="" />
          <button id="clear-filter" class="clear-filter-button" type="button" title="Clear filter" aria-label="Clear filter" hidden><ha-icon icon="mdi:close"></ha-icon></button>
        </div>
        <button id="toggle-filters" class="toolbar-icon-button" title="Show filters" aria-label="Show filters"><ha-icon icon="mdi:filter-outline"></ha-icon></button>
        <button id="toggle-card-view" class="toolbar-icon-button" title="List view" aria-label="List view"><ha-icon icon="mdi:view-list"></ha-icon></button>
        <button id="add-card" class="toolbar-icon-button" title="Add item"><ha-icon icon="mdi:plus"></ha-icon></button>
      </div>
      <div class="filter-panel" hidden>
        <div class="filter-group owner-filter" aria-label="Owner filter">
          <button class="filter-chip active" id="owner-all" type="button">All</button>
          <button class="filter-chip" id="owner-own" type="button">Mine</button>
          <button class="filter-chip" id="owner-others" type="button">Others</button>
        </div>
        <div class="filter-group type-filter" aria-label="Type filter">
          <button class="filter-chip active" id="type-all" type="button">All</button>
          <button class="filter-chip" id="type-loyalty" type="button">Cards</button>
          <button class="filter-chip" id="type-voucher" type="button">Vouchers</button>
        </div>
      </div>
    `;

    this.toolbarContainer.querySelector("#filter")
      ?.addEventListener("input", (event) => {
        this.filterText = event.target.value;
        this.render();
        this.schedulePromotionSearch();
      });

    this.toolbarContainer.querySelector("#clear-filter")
      ?.addEventListener("click", () => {
        window.clearTimeout(this._promotionSearchTimer);
        this.filterText = "";
        this.promotionMatches = [];
        this.promotionSearchQuery = "";
        this.promotionsLoading = false;
        this.render();
        this.toolbarContainer.querySelector("#filter")?.focus();
      });

    this.toolbarContainer.querySelector("#add-card")
      ?.addEventListener("click", () => {
        this.showAddDialog = true;
        this.render();
      });

    this.toolbarContainer.querySelector("#toggle-filters")
      ?.addEventListener("click", () => {
        this.filtersEnabled = !this.filtersEnabled;
        this.render();
      });

    this.toolbarContainer.querySelector("#toggle-card-view")
      ?.addEventListener("click", () => {
        this.cardViewMode = this.cardViewMode === "list" ? "grid" : "list";
        this.render();
      });

    ["all", "own", "others"].forEach(owner => {
      this.toolbarContainer.querySelector(`#owner-${owner}`)
        ?.addEventListener("click", () => {
          this.activeOwner = owner;
          this.render();
        });
    });

    ["all", "loyalty", "voucher"].forEach(type => {
      this.toolbarContainer.querySelector(`#type-${type}`)
        ?.addEventListener("click", () => {
          this.activeType = type;
          this.render();
        });
    });
  }

  setConfig(config) {
    this._config = config;
    this.cardViewMode = normalizeCardViewMode(config?.card_view_mode);
  }

  static getStubConfig() {
    return {
      card_view_mode: DEFAULT_CARD_VIEW_MODE
    };
  }

  set hass(hass) {
    const previousUserId = this._hass?.user?.id;
    this._hass = hass;
    if (previousUserId !== hass?.user?.id) {
      this.loadSettings({ force: true });
      this.loadCards({ force: true });
      this.schedulePromotionSearch();
    } else if (!this._cardsLoaded) {
      this.loadSettings();
      this.loadCards();
      this.schedulePromotionSearch();
    } else if (!this._settingsLoaded) {
      this.loadSettings();
      this.schedulePromotionSearch();
    }
  }

  schedulePromotionSearch() {
    const query = (this.filterText || "").trim();
    window.clearTimeout(this._promotionSearchTimer);

    if (query.length <= 3) {
      this.promotionMatches = [];
      this.promotionSearchQuery = "";
      this.promotionsLoading = false;
      return;
    }

    this._promotionSearchTimer = window.setTimeout(() => {
      this.loadPromotionMatches(query);
    }, 300);
  }

  async loadPromotionMatches(query) {
    if (!this._hass) return;
    const cleanQuery = String(query || "").trim();
    if (cleanQuery.length <= 3) return;
    if (this._promotionSearchPromise && this.promotionSearchQuery === cleanQuery) {
      return this._promotionSearchPromise;
    }

    this.promotionSearchQuery = cleanQuery;
    this.promotionsLoading = true;
    this.render();

    this._promotionSearchPromise = (async () => {
      try {
        const result = await this._hass.callApi(
          "get",
          `${API_PATH}/promotions/search?q=${encodeURIComponent(cleanQuery)}`
        );
        if ((this.filterText || "").trim() !== cleanQuery) return;
        this.promotionMatches = (result?.promotions || []).map(normalizePromotion);
      } catch (error) {
        console.warn("Unable to search Wallet Assistant promotion platforms.", error);
        if ((this.filterText || "").trim() === cleanQuery) {
          this.promotionMatches = [];
        }
      } finally {
        if ((this.filterText || "").trim() === cleanQuery) {
          this.promotionsLoading = false;
          this.render();
        }
      }
    })();

    try {
      return await this._promotionSearchPromise;
    } finally {
      this._promotionSearchPromise = null;
    }
  }

  async loadSettings({ force = false } = {}) {
    if (!this._hass) return;
    if (this._settingsLoadPromise) return this._settingsLoadPromise;
    if (!force && this._settingsLoaded) return;

    this._settingsLoadPromise = (async () => {
      try {
        const settings = await this._hass.callApi("get", `${API_PATH}/settings`);
        this.priceWatchServices = normalizePriceWatchServices(settings?.price_watch_services);
      } catch (error) {
        console.warn("Unable to load Wallet Assistant settings; using default price-watch services.", error);
        this.priceWatchServices = normalizePriceWatchServices();
      } finally {
        this._settingsLoaded = true;
        this.render();
      }
    })();

    try {
      return await this._settingsLoadPromise;
    } finally {
      this._settingsLoadPromise = null;
    }
  }

  async loadCards({ force = false } = {}) {
    if (!this._hass) return;
    if (this._cardsLoadPromise) return this._cardsLoadPromise;

    const now = Date.now();
    if (!force && this._cardsLoaded) return;
    if (!force && this._lastCardsLoadAttempt && now - this._lastCardsLoadAttempt < CARD_LOAD_RETRY_MS) return;

    const nameEl = this.shadowRoot.getElementById("add-name") || this.shadowRoot.getElementById("name");
    const codeEl = this.shadowRoot.getElementById("add-code") || this.shadowRoot.getElementById("code");
    const logoSlugEl = this.shadowRoot.getElementById("add-logo-slug") || this.shadowRoot.getElementById("logo-slug");
    const typeEl = this.shadowRoot.getElementById("add-type");
    const expiresEl = this.shadowRoot.getElementById("add-expires-on");
    const notesEl = this.shadowRoot.getElementById("add-notes");
    const activeId = this.shadowRoot.activeElement?.id || document.activeElement?.id;
    if (nameEl || codeEl || logoSlugEl || typeEl || expiresEl || notesEl) {
      this._inputState.name = nameEl?.value || "";
      this._inputState.code = codeEl?.value || "";
      this._inputState.logo_slug = logoSlugEl?.value || "";
      this._inputState.item_type = typeEl?.value || "loyalty";
      this._inputState.expires_on = expiresEl?.value || "";
      this._inputState.notes = notesEl?.value || "";
      this._inputState.focusId = activeId;
    }

    this._lastCardsLoadAttempt = now;
    this._cardsLoadPromise = (async () => {
      const all = await this._hass.callApi("get", API_PATH);
      const uid = this._hass.user.id;

      const items = all.map(normalizeItem);
      this.ownCards = items.filter(c => c.user_id === uid);
      this.otherCards = items.filter(c => c.user_id !== uid);
      this._cardsLoaded = true;
      this.render();
    })();

    try {
      return await this._cardsLoadPromise;
    } finally {
      this._cardsLoadPromise = null;
    }
  }

  async toggleCodeType(cardId) {
    const card = this.selectedCard || [...this.ownCards, ...this.otherCards].find(c => getItemId(c) === cardId);
    const nextView = (this.viewModes[cardId] || card?.default_view || DEFAULT_VIEW) === "barcode" ? "qr" : "barcode";
    this.viewModes[cardId] = nextView;

    if (card) {
      const updatedCard = normalizeItem({ ...card, default_view: nextView });
      this.ownCards = this.ownCards.map(c => getItemId(c) === cardId ? normalizeItem({ ...c, default_view: nextView }) : c);
      this.otherCards = this.otherCards.map(c => getItemId(c) === cardId ? normalizeItem({ ...c, default_view: nextView }) : c);
      if (this.selectedCard && getItemId(this.selectedCard) === cardId) {
        this.selectedCard = updatedCard;
      }
    }

    this.render();

    if (!card || card.user_id !== this._hass.user.id) {
      return;
    }

    try {
      const saved = await this._hass.callApi("put", `${API_PATH}/${cardId}`, {
        default_view: nextView
      });
      const savedCard = normalizeItem({ ...card, ...(saved || {}), default_view: nextView });
      this.ownCards = this.ownCards.map(c => getItemId(c) === cardId ? normalizeItem({ ...c, ...savedCard }) : c);
      if (this.selectedCard && getItemId(this.selectedCard) === cardId) {
        this.selectedCard = normalizeItem({ ...this.selectedCard, ...savedCard });
      }
    } catch (error) {
      console.error("Unable to save Wallet Assistant default view:", error);
    }
  }

  openCard(cardId) {
    this.selectedCard = [...this.ownCards, ...this.otherCards].find(c => getItemId(c) === cardId);
    if (this.selectedCard && !this.viewModes[cardId]) {
      this.viewModes[cardId] = this.selectedCard.default_view;
    }
    this.render();
  }

  closeCard() {
    this.selectedCard = null;
    this.render();
  }

  async addCard(e) {
    e.preventDefault();
    const nameEl = this.shadowRoot.getElementById("add-name") || this.shadowRoot.getElementById("name");
    const codeEl = this.shadowRoot.getElementById("add-code") || this.shadowRoot.getElementById("code");
    const logoSlugEl = this.shadowRoot.getElementById("add-logo-slug") || this.shadowRoot.getElementById("logo-slug");
    const typeEl = this.shadowRoot.getElementById("add-type");
    const expiresEl = this.shadowRoot.getElementById("add-expires-on");
    const notesEl = this.shadowRoot.getElementById("add-notes");
    const name = nameEl?.value?.trim();
    const code = codeEl?.value?.trim();
    const logoSlug = logoSlugEl?.value?.trim() || "";
    const itemType = typeEl?.value || "loyalty";
    const expiresOn = expiresEl?.value || "";
    const notes = notesEl?.value?.trim() || "";
    if (!name || !code) return alert("Missing fields");
    await this._hass.callApi("post", API_PATH, {
      name,
      code,
      logo_slug: logoSlug,
      owner: this._hass.user.name,
      user_id: this._hass.user.id,
      item_type: itemType,
      expires_on: itemType === "voucher" ? expiresOn : "",
      notes,
      format: "CODE128", // default for new cards
      default_view: DEFAULT_VIEW
    });

    this._inputState = { name: "", code: "", logo_slug: "", item_type: "loyalty", expires_on: "", notes: "" };
    if (nameEl) nameEl.value = "";
    if (codeEl) codeEl.value = "";
    if (logoSlugEl) logoSlugEl.value = "";
    if (typeEl) typeEl.value = "loyalty";
    if (expiresEl) expiresEl.value = "";
    if (notesEl) notesEl.value = "";
    this.showAddDialog = false;
    await this.loadCards({ force: true });
  }

  closeAddDialog() {
    this.showAddDialog = false;
    this.render();
  }

  async deleteCard(card) {
    await this._hass.callApi("delete", `${API_PATH}/${getItemId(card)}`, {
      user_id: card.user_id
    });
    this.closeCard();
    await this.loadCards({ force: true });
  }

  async scanBarcodeIntoInput(input, onValue = null) {
    if (!input) return;
    if (!navigator.mediaDevices?.getUserMedia) {
      alert(getCameraScanErrorMessage());
      return;
    }

    const dialog = document.createElement("div");
    dialog.className = "scanner-dialog";
    dialog.innerHTML = `
      <div class="scanner-content">
        <video id="scanner-video" autoplay muted playsinline></video>
        <div id="scanner-status">Point the camera at a barcode</div>
        <div class="btn-row">
          <button id="scanner-cancel" type="button"><ha-icon icon="mdi:close"></ha-icon> Cancel</button>
        </div>
      </div>
    `;
    this.shadowRoot.appendChild(dialog);

    const video = dialog.querySelector("#scanner-video");
    const status = dialog.querySelector("#scanner-status");
    const reader = new BrowserMultiFormatReader();
    let scannerControls = null;
    let closed = false;

    const closeScanner = (controls = scannerControls) => {
      if (closed) return;
      closed = true;
      controls?.stop();
      dialog.remove();
    };

    dialog.querySelector("#scanner-cancel").addEventListener("click", () => closeScanner());

    try {
      scannerControls = await reader.decodeFromVideoDevice(undefined, video, (result, error, controls) => {
        if (closed) return;
        if (result) {
          const value = result.getText();
          input.value = value;
          input.dispatchEvent(new Event("input", { bubbles: true }));
          onValue?.(value);
          closeScanner(controls);
          return;
        }

        if (error && error.name !== "NotFoundException") {
          status.textContent = "Scanning...";
        }
      });
    } catch (error) {
      status.textContent = getCameraScanErrorMessage(error);
      status.classList.add("error");
    }
  }

  async updateCard(card) {
    const supportedFormats = [
      "CODE128", "CODE128A", "CODE128B", "CODE128C",
      "EAN13", "EAN8",
      "UPC",
      "CODE39",
      "ITF14", "ITF",
      "MSI", "MSI10", "MSI11", "MSI1010", "MSI1110",
      "pharmacode", "codabar", "CODE93"
    ];

    const dialog = document.createElement("div");
    dialog.className = "edit-dialog";
    dialog.innerHTML = `
      <div class="dialog-content">
        <h3>Edit item</h3>
        <form class="popup-form">
          <select id="edit-type" aria-label="Type">
            <option value="loyalty" ${getItemType(card) === "loyalty" ? "selected" : ""}>Loyalty card</option>
            <option value="voucher" ${getItemType(card) === "voucher" ? "selected" : ""}>Voucher</option>
            <option value="promotion" ${getItemType(card) === "promotion" ? "selected" : ""}>Promotion</option>
          </select>
          <input id="edit-name" type="text" placeholder="Name" value="${escapeHtml(card.name)}" />
          <div class="code-input-row">
            <input id="edit-code" type="text" placeholder="Code" value="${escapeHtml(card.code)}" />
            <button id="scan-edit-code" class="inline-icon-button" type="button" title="Scan barcode" aria-label="Scan barcode"><ha-icon icon="mdi:barcode-scan"></ha-icon></button>
          </div>
          <input id="edit-logo-slug" type="text" value="${escapeHtml(card.logo_slug || "")}" placeholder="Logo.dev slug (optional)" />
          <input id="edit-expires-on" type="date" value="${escapeHtml(card.expires_on || "")}" title="Expiry date" aria-label="Expiry date" />
          <textarea id="edit-notes" rows="3" placeholder="Note (optional)">${escapeHtml(card.notes || "")}</textarea>
          <select id="edit-format" title="Default barcode format" aria-label="Default barcode format">
            ${supportedFormats
              .map(
                (f) =>
                  `<option value="${f}" ${f === (card.format || "CODE128") ? "selected" : ""}>${f}</option>`
              )
              .join("")}
          </select>

          <div id="preview-wrap" style="margin-top:10px; text-align:center;">
            <canvas id="preview-canvas"></canvas>
            <div id="preview-error" style="color:red; font-size:0.9em; display:none;"></div>
          </div>

          <div class="btn-row">
            <button id="delete-btn" class="danger-button" type="button"><ha-icon icon="mdi:delete"></ha-icon> Delete</button>
            <button id="save-btn" type="button"><ha-icon icon="mdi:content-save"></ha-icon> Save</button>
            <button id="cancel-btn" type="button"><ha-icon icon="mdi:close"></ha-icon> Cancel</button>
          </div>
        </form>
      </div>
    `;

    // inline style (kept same as before)
    dialog.style.cssText = `
      position: fixed; top:0; left:0; right:0; bottom:0;
      padding:16px; box-sizing:border-box;
      background:rgba(0,0,0,0.4);
      display:flex; align-items:center; justify-content:center;
      z-index:1000;
    `;
    dialog.querySelector(".dialog-content").style.cssText = `
      background:#fff; padding:1em; border-radius:8px; width:min(320px, calc(100vw - 32px)); max-width:calc(100vw - 32px); box-sizing:border-box;
      display:flex; flex-direction:column; gap:0.5em;
    `;

    this.shadowRoot.appendChild(dialog);

    const nameInput = dialog.querySelector("#edit-name");
    const codeInput = dialog.querySelector("#edit-code");
    const logoSlugInput = dialog.querySelector("#edit-logo-slug");
    const typeSelect = dialog.querySelector("#edit-type");
    const expiresInput = dialog.querySelector("#edit-expires-on");
    const notesInput = dialog.querySelector("#edit-notes");
    const formatSelect = dialog.querySelector("#edit-format");
    const previewCanvas = dialog.querySelector("#preview-canvas");
    const errorDiv = dialog.querySelector("#preview-error");

    // --- live preview ---
    const renderPreview = () => {
      const fmt = formatSelect.value;
      const codeValue = codeInput?.value?.trim() || card.code;
      renderBarcodeCentral(previewCanvas, codeValue, fmt, { width: 2, height: 60 }, errorDiv);
    };
    formatSelect.addEventListener("change", renderPreview);
    codeInput?.addEventListener("input", renderPreview);
    renderPreview(); // initial
    dialog.querySelector("#scan-edit-code")
      ?.addEventListener("click", () => this.scanBarcodeIntoInput(codeInput, renderPreview));

    // --- buttons ---
    dialog.querySelector("#cancel-btn").addEventListener("click", () => dialog.remove());
    dialog.querySelector("#delete-btn").addEventListener("click", async () => {
      dialog.remove();
      await this.deleteCard(card);
    });

    dialog.querySelector("#save-btn").addEventListener("click", async () => {
      const newName = nameInput.value;
      const newCode = dialog.querySelector("#edit-code").value;
      const newLogoSlug = logoSlugInput.value.trim();
      const newType = typeSelect.value;
      const newExpiresOn = expiresInput.value;
      const newNotes = notesInput.value.trim();
      const newFormat = formatSelect.value;

      const updated = await this._hass.callApi("put", `${API_PATH}/${getItemId(card)}`, {
        user_id: card.user_id,
        name: newName,
        code: newCode,
        logo_slug: newLogoSlug,
        item_type: newType,
        expires_on: newType === "voucher" ? newExpiresOn : "",
        notes: newNotes,
        format: newFormat
      });
      const updatedCard = {
        name: newName,
        code: newCode,
        logo_slug: newLogoSlug,
        item_type: newType,
        expires_on: newType === "voucher" ? newExpiresOn : "",
        notes: newNotes,
        format: newFormat,
        ...(updated || {})
      };

      // locally update caches
      this.ownCards = this.ownCards.map((c) =>
        getItemId(c) === getItemId(card) ? normalizeItem({ ...c, ...updatedCard }) : c
      );
      this.otherCards = this.otherCards.map((c) =>
        getItemId(c) === getItemId(card) ? normalizeItem({ ...c, ...updatedCard }) : c
      );

      // if card is open, refresh the live canvas + title
      if (this.selectedCard && getItemId(this.selectedCard) === getItemId(card)) {
        this.selectedCard = normalizeItem({ ...this.selectedCard, ...updatedCard });

        const container = this.dynamicContainer.querySelector("#code-preview");
        if (container) {
          const canvas = document.createElement("canvas");
          container.innerHTML = "";
          container.appendChild(canvas);

          const fmt = this.selectedCard.format || "CODE128";
          renderBarcodeCentral(canvas, this.selectedCard.code, fmt);
        }

        const titleEl = this.dynamicContainer.querySelector(".card-title");
        if (titleEl) titleEl.textContent = this.selectedCard.name;
      }

      dialog.remove();
      this.render();
    });
  }

  render() {
    const cardListEl = this.dynamicContainer.querySelector(".cardlist");
    const scrollPosition = cardListEl ? cardListEl.scrollTop : 0;
    const previousFocusedElement = this.shadowRoot.activeElement;
    const previousFocusedId = previousFocusedElement?.id;
    const previousSelectionStart = previousFocusedElement?.selectionStart;
    const previousSelectionEnd = previousFocusedElement?.selectionEnd;

    const allCards = [...(this.ownCards || []), ...(this.otherCards || [])];
    const ownerCards = this.filtersEnabled && this.activeOwner !== "all"
      ? allCards.filter(card =>
          this.activeOwner === "own"
            ? card.user_id === this._hass.user.id
            : card.user_id !== this._hass.user.id
        )
      : allCards;
    const filter = (this.filterText || "").trim().toLowerCase();
    const typedCards = !this.filtersEnabled || this.activeType === "all"
      ? ownerCards
      : ownerCards.filter(card => getItemType(card) === this.activeType);
    const filteredCards = filter
      ? typedCards.filter(card =>
          card.name.toLowerCase().includes(filter) ||
          String(card.owner || "").toLowerCase().includes(filter) ||
          String(card.notes || "").toLowerCase().includes(filter) ||
          getTypeLabel(card).toLowerCase().includes(filter)
        )
      : typedCards;
    const sortedCards = [...filteredCards].sort(
      this.filtersEnabled && this.activeType === "voucher" ? compareItemsByExpiryThenName : compareItemsByName
    );

    const filterInput = this.toolbarContainer.querySelector("#filter");
    const clearFilterButton = this.toolbarContainer.querySelector("#clear-filter");
    const toggleFiltersButton = this.toolbarContainer.querySelector("#toggle-filters");
    const toggleViewButton = this.toolbarContainer.querySelector("#toggle-card-view");
    const filterPanel = this.toolbarContainer.querySelector(".filter-panel");
    if (filterInput) filterInput.value = this.filterText;
    if (clearFilterButton) clearFilterButton.hidden = !this.filterText;
    if (toggleFiltersButton) {
      toggleFiltersButton.classList.toggle("active", this.filtersEnabled);
      toggleFiltersButton.title = this.filtersEnabled ? "Hide filters" : "Show filters";
      toggleFiltersButton.setAttribute("aria-label", toggleFiltersButton.title);
      toggleFiltersButton.setAttribute("aria-pressed", String(this.filtersEnabled));
      toggleFiltersButton.innerHTML = `<ha-icon icon="${this.filtersEnabled ? "mdi:filter" : "mdi:filter-outline"}"></ha-icon>`;
    }
    if (toggleViewButton) {
      const isGridView = this.cardViewMode === "grid";
      toggleViewButton.title = isGridView ? "List view" : "Grid view";
      toggleViewButton.setAttribute("aria-label", toggleViewButton.title);
      toggleViewButton.innerHTML = `<ha-icon icon="${isGridView ? "mdi:view-list" : "mdi:view-grid"}"></ha-icon>`;
    }
    if (filterPanel) filterPanel.hidden = !this.filtersEnabled;
    ["all", "own", "others"].forEach(owner => {
      this.toolbarContainer.querySelector(`#owner-${owner}`)
        ?.classList.toggle("active", this.activeOwner === owner);
    });
    ["all", "loyalty", "voucher"].forEach(type => {
      this.toolbarContainer.querySelector(`#type-${type}`)
        ?.classList.toggle("active", this.activeType === type);
    });

    const selectedLogoUrl = this.selectedCard ? getLogoUrl(this.selectedCard.logo_slug, 96) : "";
    const selectedInitial = this.selectedCard ? getCardInitial(this.selectedCard.name) : "";
    const selectedExpiry = this.selectedCard?.expires_on ? formatExpiry(this.selectedCard.expires_on) : "";
    const selectedNotes = this.selectedCard?.notes || "";
    const priceWatchQuery = (this.filterText || "").trim();
    const promotionMatches = priceWatchQuery.length > 3
      && this.promotionSearchQuery === priceWatchQuery
      ? this.promotionMatches
      : [];
    const showPromotions = priceWatchQuery.length > 3
      && (this.promotionsLoading || promotionMatches.length > 0);
    const priceWatchServices = priceWatchQuery.length > 3
      ? normalizePriceWatchServices(this.priceWatchServices)
      : [];

    this.dynamicContainer.innerHTML = `
      <div class="cardlist ${this.cardViewMode === "grid" ? "grid-view" : "list-view"}">
        ${sortedCards.length ? sortedCards.map(card => {
          const logoUrl = getLogoUrl(card.logo_slug, 64);
          const initial = getCardInitial(card.name);
          const typeLabel = getTypeLabel(card);
          const expiry = card.expires_on ? formatExpiry(card.expires_on) : "";
          return `
          <div class="card ${getItemType(card)}" data-card-id="${escapeHtml(getItemId(card))}" title="${escapeHtml(card.name)}">
            <div class="card-logo-wrap">
              ${logoUrl
                ? `<img class="card-logo" src="${escapeHtml(logoUrl)}" alt="" data-initial="${escapeHtml(initial)}" loading="lazy" />`
                : `<div class="card-logo-placeholder">${escapeHtml(initial)}</div>`}
            </div>
            <div class="card-details">
              <strong>${escapeHtml(card.name)}</strong>
              <small class="item-meta">
                <span>${escapeHtml(typeLabel)}</span>
                ${expiry ? `<span class="expiry"><ha-icon icon="mdi:calendar-clock"></ha-icon>${escapeHtml(expiry)}</span>` : ""}
                ${card.user_id !== this._hass.user.id ? `<span><ha-icon icon="mdi:account"></ha-icon>${escapeHtml(card.owner)}</span>` : ""}
              </small>
            </div>
          </div>
        `;
        }).join("") : `<div class="no-results">No matching items</div>`}
      </div>
      ${showPromotions ? `
        <section class="promotion-results-section" aria-label="Promotion platform results">
          <h3>Promotions "${escapeHtml(priceWatchQuery)}"</h3>
          ${this.promotionsLoading ? `<div class="promotion-results-status">Searching promotion platforms...</div>` : ""}
          ${promotionMatches.length ? `
            <div class="promotion-results-list">
              ${promotionMatches.map(promotion => {
                const promotionUrl = promotion.item_url || "#";
                const hasLink = /^https?:\/\//i.test(promotionUrl);
                const imageUrl = promotion.image_url;
                return `
                  <a
                    class="promotion-result"
                    href="${hasLink ? escapeHtml(promotionUrl) : "#"}"
                    target="${hasLink ? "_blank" : ""}"
                    rel="${hasLink ? "noopener noreferrer" : ""}"
                    aria-label="${escapeHtml(promotion.title)}"
                  >
                    <div class="promotion-image-wrap">
                      ${imageUrl
                        ? `<img class="promotion-image" src="${escapeHtml(imageUrl)}" alt="" loading="lazy" />`
                        : `<div class="promotion-image-placeholder"><ha-icon icon="mdi:ticket-percent"></ha-icon></div>`}
                    </div>
                    <div class="promotion-details">
                      <strong>${escapeHtml(promotion.title)}</strong>
                      <span>${escapeHtml(promotion.promotion || promotion.description)}</span>
                      <small>
                        ${escapeHtml(promotion.platform_name)}
                        ${promotion.valid_until ? ` · Valid until ${escapeHtml(formatExpiry(promotion.valid_until))}` : ""}
                      </small>
                    </div>
                    ${hasLink ? `<ha-icon class="promotion-open-icon" icon="mdi:open-in-new"></ha-icon>` : ""}
                  </a>
                `;
              }).join("")}
            </div>
          ` : ""}
        </section>
      ` : ""}
      ${priceWatchServices.length ? `
        <section class="price-watch-section" aria-label="Price-watch searches">
          <h3>Price watch "${escapeHtml(priceWatchQuery)}"</h3>
          <div class="price-watch-actions">
            ${priceWatchServices.map(service => `
              <a
                class="price-watch-link"
                href="${escapeHtml(getPriceWatchUrl(service, priceWatchQuery))}"
                target="_blank"
                rel="noopener noreferrer"
                title="Search ${escapeHtml(priceWatchQuery)} on ${escapeHtml(service.name)}"
              >
                <ha-icon icon="mdi:open-in-new"></ha-icon>
                <span>${escapeHtml(service.name)}</span>
              </a>
            `).join("")}
          </div>
        </section>
      ` : ""}
      ${this.selectedCard ? `
        <div class="popup-overlay" id="details-overlay">
          <div class="popup">
            <div class="popup-header">
              <div class="popup-logo-wrap">
                ${selectedLogoUrl
                  ? `<img class="card-logo popup-logo" src="${escapeHtml(selectedLogoUrl)}" alt="" data-initial="${escapeHtml(selectedInitial)}" loading="lazy" />`
                  : `<div class="card-logo-placeholder popup-logo-placeholder">${escapeHtml(selectedInitial)}</div>`}
              </div>
              <h3 class="card-title">${escapeHtml(this.selectedCard.name)}</h3>
            </div>
            <div class="popup-meta">
              <span>${escapeHtml(getTypeLabel(this.selectedCard))}</span>
              ${selectedExpiry ? `<span><ha-icon icon="mdi:calendar-clock"></ha-icon>${escapeHtml(selectedExpiry)}</span>` : ""}
            </div>
            ${selectedNotes ? `<div class="popup-note">${escapeHtml(selectedNotes)}</div>` : ""}
            <div class="code" id="code-preview"></div>
            <div class="button-group">
              <button id="toggle-code"><ha-icon icon="mdi:cached"></ha-icon> QR/Barcode</button>
              ${this.selectedCard.user_id === this._hass.user.id ? `
                <button id="edit"><ha-icon icon="mdi:pencil"></ha-icon> Edit</button>
              ` : ""}
            </div>
            <ha-icon icon="mdi:close" class="close-btn" id="close" title="Close"></ha-icon>
          </div>
        </div>
      ` : ""}
      ${this.showAddDialog ? `
        <div class="dialog-overlay" id="add-dialog">
          <div class="dialog-content">
            <h3>Add new item</h3>
            <form id="add-card-form" class="popup-form">
              <select id="add-type">
                <option value="loyalty" ${(this._inputState.item_type || "loyalty") === "loyalty" ? "selected" : ""}>Loyalty card</option>
                <option value="voucher" ${this._inputState.item_type === "voucher" ? "selected" : ""}>Voucher</option>
                <option value="promotion" ${this._inputState.item_type === "promotion" ? "selected" : ""}>Promotion</option>
              </select>
              <input id="add-name" placeholder="Name" value="${escapeHtml(this._inputState.name || "")}" />
              <div class="code-input-row">
                <input id="add-code" placeholder="Code" value="${escapeHtml(this._inputState.code || "")}" />
                <button type="button" id="scan-add-code" class="inline-icon-button" title="Scan barcode" aria-label="Scan barcode"><ha-icon icon="mdi:barcode-scan"></ha-icon></button>
              </div>
              <input id="add-logo-slug" placeholder="Logo.dev slug (optional)" value="${escapeHtml(this._inputState.logo_slug || "")}" />
              <input id="add-expires-on" type="date" value="${escapeHtml(this._inputState.expires_on || "")}" />
              <textarea id="add-notes" rows="3" placeholder="Note (optional)">${escapeHtml(this._inputState.notes || "")}</textarea>
              <div class="btn-row">
                <button type="submit" id="save-btn"><ha-icon icon="mdi:content-save"></ha-icon> Save</button>
                <button type="button" id="cancel-add-btn"><ha-icon icon="mdi:close"></ha-icon> Cancel</button>
              </div>
            </form>
          </div>
        </div>
      ` : ""}
    `;

    this.dynamicContainer.querySelectorAll("[data-card-id]").forEach(el =>
      el.addEventListener("click", () => this.openCard(el.getAttribute("data-card-id")))
    );

    this.dynamicContainer.querySelectorAll(".card-logo").forEach(img =>
      img.addEventListener("error", () => {
        const placeholder = document.createElement("div");
        placeholder.className = img.classList.contains("popup-logo")
          ? "card-logo-placeholder popup-logo-placeholder"
          : "card-logo-placeholder";
        placeholder.textContent = img.dataset.initial || "?";
        img.replaceWith(placeholder);
      })
    );

    this.dynamicContainer.querySelectorAll(".promotion-image").forEach(img =>
      img.addEventListener("error", () => {
        const placeholder = document.createElement("div");
        placeholder.className = "promotion-image-placeholder";
        placeholder.innerHTML = `<ha-icon icon="mdi:ticket-percent"></ha-icon>`;
        img.replaceWith(placeholder);
      })
    );

    if (this.showAddDialog) {
      const addForm = this.dynamicContainer.querySelector("#add-card-form");
      addForm?.addEventListener("submit", this.addCard.bind(this));
      this.dynamicContainer.querySelector("#cancel-add-btn")
        ?.addEventListener("click", () => this.closeAddDialog());

      const addName = this.shadowRoot.getElementById("add-name");
      const addCode = this.shadowRoot.getElementById("add-code");
      const addLogoSlug = this.shadowRoot.getElementById("add-logo-slug");
      const addType = this.shadowRoot.getElementById("add-type");
      const addExpiresOn = this.shadowRoot.getElementById("add-expires-on");
      const addNotes = this.shadowRoot.getElementById("add-notes");
      addName?.addEventListener("input", (event) => { this._inputState.name = event.target.value; });
      addCode?.addEventListener("input", (event) => { this._inputState.code = event.target.value; });
      this.dynamicContainer.querySelector("#scan-add-code")
        ?.addEventListener("click", () => this.scanBarcodeIntoInput(addCode));
      addLogoSlug?.addEventListener("input", (event) => { this._inputState.logo_slug = event.target.value; });
      addType?.addEventListener("change", (event) => { this._inputState.item_type = event.target.value; });
      addExpiresOn?.addEventListener("input", (event) => { this._inputState.expires_on = event.target.value; });
      addNotes?.addEventListener("input", (event) => { this._inputState.notes = event.target.value; });
    }

    if (this.selectedCard) {
      const selectedId = getItemId(this.selectedCard);
      const mode = normalizeDefaultView(this.viewModes[selectedId] || this.selectedCard.default_view);
      const container = this.dynamicContainer.querySelector("#code-preview");
      container.innerHTML = "";

      if (mode === "qr") {
        const canvas = document.createElement("canvas");
        container.appendChild(canvas);
        QRCode.toCanvas(
          canvas,
          this.selectedCard.code,
          { width: 160, margin: 1 },
          (error) => { if (error) console.error("QR generation error:", error); },
        );
      } else {
        const canvas = document.createElement("canvas");
        container.appendChild(canvas);
        const fmt = this.selectedCard.format || "CODE128";
        // központi render (nincs duplikált try/catch)
        renderBarcodeCentral(canvas, this.selectedCard.code, fmt);
      }

      this.dynamicContainer.querySelector("#toggle-code")
        ?.addEventListener("click", () => this.toggleCodeType(selectedId));
      this.dynamicContainer.querySelector("#close")
        ?.addEventListener("click", () => this.closeCard());
      this.dynamicContainer.querySelector("#details-overlay")
        ?.addEventListener("click", (event) => {
          if (event.target === event.currentTarget) this.closeCard();
        });

      if (this.selectedCard.user_id === this._hass.user.id) {
        this.dynamicContainer.querySelector("#edit")
          ?.addEventListener("click", () => this.updateCard(this.selectedCard));
      }
    }

    const newCardListEl = this.dynamicContainer.querySelector(".cardlist");
    if (newCardListEl) newCardListEl.scrollTop = scrollPosition;

    if (previousFocusedId === "filter" && filterInput) {
      filterInput.focus();
      if (typeof previousSelectionStart === "number" && typeof previousSelectionEnd === "number") {
        filterInput.setSelectionRange(previousSelectionStart, previousSelectionEnd);
      }
    }
  }
}

if (!customElements.get(CARD_TYPE)) {
  customElements.define(CARD_TYPE, WalletAssistantCard);
}
