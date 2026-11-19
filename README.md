# Shelf-Stock Monitor

Load cells under shop shelves count the items, and the online stock in Shopify follows the shelf automatically.

Team: Gheorghe Alin (hardware, ESP32), Voicea David (cloud, Shopify, security), Popovici Andra (owner page, alerts, testing).

```
 ESP32 + HX711 + load cell  --MQTT over TLS-->  Mosquitto  -->  FastAPI service  --Admin GraphQL-->  Shopify
      (firmware/)                               (broker/)        + PostgreSQL        inventorySetQuantities
                                                                 (cloud/)
```

## Sprint 1 increment (weeks 5–8, milestone D-03)
One shelf position works end to end: **scale → cloud → Shopify**.

- The ESP32 counts items by weight and ignores a pick-up-and-put-back shorter than 6 s.
- Changes go to the cloud over MQTT with TLS, and each device logs in with its own user.
- The cloud stores every change once. Duplicates are dropped by sequence number.
- The cloud sets the product's *available* stock in Shopify.
- On the owner page you can see live counts, link a position to a Shopify product, set its threshold and calibrate (tare + 10 items).

Planned for Sprint 2: low-stock and offline emails, an offline queue on the ESP32, owner login, history page, and deployment on Azure.

## Repository layout
| Folder | Contents |
|---|---|
| `firmware/shelf_node/` | ESP32 sketch (`shelf_node.ino`), counting logic (`counting.h`), `config.example.h` |
| `firmware/test/` | PC unit tests for the counting logic |
| `cloud/` | FastAPI service, database models, Shopify client, owner pages, tests |
| `broker/config/` | Mosquitto configuration and access rules (ACL) |
| `scripts/setup_broker.py` | Creates TLS certificates and MQTT passwords |
| `tools/simulator.py` | Fake shelf device for testing without hardware |
| `docs/test-plan.md` | Manual test procedures (AC-01, AC-02, AC-06, AC-08) |

## Run it locally
Requirements: Docker Desktop, Python 3.11+.

```bash
pip install cryptography paho-mqtt
python scripts/setup_broker.py <your-laptop-IP> shelf-01 shelf-sim   # prints passwords
cp .env.example .env            # paste MQTT_PASSWORD and the Shopify values
docker compose up --build       # owner page: http://localhost:8000
```

To try it without hardware, run this in a second terminal and type `1 7` (sets position 1 to 7 items):

```bash
python tools/simulator.py --host localhost --device shelf-sim --password <shelf-sim password>
```

With `SHOPIFY_DRY_RUN=true` in `.env`, the service only logs the Shopify update. Use that until the store is set up.

## Shopify setup (once)
1. At https://dev.shopify.com create a **development store**, and in it products with **Track quantity** on.
2. In the **Dev Dashboard**, create an app. Create a version with the scopes `read_products, read_inventory, write_inventory, read_locations` and install it on the store.
3. Copy the app's **Client ID** and **Client secret** into `.env`, together with `SHOPIFY_SHOP=<store>.myshopify.com`.

The service gets a 24-hour access token with the client credentials grant and renews it automatically.

## Flash the ESP32
1. Arduino IDE → Boards Manager → install **esp32 by Espressif Systems**. Library Manager → install **HX711** (Bogdan Necula), **PubSubClient** (Nick O'Leary) and **ArduinoJson**.
2. Copy `firmware/shelf_node/config.example.h` to `config.h`. Fill in Wi-Fi, broker IP, device password and the contents of `broker/certs/ca.crt`.
3. Wiring for position 1: HX711 DT → GPIO16, SCK → GPIO17, VCC → 3V3, GND → GND. Board: *ESP32 Dev Module*. Upload, then open Serial Monitor at 115200 baud.
4. Calibrate from the owner page. Empty the plate and press **Tare**. Then put 10 items on it and press **Calibrate**.

## Tests
```bash
cd cloud && pip install -r requirements-dev.txt && pytest -q
g++ -std=c++17 -Ifirmware/shelf_node firmware/test/test_counting.cpp -o t && ./t
```
GitHub Actions runs both on every push and also compiles the ESP32 firmware.

## Working agreement
See [CONTRIBUTING.md](CONTRIBUTING.md) for branches, pull requests and the Definition of Done.
