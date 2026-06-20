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

## Installation

[![Open your Home Assistant instance and add this repository to HACS.](https://my.home-assistant.io/badges/hacs_repository.svg?style=flat-square)](https://my.home-assistant.io/redirect/hacs_repository/?owner=myTselection&repository=Wallet-Assistant&category=integration)

1. Open the HACS repository link above, or add `myTselection/Wallet-Assistant` manually as a custom HACS integration repository.
2. Install **Wallet Assistant** from HACS and restart Home Assistant.
3. Add **Wallet Assistant** from **Settings > Devices & services > Add integration**.
4. Add a new Lovelace/Dashboard card by selecting the card in UI ('Community cards') or manually:

```yaml
type: custom:wallet-assistant-card
```

## Storage

Wallet Assistant stores items in `wallet_assistant_items.json` in the Home Assistant config folder.

## Price-Watch Searches

When the dashboard filter contains more than 3 characters, Wallet Assistant shows quick links below the filtered items for external product and price-watch searches.

The default services are Google Shopping, Hagglezon, Tweakers Pricewatch, MaxSpar, Idealo France, Geizhals, and Kieskeurig. To customize them, open **Settings > Devices & services > Wallet Assistant > Configure** and edit one service per line:

```text
Service name|https://example.com/search?q={query}
# Disabled service|https://example.com/search?q={query}
```

`{query}` is replaced with the current filter text.

## Promotion Platforms

Wallet Assistant includes a generic promotion-platform search pipeline. When the dashboard filter contains more than 3 characters, the frontend calls the integration backend and can show normalized external promotions below the matching cards.

Promotion platform options use one platform per line:

```text
platform_id|Platform name|enabled|base address|username|password
benefits_at_work|Benefits at Work|disabled|https://agoria.benefitsatwork.be/login||
edenred_engagement|Edenred Engagement|disabled|||
```

Adapters normalize external data into a shared promotion structure with a title, promotion text, image URL, platform link, optional voucher code, validity dates, and categories. The Benefits at Work and Edenred entries are currently configured as adapter placeholders until their authenticated API or export format is known.

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
