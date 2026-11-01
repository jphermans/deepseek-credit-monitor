import { createStore } from "/js/AlpineStore.js";
import { callJsonApi } from "/js/api.js";
import {
  toastFrontendError,
  toastFrontendWarning,
} from "/components/notifications/notification-store.js";

const API_PATH = "/plugins/deepseek_credit_monitor/status";

const CURRENCIES = {
  USD: new Intl.NumberFormat("en-US", { style: "currency", currency: "USD" }),
  CNY: new Intl.NumberFormat("zh-CN", { style: "currency", currency: "CNY" }),
};

function formatMoney(value, currency) {
  if (typeof value !== "number") return null;
  const fmt = CURRENCIES[currency];
  if (fmt) return fmt.format(value);
  const symbol = currency ? currency + " " : "";
  return symbol + value.toFixed(2);
}

let pollTimer = null;

export const store = createStore("deepseekCreditStore", {
  // loading|normal|warning|critical|unavailable|not_configured|unknown
  status: "loading",
  balances: [], // [{currency,total_balance,granted_balance,topped_up_balance}]
  displayCurrency: "USD",
  primaryBalance: null,
  primaryCurrency: null,
  available: false,
  showBalance: true,
  staleAgeSeconds: null,
  lastUpdatedLabel: "",
  refreshIntervalSeconds: 60,
  open: false,
  busy: false,
  lastNotifiedStatus: "",

  init() {},

  onOpen() {
    this.schedulePoll(1500);
    document.addEventListener("modal-closed", this._onModalClosed);
  },

  cleanup() {
    if (pollTimer) {
      clearTimeout(pollTimer);
      pollTimer = null;
    }
    document.removeEventListener("modal-closed", this._onModalClosed);
  },

  _onModalClosed: null, // assigned below to keep stable reference

  schedulePoll(delayMs = 0) {
    if (pollTimer) clearTimeout(pollTimer);
    pollTimer = setTimeout(async () => {
      await this.refresh();
      if (this.refreshIntervalSeconds >= 30) {
        this.schedulePoll(this.refreshIntervalSeconds * 1000);
      } else {
        this.schedulePoll(60000);
      }
    }, delayMs);
  },

  async refresh({ force = false, notify = false } = {}) {
    if (this.busy) return; // prevent overlapping requests
    this.busy = true;
    try {
      const data = await callJsonApi(API_PATH, { force });
      if (data && typeof data === "object") {
        this.applyStatus(data);
        if (notify) this.notifyStatusChange(data, true);
      }
    } catch (e) {
      // Backend unreachable: keep last known state, show unavailable.
      this.status = "unavailable";
      if (notify) {
        toastFrontendError("Could not refresh DeepSeek balance.", "DeepSeek Credit Monitor");
      }
    } finally {
      this.busy = false;
    }
  },

  applyStatus(data) {
    const previous = this.status;
    this.status = String(data.status || "unavailable");
    this.balances = Array.isArray(data.balance_infos) ? data.balance_infos : [];
    this.displayCurrency =
      typeof data.display_currency === "string" && data.display_currency
        ? data.display_currency
        : "USD";
    this.primaryBalance = typeof data.primary_balance === "number" ? data.primary_balance : null;
    this.primaryCurrency =
      typeof data.primary_currency === "string" && data.primary_currency
        ? data.primary_currency
        : null;
    this.available = data.available === true;
    this.showBalance = data.show_balance_in_ui !== false;
    this.staleAgeSeconds =
      typeof data.age_seconds === "number" ? data.age_seconds : null;
    this.refreshIntervalSeconds =
      typeof data.refresh_interval_seconds === "number" && data.refresh_interval_seconds >= 30
        ? data.refresh_interval_seconds
        : 60;
    this.lastUpdatedLabel = this._ageLabel(this.staleAgeSeconds);

    // Threshold transition notifications (avoid spam on first load).
    if (previous !== this.status && this.status !== "loading") {
      this.notifyStatusChange(data, false);
    }
  },

  notifyStatusChange(data, manual) {
    const status = String(data.status || "");
    if (status === this.lastNotifiedStatus && !manual) return;
    this.lastNotifiedStatus = status;
    if (manual) {
      if (status === "normal" || status === "unknown") {
        // No toast for healthy or non-threshold refreshes; popover shows values.
      } else if (status === "not_configured") {
        toastFrontendWarning(
          "Add a DeepSeek API key in the plugin settings.",
          "DeepSeek Credit Monitor"
        );
      } else if (status === "unavailable") {
        toastFrontendError(
          "DeepSeek balance is not reachable. Showing last known balance if available.",
          "DeepSeek Credit Monitor"
        );
      }
      return;
    }
    const currency = this.primaryCurrency || this.displayCurrency;
    if (status === "warning") {
      toastFrontendWarning(
        `DeepSeek balance is low (${this._format(currency, this.primaryBalance)}).`,
        "DeepSeek Credit Monitor"
      );
    } else if (status === "critical") {
      toastFrontendError(
        `DeepSeek balance is critical (${this._format(currency, this.primaryBalance)}).`,
        "DeepSeek Credit Monitor"
      );
    }
  },

  _format(currency, value) {
    return formatMoney(value, currency) || "n/a";
  },

  _ageLabel(seconds) {
    if (typeof seconds !== "number" || seconds < 0) return "";
    if (seconds < 60) return "just now";
    const minutes = Math.floor(seconds / 60);
    if (minutes < 60) return `${minutes} min ago`;
    const hours = Math.floor(minutes / 60);
    return `${hours} h ago`;
  },

  formatEntry(entry, field) {
    if (!entry) return "n/a";
    return this._format(entry.currency, entry[field]) || "n/a";
  },

  get balanceLabel() {
    if (this.status === "loading") return "…";
    if (this.status === "not_configured") return "Setup required";
    if (this.status === "unavailable" && this.primaryBalance === null) return "Unavailable";
    if (this.primaryBalance === null) return "Unknown";
    return this._format(this.primaryCurrency || this.displayCurrency, this.primaryBalance);
  },

  get statusLabel() {
    if (this.status === "warning") return "Low";
    if (this.status === "critical") return "Critical";
    if (this.status === "unavailable") return "Unavailable";
    if (this.status === "unknown") return "Unknown";
    return "";
  },

  get statusClass() {
    return `dcm-${this.status}`;
  },

  get indicatorVisible() {
    return this.status !== "loading" && this.showBalance;
  },

  get apiAvailabilityLabel() {
    if (this.status === "not_configured") return "Setup required";
    if (!this.available) return "Unavailable";
    return "Available";
  },

  toggle() {
    this.open = !this.open;
  },

  async manualRefresh() {
    await this.refresh({ force: true, notify: true });
  },

  async openSettings() {
    try {
      const { store: pluginSettingsStore } = await import(
        "/components/plugins/plugin-settings-store.js"
      );
      await pluginSettingsStore.openConfig("deepseek_credit_monitor", "", "", { tab: "config" });
      this.open = false; // close popover
    } catch (e) {
      console.error("openSettings error:", e);
      toastFrontendError("Could not open plugin settings.", "DeepSeek Credit Monitor");
    }
  },
});

store._onModalClosed = (event) => {
  const path = event?.detail?.modalPath || "";
  if (path.includes("plugin-settings")) {
    store.refresh({ force: true });
  }
};
