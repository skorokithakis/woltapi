# woltapi

A Python library for looking up restaurants, reading menus, and viewing your
Wolt orders.

This is an **unofficial** project. It also has code for placing orders, but that
part is unfinished and has not been tested with a real purchase.

## Install

You need Python 3.10 or newer. Open a terminal in this project's folder and run:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e .
```

These commands create a separate Python environment and install the library in
it. On Windows, activate it with `.venv\Scripts\activate` instead of `source`.

## Try it

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

Tokens expire. This library cannot refresh them or sign you in. If you get
**HTTP 401**, get a current token from a successful browser request and try again.
If you set `WOLT_ACCESS_TOKEN`, remember to update it too.

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

Login, token refresh, adding cards, payment verification screens, scheduled
orders, cancellation, and refunds are not supported.

For the technical details behind the ordering code, see [WOLT_API.md](WOLT_API.md).
You do not need to read that file to use the browsing example.

## Run the tests

With your Python environment active:

```bash
python -m pip install -e ".[test]"
python -m pytest
```

The tests use made-up responses. They do not contact Wolt, need your token, or
place orders.

## License

Licensed under the GNU Affero General Public License, version 3
(`AGPL-3.0-only`). See [LICENSE](LICENSE) for the full terms.
