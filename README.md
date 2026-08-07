[![hacs_badge](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://github.com/hacs/integration)
[![GitHub release](https://img.shields.io/github/release/myTselection/Wallet-Assistant.svg)](https://github.com/myTselection/Wallet-Assistant/releases)
![GitHub repo size](https://img.shields.io/github/repo-size/myTselection/Wallet-Assistant.svg)

[![GitHub issues](https://img.shields.io/github/issues/myTselection/Wallet-Assistant.svg)](https://github.com/myTselection/Wallet-Assistant/issues)
[![GitHub last commit](https://img.shields.io/github/last-commit/myTselection/Wallet-Assistant.svg)](https://github.com/myTselection/Wallet-Assistant/commits/main)
[![GitHub commit activity](https://img.shields.io/github/commit-activity/m/myTselection/Wallet-Assistant.svg)](https://github.com/myTselection/Wallet-Assistant/graphs/commit-activity)

<p align="center">
  <img src="custom_components/wallet_assistant/brand/logo.png" alt="Wallet Assistant" width="720">
</p>

## Wallet Assistant - Loyalty cards, vouchers and shop promotions - Home Assistant Custom Integration

A combined Home Assistant custom integration and Lovelace dashboard card that makes it easy to manage loyalty cards, vouchers, and store promotions directly within Home Assistant.

<details><summary>Several similar solutions already exist, and I tried a number of them before starting this project. While they all offer useful functionality, none of them fully matched my requirements.</summary>

* **[VoucherVault](https://github.com/l4rm4nd/VoucherVault)** is an excellent open-source application with extensive features for managing loyalty cards, vouchers, and coupons. It integrates well with Home Assistant through a dedicated custom integration. If you're looking for a comprehensive and secure solution to manage a large collection of cards and vouchers, it's definitely worth considering. For my use case, however, it felt somewhat over-engineered and resource-intensive. Running VoucherVault typically requires multiple components, including the web application itself, Redis, a database (or SQLite), and the Home Assistant integration, which adds complexity if you only need to manage a small personal collection.

* **[Shopping List Manager](https://github.com/thekiwismarthome/shopping-list-manager-card)** is a Home Assistant custom integration with a companion Lovelace card that focuses primarily on shopping lists. It also includes support for storing loyalty cards associated with stores. While the concept is useful, the loyalty card functionality was too limited for my needs.

* **[Card Wallet](https://github.com/rozgonyiadam/hass-cardwallet)** ([forum](https://community.home-assistant.io/t/save-store-cards-loyalty-cards-in-home-assistant-with-a-widget-on-your-phone/487522/16)) is a lightweight and elegant Home Assistant integration for storing loyalty cards. It uses a separate **[Lovelace card](https://github.com/rozgonyiadam/lovelace-cardwallet)** for the user interface and stores data in a simple JSON file. I initially started extending this project because I appreciated its straightforward design and simplicity. However, as I continued adding features, I realized it would be more practical to build a solution tailored to my own vision. I wanted to add voucher support, combine the frontend and backend into a single repository, and introduce additional features for managing promotions and special offers.

</details>

So I started with the Card Wallet code and extended it.

## ⭐ Features

* Single custom HACS integration installation
* UI config flow setup of the integration
* Bundled Lovelace dashboard card served by the integration
* Support loyalty cards, linked to Home Assistant users
  * User-friendly default grid view, can be switched to list view
  * Easy direct filtering while typing to quickly retrieve the needed card
  * Search the current filter text in configurable price-watch services once more than 3 characters are typed
  * Easily add a logo by typing the base URL of the company and retrieve image using [logo.dev](https://logo.dev)
  * Easily add barcode code by scanning with camera
  * Simple storage in a JSON file within the Home Assistant folder
  * Support QR and barcode representation, last used representation is used as default
  * By default see all cards of all users, if desired filter and switch between your own cards and other Home Assistant user cards
  * Add new cards directly from the UI
  * Edit or delete your cards
  * Responsive design
* Support for voucher cards, linked to Home Assistant users
  * Show vouchers inline with loyalty cards, or filter the view by type
  * Easy direct filtering while typing to quickly retrieve the needed item
  * See expiry dates
  * Sensor with number of vouchers that will expire soon
* Support for price-watch links
  * After entering a search query, price-watch links below the loyalty/voucher cards will be shown
  * The links will re-drirect to browser with search query pre-filled to allow easy price lookups
* Support for promotion platform vouchers
  * Edenred platform: enter edenred platform credentials
  * Benefits at work platform: enter platform credentials
  * All promotions of the platform will be downloaded/refreshed once a week and stored in sensor attributes
  * When searching on the wallet assistant dashboard, all possible matches of the promotion platforms will be shown immediately

## Installation

[![Open your Home Assistant instance and add this repository to HACS.](https://my.home-assistant.io/badges/hacs_repository.svg?style=flat-square)](https://my.home-assistant.io/redirect/hacs_repository/?owner=myTselection&repository=Wallet-Assistant&category=integration)

1. Open the HACS repository link above, or add `myTselection/Wallet-Assistant` manually as a custom HACS integration repository.
2. Install **Wallet Assistant** from HACS and restart Home Assistant.
3. Add **Wallet Assistant** from **Settings > Devices & services > Add integration**.
4. Configur price-watch links and the promotion platform credentials using the integration configuration option
5. Add a new Lovelace/Dashboard card by selecting the card in UI ('Community cards') or manually:

```yaml
type: custom:wallet-assistant-card
```

## Storage

Wallet Assistant stores items in `wallet_assistant_items.json` in the Home Assistant config folder.

## Price-Watch Searches

When the dashboard filter contains more than 3 characters, Wallet Assistant shows quick links below the filtered items for external product and price-watch searches.

The default services are Google Shopping, Hagglezon, Tweakers Pricewatch, MaxSpar, Idealo France, Geizhals, and Kieskeurig. To customize them, open **Settings > Devices & services > Wallet Assistant > Configure > Price-watch sites**. From there you can add, edit, disable, or remove a site. Each site has a name, an enabled toggle, and a search URL template.

`{query}` is replaced with the current filter text.

## Promotion Platforms

Wallet Assistant includes a generic promotion-platform search pipeline. When the dashboard filter contains more than 3 characters, the frontend calls the integration backend and can show normalized external promotions below the matching cards.

Promotion platforms are configured from **Settings > Devices & services > Wallet Assistant > Configure > Promotion platforms**. Each platform has a platform ID, name, enabled toggle, login or base address, username, password, and an optional two-factor authentication seed.

The `benefits_at_work` platform logs in to the configured Benefits at Work tenant, accepts the required disclaimer, discovers the main `Alle voordelen in ...` category overview links, and refreshes available offers from those category pages once per day. Promotions from every platform are normalized into the same JSON structure and stored on the `Promotion platform promotions` sensor in the `promotions` attribute. The wallet card searches that cached sensor data when you type in the filter, so UI searches do not log in to external platforms on every keystroke.

Use the tenant login URL as the base address, for example `https://agoria.benefitsatwork.be/login`.

For `edenred_engagement`, use the tenant address such as `https://company.engagement.edenred.be`. If Edenred requires authenticator-app verification, enter its base32 TOTP seed (or an `otpauth://` URI) in the two-factor authentication seed field. Wallet Assistant generates and submits a token only when Edenred redirects the login to its multi-factor validation page.

Adapters normalize external data into a shared promotion structure with a title, promotion text, image URL, platform link, optional voucher code, validity dates, and categories.

## Dashboard Resource

The dashboard card is built into `custom_components/wallet_assistant/frontend/wallet-assistant-card.js`, served by the integration at `/wallet_assistant_static/wallet-assistant-card.js`, and automatically registered as a Lovelace module resource.

## 📷 Screenshots

<img width="514" height="307" alt="image" src="https://github.com/user-attachments/assets/ee60e7b2-f5d3-484c-94c3-c6ae1976055e" />

---

<img width="532" height="156" alt="image" src="https://github.com/user-attachments/assets/744a5f23-d26e-4150-8b84-03eb8d8f840c" />

---
<img width="525" height="327" alt="image" src="https://github.com/user-attachments/assets/ae7cd14b-04a4-485b-adc4-e7448d0cd602" />

---
<img width="527" height="554" alt="image" src="https://github.com/user-attachments/assets/72fac2c8-a556-4746-980c-9b6f1e6ec5d2" />

---
<img width="373" height="523" alt="image" src="https://github.com/user-attachments/assets/8687d0b9-4141-4061-8db2-88dc313466e1" />

---
<img width="666" height="318" alt="image" src="https://github.com/user-attachments/assets/3aaad85b-e9fd-40c2-b35b-8ae6f398ff81" />

---
<img width="590" height="735" alt="image" src="https://github.com/user-attachments/assets/61c74acf-3df2-4997-9d17-6e659432af57" />

---
<img width="602" height="738" alt="image" src="https://github.com/user-attachments/assets/f096c4a5-d155-4ebe-9762-c804ea70f62d" />

---







## 🙏 Credits

- [Card Wallet](https://github.com/rozgonyiadam) - Original code base and main base functionality
- [node-qrcode](https://github.com/soldair/node-qrcode) - Used for QR code generation
- [JsBarcode](https://github.com/lindell/JsBarcode) - Used for barcode rendering
