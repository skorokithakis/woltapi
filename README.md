# woltapi

A Python library for looking up restaurants, reading menus, and viewing your
Wolt orders.

This is an **unofficial** project. It also has code for placing orders, but that
part is unfinished and has not been tested with a real purchase.

## Install

You need Python 3.10 or newer. Install the library from PyPI:

```bash
pip install woltapi
```

You can then use `from woltapi import WoltClient, SessionCredentials` in your
Python code. See [Use it in Python](#use-it-in-python) below.

### Install from source

To run the example scripts or work on the library, clone this repository and
install an editable copy:

```bash
git clone https://github.com/skorokithakis/woltapi.git
cd woltapi
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e .
```

These commands create a separate Python environment and install the library in
it. On Windows, activate it with `.venv\Scripts\activate` instead of `source`.

## Try it

Run these commands from the project folder after installing from source above.
The `examples/` scripts are not installed by `pip install woltapi`.

The browsing example shows your recent orders, searches for a restaurant, and
lists some of its menu items. **It will not order anything or change your basket.**

```bash
python examples/browse.py \
  --latitude 60.17 \
  --longitude 24.94 \
  --query "pizza"
```

Replace the two numbers with your location. The example numbers are in Helsinki.
Latitude and longitude are the two numbers that identify a place on a map.

The script asks for your Wolt access token. Paste it and press Enter. Nothing
will appear while you paste; that is intentional.

By default, it shows up to 5 recent orders and 20 menu items from the first
restaurant in the search results. You can change that:

```bash
python examples/browse.py \
  --latitude 60.17 \
  --longitude 24.94 \
  --query "burger" \
  --orders 3 \
  --menu-limit 50 \
  --venue-index 2
```

`--venue-index 2` chooses the second search result. You can also search for a
restaurant by name. Use `--help` to see all the options.

Menu prices are converted from cents: for example, `1234` is shown as `12.34 EUR`.
These are item prices, not a final order total including delivery and other fees.
The library keeps the original integer amounts; only the display converts them.

## Get a token

An **access token** is a temporary key that lets the library use your Wolt
account. Treat it like a password.

1. Sign in to Wolt in your browser.
2. Open Developer Tools and select the **Network** tab.
3. Open your order history in Wolt.
4. Select a successful request to `consumer-api.wolt.com`.
5. Under **Request Headers**, find `authorization: Bearer ...`.
6. Copy the long token after `Bearer `.

Do not share the token, put it in Git, or include it in screenshots.

The browsing script can also read the token from an environment variable named
`WOLT_ACCESS_TOKEN`. An environment variable is a setting passed to a program
when it starts. If that variable is set, the script uses it instead of asking
you to paste a token. No credential file is needed.

Access tokens expire. With manually supplied headers, get a current access token
if you receive **HTTP 401**, and update `WOLT_ACCESS_TOKEN` if you use it. For
automatic renewal in Python, use a consumer refresh token as described below.
The library cannot sign you in.

## Use it in Python

Here is a complete browsing example. It asks for your token and location, then
searches for pizza and loads the first result's menu:

```python
from getpass import getpass

from woltapi import SessionCredentials, WoltClient

token = getpass("Wolt access token: ").strip()
token = token.removeprefix("Bearer ")
headers = {"Authorization": f"Bearer {token}"}

client = WoltClient(
    SessionCredentials(
        restaurant_headers=headers,
        consumer_headers=headers,
    )
)

latitude = float(input("Latitude: "))
longitude = float(input("Longitude: "))

restaurants = client.search_venues(
    "pizza",
    latitude=latitude,
    longitude=longitude,
)

for restaurant in restaurants:
    print(restaurant.title or restaurant.slug)

if restaurants:
    restaurant = restaurants[0]
    menu = client.get_assortment(restaurant.slug)
    print(f"Found {len(menu.get('items', []))} menu items.")
else:
    print("No restaurants found. Try another search.")
```

Wolt uses different servers for different jobs. `restaurant_headers` supplies
the login token for searches and saved delivery addresses. `consumer_headers`
supplies it for menus and order history. The library sends each set of headers
only to its matching server.

**The library does not find your token for you.** Your code passes it into
`SessionCredentials`. Reading an environment variable or asking for a token is
the job of your script, not the library.

### Automatic token refresh

Only a **consumer refresh token** is needed to start: no access token, password,
client secret, or browser cookies need to accompany the request. In your logged-in
Wolt browser, find `__wrtoken` under **Developer Tools > Application > Cookies**.
Use its value, without surrounding quotes. This is not the access token from an
Authorization header or the refresh token used by the Converse support widget.
Treat it like a password and do not put it in Git or logs.

```python
from getpass import getpass

from woltapi import RefreshTokenCredentials, WoltClient

credentials = RefreshTokenCredentials(
    getpass("Wolt consumer refresh token: ").strip(),
)
client = WoltClient(credentials)
history = client.get_orders_page()
```

The first API call exchanges the refresh token at
`https://authentication.wolt.com/v1/wauth2/access_token`. Subsequent calls reuse
the access token until shortly before its server-reported expiry (30 minutes in
the verified response). The access token is supplied to the restaurant, consumer,
and payment hosts; the refresh token is sent only to the authentication host.
You can also call `credentials.refresh()` to exchange it explicitly.

Wolt may return a replacement refresh token. `credentials.refresh_token` always
holds the latest successfully validated value. For applications that run across
restarts, pass `on_refresh=save_refresh_token`, where your function accepts that
string and saves it to your secret store after each exchange. The library does
not read or write credential files. Without persistence, a rotated token may be
lost when your process exits. Do not share one refresh token across independent
processes that might refresh it concurrently.

The callback runs before the API request proceeds and must not call back into
`credentials.refresh()` or `credentials.headers_for()`. If it raises, the
exception propagates but the new tokens remain in memory. Later calls retry the
callback before any further authentication or API request; they remain blocked
until persistence succeeds. Make your callback safe to repeat with the same token.

Optional `restaurant_headers`, `consumer_headers`, and `payment_headers` still
scope extra headers to their respective hosts. `Authorization` is managed by
`RefreshTokenCredentials`. Its `timeout` controls authentication requests
separately from `WoltClient`'s API timeout (both default to 10 seconds).

Refresh requests are not retried or redirected, and API requests are never
automatically replayed after a 401, including purchases. An expired or revoked
refresh token requires a new browser session credential. Authentication failures
use the existing exceptions, such as `HTTPStatusError` with
`service == "authentication"`. The example scripts still accept access tokens;
automatic renewal is opt-in through this Python API.

### Useful methods

| Call | What you get |
| --- | --- |
| `client.search_venues("pizza", latitude, longitude)` | Restaurant results with names, IDs, and slugs. A slug is the name used in a restaurant's URL. |
| `client.get_assortment(slug)` | A dictionary containing menu items and their options. |
| `client.get_venue_static(slug)` | A dictionary of restaurant details. |
| `client.get_venue_dynamic(slug, latitude, longitude)` | Current opening and delivery information. |
| `client.get_orders_page()` | A dictionary containing the current page of order history. |
| `client.list_delivery_targets()` | References to your saved delivery addresses, without printing the addresses. |
| `client.get_order_status(purchase_id)` | An order's status and some price information. |

For example, after creating `client`:

```python
history = client.get_orders_page()
orders = history.get("orders", [])
print(f"This page contains {len(orders)} orders.")

targets = client.list_delivery_targets()
print(f"You have {len(targets)} saved delivery addresses.")
```

Responses can contain personal information. Avoid printing entire responses or
sending them to shared logs. The browsing example prints only selected fields.

## Can it order food?

Not as a simple, ready-to-use feature yet. There is no `order("pizza")` method.

To try **checkout without buying anything**, use the new checkout-only example
from an editable source installation:

```bash
python examples/order.py --latitude 60.17 --longitude 24.94 --query pizza
```

Use your own coordinates. It prompts for your token (or uses `WOLT_ACCESS_TOKEN`),
guides you through selecting an item and a saved delivery target/card, then asks
before requesting a price. It never submits a purchase. Basket saving is off by
default and needs a separate confirmation if enabled.

This is still a test tool: it may stop if the catalog lacks required checkout
fields. Run `python examples/order.py --help` for available options.

The library has methods to choose items, save a basket, ask Wolt for a price,
and submit a purchase. But some required inputs still need to come from your
own code, including browser/device information and detailed item data.

Order-history and saved-address reads have worked in live checks. **Payment and
purchase handling have not been verified with a real order.** The purchase code
only targets one restaurant, immediate home delivery, and one saved card.

`submit_prepared_order()` is the call that can place an order and charge you.
Do not call it unless you intend to buy the exact order you have reviewed. If it
times out or raises `OrderOutcomeUnknown`, **do not send it again**: Wolt may
already have received it. Check the order in Wolt instead.

Login, adding cards, payment verification screens, scheduled
orders, cancellation, and refunds are not supported.

## Run the tests

With your Python environment active:

```bash
python -m pip install -e ".[test]"
python -m pytest
```

The tests use made-up responses. They do not contact Wolt, need your token, or
place orders.

## Releases

Maintainers: see [RELEASING.md](RELEASING.md) for tests, release PRs, and automatic
PyPI publishing.

## License

Licensed under the GNU Affero General Public License, version 3
(`AGPL-3.0-only`). See [LICENSE](LICENSE) for the full terms.
