# Captured Wolt ordering API reference

> **Evidence source:** offline inspection of `wolt.com.har` only. Every `HAR n` reference is a **zero-based** entry index. This is a practical record of one browser session, not an official API specification or a replay recipe.
>
> **Safety boundary:** no live requests, token exchange, order replay, payment action, or HAR modification was performed while producing this reference. A successful `200` in this capture proves only that captured request/response pair—not a generally supported contract.

## Reading this document safely

- **Observed HTTP** means the method, route, status, field, or relationship occurred in the HAR.
- **Bundle-derived** means static JavaScript in the HAR contains the stated clue. It is not a runtime-authentication or server-contract claim.
- **Unknown** means the capture does not establish it.
- All angle-bracket values are deliberate redactions: `<venue_id>`, `<payment_method_id>`, `<latitude>`, and so on. They are not values from the capture. The public static signature constant `N/A` is documented explicitly in §5.4; it is not a customer-specific signature.
- Integer examples such as `11111` are synthetic illustrative values, not captured prices or totals. Placeholder strings may occupy fields that the HAR carried as another scalar type; the prose calls out observed types where useful.
- All JSON code blocks are valid JSON. They show captured **shapes**, not a complete schema, required-field list, or complete enum set.
- Do not log or expose delivery addresses, coordinates, phone numbers, customer IDs, saved-card metadata, device identifiers, browser metadata, tokens, dynamic signatures, nonces, checksums, or opaque identifiers. They occur in otherwise useful responses. `N/A` is the narrowly documented public bundle constant, not a secret.

## 1. Host map and observed request envelope

| Host | Captured role | Key evidence | Boundary |
| --- | --- | --- | --- |
| `restaurant-api.wolt.com` | Search, saved delivery-info lookup, post-checkout configuration, purchase creation, and operational tracking | HAR 439, 666, 759, 762, 784/798 | Purchase and tracking bodies are highly sensitive. |
| `consumer-api.wolt.com` | Venue/page models, assortment, persisted basket, checkout quote, presentation-oriented order tracking | HAR 457/460/467/469, 587/589/591, 599–757, 789/793 | Page responses are server-driven UI models, not a stable public SDK. |
| `payment-service.wolt.com` | Payment-method selection UI for checkout | HAR 675, 676, 720 | Masked-card details, BIN, scheme, expiry, and method IDs are sensitive. |
| `converse-api.wolt.com` | Support-chat token and conversation APIs | HAR 195, 199, 201 | This is distinct from the ordering path; do not reuse its token for ordering. |
| `consumer-events.wolt.com` | Authenticated WebSocket with purchase notifications | HAR 168, including four captured frames | Login frame contains an access token; never log raw frames. |
| `wolt-com-static-assets.wolt.com` | Captured browser bundles used only for auth/signing clues | HAR 6, 17, 24, 25, 27, 625 | Bundle code is evidence of implementation hints, not authorization to automate. |

### Common browser headers observed

The focused ordering requests carried the following browser-context headers. Values are intentionally omitted:

```text
accept: <browser accept value>
app-currency-format: <currency-format setting>
app-language: <language>
client-version: <browser-client-version>
clientversionnumber: <browser-client-version-number>
platform: <platform>
w-wolt-session-id: <opaque session identifier>
x-wolt-web-clientid: <opaque web-client identifier>
content-type: application/json                 # JSON writes only
x-adyen-checkout-sdk-version: <SDK version>    # purchase request only, HAR 762
```

`w-wolt-session-id` and `x-wolt-web-clientid` appeared on every focused ordering request inspected. `Authorization` and `Cookie` headers did **not** appear on those HAR entries. That absence does not mean the endpoints are anonymous, nor does it show which headers are required. The full authentication/session contract is unknown.

Browser CORS preflights also appear in the HAR; endpoint references below describe the substantive request, not its `OPTIONS` preflight.

## 2. Identifier and representation map

| Placeholder | Observed representation and use | Verified joins in this capture |
| --- | --- | --- |
| `<venue_slug>` | Public route selector used by venue static/dynamic and assortment routes | Search result `venue.slug` can drive slug routes. |
| `<venue_id>` | Opaque venue identifier | Search/menu/basket/final checkout/purchase use the same value. |
| `<menu_item_id>` | Opaque catalog item identifier | The selected basket, checkout `menu_items`, and purchase `items` used the same selected ID. |
| `<item_option_config_id>` | Item-level option configuration ID | The selected basket option ID matched an item option-reference `id` in the assortment for this item. It did **not** match the root option ID in this sample. |
| `<option_value_id>` | Opaque selected option-value ID | Selected basket/checkout/purchase values matched IDs present in the assortment option-value list. |
| `<basket_id>` | Persisted basket response ID | Returned by basket save/list responses. It does not appear in the captured purchase payload. |
| `<delivery_info_id>` | Saved delivery-info ID | `GET /v2/delivery/info` returned it; final checkout used it as a string and purchase wrapped the same value as `{"$oid":"<delivery_info_id>"}`. |
| `<payment_method_id>` | Saved-payment-method reference | Payment selection request → final checkout request `purchase_plan.payment_methods[*].id` → purchase `payment_method_id`. |
| `<checkout_id>` | Server-generated checkout quote ID | Final checkout response `id` equals purchase `checkout_id`. |
| `<purchase_id>` | Purchase/order identifier | Purchase response `results.id.$oid` equals both tracking route IDs and `order_details.order_id`. |

The HAR mixes ordinary strings with Mongo-style wrappers such as `{"$oid":"<id>"}` and `{"$date":11111}`. Treat those as observed wire shapes, not a general serialization guarantee.

### Quote-to-purchase integrity join

The final quote at **HAR 757** and purchase at **HAR 762** establish these exact object/value relationships for the captured order:

Here `plan` is the **request** body's `purchase_plan`, `quote` is the checkout **response**, and `purchase` is the purchase **request**. The checkout response does not contain root `venue`, `delivery`, or `payment_methods` fields. Retain the submitted plan alongside the response instead of trying to recover those selections from the quote.

```text
quote.id                                 == purchase.checkout_id
quote.purchase_validation                == purchase.price_shadowing
quote.purchase_validation.end_amount     == purchase.end_amount
quote.purchase_validation.delivery_price == purchase.delivery_price
plan.venue.id                            == purchase.venue_id
plan.delivery.delivery_info_id           == purchase.delivery_info.id.$oid
plan.payment_methods[0].id                == purchase.payment_method_id
plan.payment_methods[0].type              == purchase.payment_method_type
plan.courier_tip                         == purchase.tip_amount
```

A future client should treat the checkout output as a fresh server quote and preserve the matching `purchase_validation` object unchanged as `price_shadowing`. The capture does **not** establish how long a quote is valid, which changes invalidate it, or whether the server rejects a stale join.

## 3. Observed ordering walkthrough

### 3.1 Search, venue, menu, and option discovery

**Search — observed HTTP:** `POST https://restaurant-api.wolt.com/v1/pages/search` → `200` at **HAR 439**.

```json
{
  "q": "<query>",
  "target": null,
  "lat": "<latitude>",
  "lon": "<longitude>"
}
```

`lat` and `lon` were numbers in the HAR; they are strings above solely to redact coordinates. A compact response shape is:

```json
{
  "search_id": "<search_id>",
  "expires_in_seconds": 11111,
  "sections": [
    {
      "template": "<template>",
      "title": "<section_title>",
      "items": [
        {
          "template": "<template>",
          "title": "<result_title>",
          "link": {
            "target": "<target>",
            "target_sort": "<target_sort>",
            "target_title": "<target_title>",
            "title": "<link_title>",
            "type": "<link_type>"
          },
          "venue": {
            "id": "<venue_id>",
            "slug": "<venue_slug>",
            "currency": "<currency>",
            "delivers": true,
            "online": true
          }
        }
      ]
    }
  ]
}
```

Useful server-driven extraction paths are:

- `$.sections[*].items[*].venue.id` and `.slug` for the next venue request.
- `$.sections[*].items[*].venue.currency` for currency context.
- `$.sections[*].items[*].link` for the UI-provided route/action, rather than synthesizing navigation from labels.
- `$.search_id` and `$.expires_in_seconds` as presentation/session data; their reuse semantics are unknown.

**Venue/menu reads — observed HTTP:**

| Route | Observed entry | Useful response areas |
| --- | ---: | --- |
| `GET /order-xp/web/v1/pages/venue/slug/<venue_slug>/static` | 457 | `venue.id`, `venue.currency`, delivery-method and static venue metadata. |
| `GET /order-xp/web/v1/venue/slug/<venue_slug>/dynamic/?lat=<latitude>&lon=<longitude>&selected_delivery_method=homedelivery` | 460 | Current open/delivery state, `venue.delivery_configs`, `order_minimum`, server-provided banners. `homedelivery` is a captured value, not a complete delivery-method enum. |
| `GET /consumer-api/venue-content-api/v3/web/venue-content/slug/<venue_slug>` | 467 | Server-driven sections, item option references, substitution preferences. |
| `GET /consumer-api/consumer-assortment/v1/venues/slug/<venue_slug>/assortment` | 469 | Normalized `items`, root `options`, categories, languages, and variant groups. |
| `GET /order-xp/web/v1/pages/venue/<venue_id>/item/<menu_item_id>?language=<language>` | 541 | Item-detail page, restrictions, quantity limits, availability, option configuration. |

The assortment and item-detail data are the safest captured source for building a selection; do not fabricate IDs, prices, restrictions, or option constraints from text labels.

```json
{
  "items": [
    {
      "id": "<menu_item_id>",
      "price": 11111,
      "min_quantity_per_purchase": 1,
      "max_quantity_per_purchase": 2,
      "allowed_delivery_methods": ["homedelivery"],
      "restrictions": [],
      "options": [
        {
          "id": "<item_option_config_id>",
          "option_id": "<root_option_id>",
          "prerequisite_values": [],
          "multi_choice_config": {
            "total_range": {
              "min": 0,
              "max": 2
            },
            "max_single_selections": 1,
            "free_selections": 0
          }
        }
      ],
      "checksum": "<checksum>"
    }
  ],
  "options": [
    {
      "id": "<root_option_id>",
      "type": "choice",
      "values": [
        {
          "id": "<option_value_id>",
          "price": 111,
          "multi_choice_config": {
            "total_range": {
              "min": 0,
              "max": 2
            }
          }
        }
      ]
    }
  ]
}
```

The root assortment option `type` values observed here were `choice` and `multi_choice`; this is **not** a complete enum. The selected purchase representation used the differently cased values `Choice` and `Multichoice`; the capture does not establish a general conversion rule.

#### Option, count, and item-price handling

1. Start with the selected item’s own `items[*].options[*]` configuration, including `id`, `option_id`, `prerequisite_values`, and `multi_choice_config` (`total_range.min/max`, `max_single_selections`, `free_selections`).
2. Resolve selectable values from assortment `options[*].values[*]`; the capture linked selected option values to this list.
3. Preserve each item `count` and each selected option-value `{id, count, price}`. The capture carries both item count and option-value count separately.
4. Preserve server/catalog prices rather than recomputing them. In this selected item, basket `items[*].price` equaled final checkout `menu_items[*].end_amount`, **not** `base_price`. This is one observed relationship, not a pricing formula.
5. Re-quote after changes. Item availability, minimum/maximum quantities, prerequisite values, restrictions, delivery method, and price can all affect a valid selection; their full validation behavior was not captured.
6. `checksum` is an integrity-looking opaque field. The purchase item checksum matched the selected assortment item checksum and the post-checkout basket checksum in this capture, while the item-detail-page checksum did not. No checksum algorithm, freshness rule, or requiredness was established. Copying the applicable current catalog checksum through this observed representation path is supported by the capture; calculating or substituting a checksum is not.

### 3.2 Persist a basket

**Observed HTTP:** `POST https://consumer-api.wolt.com/order-xp/v1/baskets` → `200` at **HAR 587** and again at **HAR 688**.

```json
{
  "venue_id": "<venue_id>",
  "currency": "<currency>",
  "items": [
    {
      "id": "<menu_item_id>",
      "count": 2,
      "name": "<item_name>",
      "price": 11111,
      "options": [
        {
          "id": "<item_option_config_id>",
          "values": [
            {
              "id": "<option_value_id>",
              "count": 1,
              "price": 111
            }
          ]
        }
      ],
      "substitution_settings": {
        "is_allowed": false
      }
    }
  ]
}
```

```json
{
  "id": "<basket_id>",
  "venue_id": "<venue_id>"
}
```

Related reads:

- `GET /order-xp/web/v1/pages/baskets?lat=<latitude>&lon=<longitude>` → `200` at **HAR 589** (also 669, 692, 788). Extract `$.baskets[*].id`, `.venue`, `.items`, `.total`, availability notices, and UI actions.
- `GET /order-xp/v1/baskets/count` → `200` at **HAR 591**. Extract `$.count`.
- `GET /order-xp/v1/baskets/venue?venue_id=<venue_id>` → `404` at **HAR 511**. The captured response body is JSON `null`, not an error object.

The two basket POSTs had identical request bodies and returned the same basket ID in this session. That is evidence of repeated-save behavior for this one case only; it is **not** proof of idempotency. The persisted basket was still returned after purchase (HAR 788), and no basket-clear/delete mutation was captured. A basket ID neither appears as a purchase field nor substitutes for a checkout quote.

### 3.3 Load a saved delivery target

**Observed HTTP:** `GET https://restaurant-api.wolt.com/v2/delivery/info` → `200` at **HAR 666** (also 139).

The response root is `results[]`. For the observed flow, use only the selected `$.results[*].id` as `<delivery_info_id>`; it was used in the final checkout and purchase joins. The same response contains address, phone, location, address-form, and user data. Do not expose, cache broadly, or turn those details into examples.

The capture does not establish address creation/editing, verification rules, delivery-radius validation, or whether an ID remains valid after a location change.

### 3.4 Request iterative checkout quotes

**Observed HTTP:** `POST https://consumer-api.wolt.com/order-xp/web/v2/pages/checkout` → `200` at **HAR 599, 685, 702, 722, 723, 757**. The final request/response at **HAR 757** is the one joined to purchase HAR 762.

A sanitized shape of that final `purchase_plan` is:

```json
{
  "purchase_plan": {
    "courier_tip": 111,
    "delivery": {
      "delivery_coordinates": {
        "longitude": "<longitude>",
        "latitude": "<latitude>"
      },
      "delivery_info_id": "<delivery_info_id>"
    },
    "delivery_method": "homedelivery",
    "delivery_config": {
      "method": "homedelivery",
      "schedule": "standard",
      "time_slot": null
    },
    "payment_methods": [
      {
        "id": "<payment_method_id>",
        "type": "card",
        "title": "<redacted_payment_label>",
        "number": "<redacted_payment_metadata>",
        "card_bin": "<redacted_payment_metadata>",
        "card_scheme": "<redacted_payment_metadata>"
      }
    ],
    "menu_items": [
      {
        "id": "<menu_item_id>",
        "count": 2,
        "options": [
          {
            "id": "<item_option_config_id>",
            "values": [
              {
                "id": "<option_value_id>",
                "price": 111,
                "count": 1
              }
            ]
          }
        ],
        "base_price": 10000,
        "end_amount": 11111,
        "category_id": "<category_id>",
        "category_ids": ["<category_id>"],
        "exclude_from_credits": false,
        "exclude_from_discounts": false,
        "exclude_from_discounts_min_basket": false,
        "alcohol_permille": 0,
        "restrictions": []
      }
    ],
    "selected_offer_ids": [],
    "use_cash": false,
    "use_credits_and_tokens": false,
    "use_loyalty_points_amount": 0,
    "use_promo_surcharge_ids": [],
    "venue": {
      "id": "<venue_id>",
      "country": "XX",
      "currency": "XXX",
      "self_delivery": false,
      "preorder_config": null
    }
  }
}
```

The final plan’s field set is captured, not a required schema. Earlier quote bodies differed: HAR 599 had no `delivery_info_id`/`delivery_config` and an empty payment-method list; HAR 685/702 added saved-delivery/config context but still had no selected payment method; HAR 722/723/757 included one saved method. Rebuild from current server data rather than merging an old client model.

A compact final response shape is:

```json
{
  "id": "<checkout_id>",
  "payable_amount": 11111,
  "purchase_validation": {
    "end_amount": 10000,
    "delivery_price": 111,
    "credits_amount": null,
    "use_token": null,
    "bag_fee": null,
    "end_amount_rounding": null,
    "wolt_loyalty_currency_amount_converted": null,
    "discounts": [],
    "surcharges": [],
    "priority_delivery_fee": null,
    "time_slot_order_discount": null,
    "offers": []
  },
  "payment_breakdown": {
    "total": {
      "amount": 11111,
      "formatted_amount": "<formatted_amount>",
      "reason": null
    },
    "unallocated": {
      "amount": 0,
      "formatted_amount": "<formatted_amount>",
      "reason": null
    },
    "parts": [
      {
        "amount": {
          "amount": 11111,
          "formatted_amount": "<formatted_amount>",
          "reason": null
        },
        "payment_method": {
          "id": "<payment_method_id>",
          "type": "card"
        }
      }
    ]
  },
  "purchasing_disabled": null,
  "use_backend_pricing_for_shadowing_only": false,
  "delivery_configs": [
    {
      "method": "homedelivery",
      "schedule": "standard",
      "estimate": null,
      "price": {
        "price": {
          "amount": 111,
          "formatted_amount": "<formatted_amount>",
          "reason": null
        },
        "original_price": null,
        "price_style": "<price_style>",
        "price_icon": null
      }
    }
  ],
  "tip_config": {
    "allow_custom_tip": true,
    "min_amount": 0,
    "max_amount": 1000,
    "tip_amounts": [0],
    "tip_options": [
      {
        "amount": 0,
        "amount_label": "<tip_label>",
        "percentage": null,
        "percentage_label": null,
        "is_popular": false
      }
    ]
  },
  "checkout_rows": [
    {
      "template": "<row_template>",
      "headline": "<headline>",
      "sections": [
        {
          "template": "<section_template>",
          "title": "<title>",
          "body": "<body>"
        }
      ]
    }
  ],
  "call_to_action": {
    "value": "<cta_label>",
    "style": "<cta_style>",
    "enabled": true,
    "link": "<cta_link>",
    "variant": "<cta_variant>"
  }
}
```

Useful checkout extraction paths:

- Quote join and pricing snapshot: `$.id`, `$.purchase_validation`, `$.payable_amount`.
- Payment allocation: `$.payment_breakdown.total.amount`, `.unallocated.amount`, and `.parts[*].{amount,payment_method}`.
- Displayed price explanation: `$.checkout_rows[*]`, including nested `sections[*]`, and `$.delivery_configs[*].price.price.amount`.
- Tip/offer UI: `$.tip_config`, `$.offers.{selectable,applied}`, and `$.delivery_configs[*]`.
- Server-driven action state: `$.purchasing_disabled`, `$.call_to_action`, and, when present, `$.mosaic_checkout_rows_page`.

Do not treat `call_to_action.enabled` as a safe charge predicate. It was `true` even in the early quote responses where `payment_breakdown.unallocated.amount` was nonzero and no saved payment method was selected. `purchasing_disabled` was `null` in the final sample, which also does not establish a universal ready-to-purchase rule.

### 3.5 Inspect and select a saved payment method

**Observed HTTP:** `POST https://payment-service.wolt.com/v1/payment-methods/checkout` → `200` at **HAR 675, 676, 720**.

The first request contained venue/country and `available_methods`; the second added delivery/item tax/restriction context; HAR 720 added `user_selected_payment_methods`. A sanitized selected-method request shape is:

```json
{
  "venue_id": "<venue_id>",
  "available_methods": ["card", "cash"],
  "country": "XX",
  "delivery_method": "homedelivery",
  "user_selected_payment_methods": ["<payment_method_id>"],
  "user_xpay_has_supported_card": false,
  "is_ftu": false,
  "is_gift_order": false,
  "items": [
    {
      "id": "<menu_item_id>",
      "alcohol_permille": 0,
      "product_hierarchy_tags": [],
      "vat_percentage": 0,
      "vat_percentage_decimal": "<vat_decimal>"
    }
  ]
}
```

Observed `available_methods` values in this single request included `applepay`, `card`, `cash`, `cibus`, `edenred`, `epassi`, `gift_card`, `googlepay`, `invoice`, `klarna`, `meal_benefit`, `mobilepay`, `pay_on_delivery`, `paypal`, `paypay`, `paypay_raw`, `pluxee`, `rakutenpay`, `revolutpay`, `smartum`, `swish`, `szep_kh`, `szep_mkb`, `szep_otp`, `updejeuner`, and `vipps`. This is a captured list, **not** a complete or globally available payment enum.

The response is a server-driven element tree, not a flat method array:

```json
{
  "root_element": {
    "element_type": "list",
    "element_id": "<element_id>",
    "title": "<title>",
    "children": [
      {
        "element_type": "group",
        "element_id": "<element_id>",
        "children": [
          {
            "element_type": "payment-method",
            "element_id": "<element_id>",
            "method": {
              "type": "card",
              "id": "<payment_method_id>",
              "is_tokenized": true,
              "nickname": "<redacted_payment_metadata>",
              "masked_number": "<redacted_payment_metadata>",
              "expiry": {
                "month": "<redacted_payment_metadata>",
                "year": "<redacted_payment_metadata>"
              },
              "scheme": "<redacted_payment_metadata>",
              "card_bin": "<redacted_payment_metadata>",
              "is_comment_required": false,
              "used_by_subscriptions": [],
              "used_by_cards": [],
              "cvv_length": 0,
              "is_subscription_backup_allowed": false
            },
            "is_selected": true,
            "is_enabled": true,
            "is_default": false,
            "available_actions": []
          },
          {
            "element_type": "button",
            "action": "add_card"
          }
        ]
      }
    ]
  }
}
```

Walk `$.root_element` recursively through every `.children[*]`; identify eligible saved-method nodes by `element_type == "payment-method"`, then read only the server-supplied `method.id`, `method.type`, `is_enabled`, and selection state. Do not infer eligibility from title text, and do not retain/return masked number, BIN, expiry, scheme, nickname, or CVV-related metadata.

In this capture, the method ID submitted in HAR 720 matched final checkout HAR 757 request `purchase_plan.payment_methods[0].id`, and that matched purchase HAR 762 `payment_method_id`; the type was `card` throughout. Card enrollment, raw card fields, card challenge/3DS/SCA flows, declines, expiration, and alternate payment execution were not captured.

### 3.6 Obtain post-checkout configuration

**Observed HTTP:** `POST https://restaurant-api.wolt.com/v1/post-checkout-config` → `200` at **HAR 759**.

This request uses another item representation. Unlike basket/checkout option values, `basket[*].options[*].values` is an object mapping selected value IDs to counts:

```json
{
  "venue_id": "<venue_id>",
  "country": "XX",
  "basket": [
    {
      "id": "<menu_item_id>",
      "configIndex": 0,
      "count": 2,
      "name": [
        {
          "value": "<localized_item_name>",
          "lang": "<language>"
        }
      ],
      "category": "<category_id>",
      "exclude_from_credits": false,
      "exclude_from_discounts": false,
      "exclude_from_discounts_min_basket": false,
      "from_recommendation": false,
      "alcohol_percentage": 0,
      "product_hierarchy_tags": [],
      "vat_percentage": 0,
      "vat_percentage_decimal": "<vat_decimal>",
      "baseprice": 10000,
      "end_amount": 11111,
      "restrictions": [],
      "options": [
        {
          "type": "Choice",
          "id": "<item_option_config_id>",
          "name": [
            {
              "value": "<localized_option_name>",
              "lang": "<language>"
            }
          ],
          "values": {
            "<option_value_id>": 1
          }
        }
      ],
      "checksum": "<checksum>"
    }
  ]
}
```

```json
{
  "required_consents": []
}
```

For the captured selected item, ID, `configIndex`, counts, amounts, selected option IDs/value counts, and checksum were carried into the purchase item. The empty `required_consents` array is only a result for this example; it does not prove that consent is never required. `configIndex` provenance/meaning remains unknown and should not be guessed. Catalog-to-post-checkout-to-purchase checksum copying is observed, while checksum construction remains unknown.

### 3.7 Create the purchase

**Observed HTTP:** `POST https://restaurant-api.wolt.com/v2/purchases` → `200` at **HAR 762**.

This is the charge-and-place-order boundary. It is the only captured purchase creation call; no direct client should replay it from a HAR or treat it as an approved autonomous action.

```json
{
  "client_nonce": "<client_nonce>",
  "ravelin_device_id": "<ravelin_device_id>",
  "signature_datetime": {
    "$date": 11111
  },
  "language": "<language>",
  "currency": "XXX",
  "client_pre_estimate": "<opaque_pre_estimate>",
  "consumer_comment": "<redacted_comment>",
  "corporate_order_comment": "<redacted_comment>",
  "delivery_method": "homedelivery",
  "items": [
    {
      "id": "<menu_item_id>",
      "configIndex": 0,
      "count": 2,
      "name": [
        {
          "value": "<localized_item_name>",
          "lang": "<language>"
        }
      ],
      "exclude_from_credits": false,
      "exclude_from_discounts_min_basket": false,
      "from_recommendation": false,
      "alcohol_percentage": 0,
      "product_hierarchy_tags": [],
      "vat_percentage": 0,
      "vat_percentage_decimal": "<vat_decimal>",
      "baseprice": 10000,
      "end_amount": 11111,
      "restrictions": [],
      "options": [
        {
          "type": "Choice",
          "id": "<item_option_config_id>",
          "name": [
            {
              "value": "<localized_option_name>",
              "lang": "<language>"
            }
          ],
          "values": [
            {
              "count": 1,
              "name": [
                {
                  "value": "<localized_option_value_name>",
                  "lang": "<language>"
                }
              ],
              "price": 111,
              "id": "<option_value_id>"
            }
          ]
        }
      ],
      "checksum": "<checksum>"
    }
  ],
  "type": "purchase",
  "signature": "N/A",
  "pricing_model_version": 2023,
  "payment_method_type": "card",
  "payment_method_id": "<payment_method_id>",
  "end_amount": 10000,
  "tip_amount": 111,
  "no_credits_or_tokens": true,
  "device_channel": "browser",
  "to_type": "venue",
  "venue_id": "<venue_id>",
  "browser_info": {
    "color_depth": 0,
    "java_enabled": false,
    "language": "<language>",
    "screen_height": 0,
    "screen_width": 0,
    "time_zone_offset": 0,
    "user_agent": "<redacted_device_data>"
  },
  "payment_links": {
    "return_url": "<return_url>"
  },
  "delivery_info": {
    "id": {
      "$oid": "<delivery_info_id>"
    },
    "use_last_100m_address_picker": false
  },
  "delivery_price": 111,
  "additional_checkout_options": {
    "no_contact_delivery": false
  },
  "discounts": [],
  "offers": [],
  "surcharges": [],
  "use_token": false,
  "price_shadowing": {
    "end_amount": 10000,
    "delivery_price": 111,
    "credits_amount": null,
    "use_token": null,
    "bag_fee": null,
    "end_amount_rounding": null,
    "wolt_loyalty_currency_amount_converted": null,
    "discounts": [],
    "surcharges": [],
    "priority_delivery_fee": null,
    "time_slot_order_discount": null,
    "offers": []
  },
  "menu_items_source": "consumer-assortment",
  "checkout_id": "<checkout_id>",
  "use_self_service_cancellation": false
}
```

`homedelivery`, `purchase`, `card`, `browser`, `venue`, `consumer-assortment`, and the public bundle constant `N/A` are literal values observed in this payload; they are not complete enums or universal protocol guarantees. The numeric `pricing_model_version` shown above is an observed literal from this one request. All monetary/example scalar values other than that version are synthetic.

A compact success shape is:

```json
{
  "results": {
    "id": {
      "$oid": "<purchase_id>"
    },
    "status": "received",
    "currency": "XXX",
    "amount": 11111,
    "delivery_method": "homedelivery",
    "delivery_price": 111,
    "payment_method_type": "card"
  }
}
```

`results` also contained delivery location, payment references, item details, venue snapshots, timestamps, and operational state. Do not log the raw response. The `200`/`received` result establishes creation and initial state only; it does **not** prove eventual acceptance, fulfillment, delivery, or final payment settlement.

### 3.8 Track the new order

| Route | Entries | Response model / extraction |
| --- | ---: | --- |
| `GET /v2/order_details/subscriptions` | 784 | `$.order_details[*]`, `$.expires_in_seconds`; new order appeared with `status: "received"`. |
| `GET /v2/order_details/purchase_tracking/<purchase_id>` | 798 | `$.order_details` object, `$.drivers`, `$.expires_in_seconds`, `$.delivery_instructions_nudge.show`. |
| `GET /v2/order_details/by_ids?purchases=<purchase_id>[,<purchase_id>...]` | 796, 808, 869, 874, 878, 880 | `$.order_details[*].order_id`, status, price/payment fields. The query was one comma-delimited parameter in observed multi-ID requests. |
| `GET /order-xp/v1/pages/order-tracking/<purchase_id>` | 793 | Presentation context at `$.purchase_context`, plus server-driven `$.banners` and multi-venue ordering data. |
| `GET /order-xp/web/v1/pages/orders` | 789 | History/page UI at `$.orders[*].{purchase_id,status,items,venue,call_to_action}`. |

A compact dedicated-tracking response shape is:

```json
{
  "delivery_instructions_nudge": {
    "show": false
  },
  "drivers": [],
  "expires_in_seconds": 11111,
  "order_details": {
    "order_id": "<purchase_id>",
    "status": "received",
    "currency": "XXX",
    "payment_amount": 11111,
    "total_price": 11111,
    "delivery_price": 111,
    "cancellable_status": {
      "reason": "<reason>",
      "show_timer": false,
      "start": {
        "$date": 11111
      },
      "until": {
        "$date": 22222
      }
    },
    "items": []
  }
}
```

The new order’s captured status changed from `received` (purchase HAR 762; tracking HAR 784/798/808) to `acknowledged` in later `by_ids` responses (HAR 869 onward). That is an observed string transition, not proof of a complete state machine. A separate, older order in the same history was `delivered`; it is not evidence that this new order completed. The dedicated tracking response initially had an empty `drivers` array. Polling cadence, expiry semantics, driver-location consent, cancellation mutation, rejection/refund, and terminal-state behavior were not established.

#### WebSocket notifications

**Observed transport:** `wss://consumer-events.wolt.com/ws` at **HAR 168** returned `101`. Its `_webSocketMessages` extension contains four text frames (opcode `1`), in this order:

| Direction | `type` | Other top-level fields |
| --- | --- | --- |
| Client to server | `__login` | `accessToken` |
| Server to client | `__loggedIn` | None |
| Server to client | `purchase_received` | `purchase_received` object with `purchase_id`, `cancellable_status` |
| Server to client | `purchase_acknowledged` | `purchase_acknowledged` object with `purchase_id`, `automatic_rejection_time` |

The login frame shape is:

```json
{"type": "__login", "accessToken": "<consumer_events_access_token>"}
```

Both event payloads' `purchase_id` values equal purchase HAR 762 `results.id.$oid`. These frames establish push notifications for this order, not merely a WebSocket upgrade. HAR entry indexes order connection creation, so an entry opened before checkout can contain later purchase events.

The token's issuance, refresh, and equivalence to an ordering HTTP token are not established. No heartbeat, reconnection, replay cursor, acknowledgement protocol, subscription filter, or delivery guarantee is shown by these four frames. A library should use notifications to trigger an HTTP status refresh, and reconcile known orders after reconnecting rather than assume every event was delivered. Do not interpret a lost connection as a failed purchase or resend a purchase on reconnect.

## 4. Pricing, payment allocation, and quote safety

The captured amounts must remain distinct:

| Field | Observed relationship | Do not infer |
| --- | --- | --- |
| `checkout.payable_amount` | Equal to `checkout.payment_breakdown.total.amount` in all six captured checkout responses. Equal to new-order tracking `payment_amount` and `total_price` at HAR 808+. | That it is necessarily the final amount charged or a universal formula. |
| `checkout.purchase_validation.end_amount` | Equal to purchase `end_amount` through the quote-to-purchase join. | That it equals `payable_amount`; it did not in this capture. |
| `checkout.purchase_validation.delivery_price` | Equal to purchase `delivery_price`. | That it alone explains delivery charges/discounts. |
| `checkout_rows`, `delivery_configs`, `tip_config`, `offers`, `payment_breakdown` | Server-provided display/allocation structures useful for a confirmation UI. | That local addition/subtraction of their fields reproduces backend pricing. |

The HAR did not establish a universal arithmetic formula among item amounts, delivery, service/small-order fees, tip, discounts, credits, offers, loyalty, rounding, or surcharges. Money-like fields were JSON integers, but currency exponent/unit semantics were not proven. Present the server quote faithfully and preserve its validation snapshot; do not locally recompute a payable total.

Before any purchase attempt, obtain a fresh final quote after every substantive change to venue, item, option value/count, delivery target/configuration, tip, payment method, offer, credit/token choice, or timing. The capture does not prove which change forces requoting, so this is a conservative safety rule.

## 5. Authentication, signing, integrity, and token evidence

### 5.1 Observed HTTP facts

- No `Authorization` or `Cookie` header was captured on the focused ordering requests. The bodies and browser-context headers are still sensitive.
- A purchase request included nonempty `client_nonce`, `ravelin_device_id`, `signature_datetime`, `signature`, `browser_info`, and item `checksum` fields (HAR 762). Their presence does not prove requiredness, algorithm, lifetime, or portability.
- No `Idempotency-Key`-like header appeared in the focused operations. `client_nonce` is not evidence of an idempotency contract.
- No consumer login/token exchange, refresh, logout, unauthorized response, or scope check was captured on an established ordering host.

### 5.2 Bundle-derived consumer-auth clues

The following are static-JS clues only:

- The literal path `/v1/wauth2/access_token` occurs in captured static-bundle responses at **HAR 6, 17, and 625**.
- HAR 6’s refresh path calls its access-token API with `grantType: "refresh_token"` and conditionally adds `refreshToken`. Its helper converts those camel-case input names to `grant_type` and `refresh_token` before `URLSearchParams` serialization, uses a configured `serviceUrl`, and requests with browser credentials.
- HAR 17 declares public auth-storage constants `AUTH.ACCESS_TOKEN_NAME: "__wtoken"`, `AUTH.REFRESH_TOKEN_NAME: "__wrtoken"`, and `COOKIE_TTL_DAYS`; its authorization path reads `__wrtoken` through a storage helper. This is source evidence about storage names/configuration, not token values or proof that a particular browser state exists.
- HAR 24's chunk 31620 contains module 601336, which exports a helper that registers an HTTP request interceptor. When its auth state is `status === "logged-in"`, it sets `headers.authorization` to `Bearer <access-token>` on the clients passed to that helper.
- HAR 625 contains a token-result shape with `access_token`, `expires_in`, and `refresh_token`.

This static evidence does not identify the runtime auth host, establish grant scopes/lifetimes, or prove that module 601336 is installed on any consumer/restaurant/payment client. No ordering HTTP request inspected in this HAR demonstrates an `Authorization` header. Separately, HAR 168 does contain a WebSocket access token in its login frame; that token must stay redacted, and its presence does not resolve the HTTP authentication contract.

### 5.3 Support-chat token is a separate boundary

**Observed HTTP:** `POST https://converse-api.wolt.com/auth-api/v2/token` → `200` at **HAR 195**.

```json
{
  "grantType": "<grant_type>",
  "audience": "<audience>",
  "refreshToken": "<support_chat_refresh_token>",
  "appId": "<support_chat_app_id>"
}
```

```json
{
  "accessToken": "<support_chat_access_token>",
  "expiresIn": 11111,
  "tokenType": "<token_type>",
  "refreshToken": "<support_chat_refresh_token>"
}
```

Subsequent captured support-chat calls use `converse-api.wolt.com` and `x-converse-*` client headers (HAR 199/201). There is no capture evidence that this access token is accepted by `consumer-api.wolt.com`, `restaurant-api.wolt.com`, or `payment-service.wolt.com`. Treat it as support-chat scoped and never substitute it for consumer ordering authentication.

### 5.4 Signature and checksum investigation

- **HAR 25** contains the bundled purchase-payload runtime schema with `signature` and `signature_datetime` fields.
- **HAR 27** contains the browser purchase-payload builder. It sets `signature_datetime` as `{"$date": Date.now()}` and assigns the public constant `signature: "N/A"` alongside `client_nonce`, `ravelin_device_id`, and browser-info collection. **HAR 762** carries that same `N/A` literal.
- `N/A` is therefore a fixed, noncomputed value in this captured browser path rather than a demonstrated cryptographic signature construction. The capture does not establish whether the server requires, ignores, or treats it differently in another flow.
- A scan of the captured bundles found `signature_datetime` only in HAR 25 and 27. Crypto APIs appear elsewhere in the bundle set, but no captured code path linked a cryptographic operation to this purchase `signature` field.
- The selected assortment checksum (HAR 469) propagated into the post-checkout basket (HAR 759) and purchase item (HAR 762), but no code or server rule for constructing/validating it was established.

The capture does not provide a general signing algorithm, key source, checksum algorithm, server-side nonce rule, server freshness requirement, or failure response. It does support the narrow, capture-correlated facts that this browser builder used `N/A`, generated the timestamp with `Date.now()`, and copied the applicable assortment checksum along the selected-item path. Treat all three as evidence for this path—not a universal server contract—until runtime validation covers other flows.

### 5.5 Browser nonce and purchase-builder details

**Bundle-derived, HAR 27:** purchase-builder module 288089 calls module 327848 export `S` with the venue ID to populate `client_nonce`. That helper reads an array of `{nonce, venueId}` records under the public storage key `WOLT_ORDER_NONCE`. It returns the existing nonce for that venue if present; otherwise it invokes its generator, replaces that venue's record, and retains at most five records. Storage helper module 17216 export `i` uses JSON-serialized `localStorage` with runtime shape validation. Module 327848 also exports the replacement helper as `J`.

This establishes browser-side per-venue reuse, not a server idempotency guarantee. The generator algorithm, replacement call sites, and replacement timing have not been established here. Do not assume either "new nonce on every HTTP retry" or "same nonce forever for a venue" is a correct Python implementation.

Other useful clues in builder module 288089:

- `items` strips `category` and `exclude_from_discounts` from the supplied purchase-menu items. Those keys are present in post-checkout HAR 759 and absent from purchase HAR 762; this omission is deliberate in the builder, not a reason to drop other unknown fields.
- `client_pre_estimate` is constructed from estimate ranges as a `min-max` string, with separate priority/preorder branches and an empty-string fallback. It is not shown as a cryptographic token. The standard estimate's upstream source still needs tracing.
- `ravelin_device_id` comes from a `ravelinDeviceId` argument. This builder does not show its generation or prove it can be omitted or synthesized.
- The timestamp is milliseconds since the Unix epoch (`Date.now()`), so the equivalent Python wire construction is `{"$date": time.time_ns() // 1_000_000}` after importing `time`.
- The builder has conditional `payment_plan`, preorder, time-slot, gifting, and other branches. Their source presence does not validate those flows; HAR 762 only demonstrates the selected saved-card delivery path.

## 6. Duplicate, failure, and retry safety

Only successful `200` write responses were captured for basket, quote, payment-method UI, post-checkout configuration, and purchase. The focused negative result was the basket-by-venue `404` at HAR 511 with JSON `null`. There are no captures for timeout-after-acceptance, duplicate purchase, stale quote, rejected item/option, venue closure, minimum order, invalid delivery range, payment decline, payment challenge, or consent failure.

**Do not blindly retry `POST /v2/purchases`.** If a transport timeout happens after the request leaves the client, the server may already have created an order or initiated payment. The nonce’s presence does not prove deduplication. Recommended operational behavior, pending real contract evidence:

1. Preserve the user-visible confirmation, final quote ID, validation snapshot, and local request-attempt state without recording secrets.
2. Do not send a second purchase just because the first response is absent.
3. Reconcile through a known purchase ID only when one is available; otherwise require an explicit human recovery path rather than guessing from duplicate candidate orders.
4. Require a new explicit confirmation before any intentionally repeated order attempt.

## 7. Recommended MCP boundaries (recommendations, not captured API contract)

These are product-safety recommendations only. They do not imply that the corresponding HTTP calls are stable, authorized, or sufficient to implement an MCP server.

| Proposed tool | Side effect | Confirmation/privacy boundary |
| --- | --- | --- |
| `search_venues` / `get_venue` / `get_assortment` / `get_item` | Read | Require explicit user-provided location before using coordinates; return only needed venue/menu data. |
| `list_saved_delivery_targets` | Read of sensitive profile data | Return opaque target IDs and user-approved display labels only; do not expose street, phone, coordinates, or address-form details. |
| `save_basket` | Persists a basket | Label as a saved-basket mutation, not a purchase; require an explicit instruction to persist. |
| `quote_checkout` | Remote quote; may disclose delivery/payment context | Require selected delivery target and payment-method reference from the user. Return quote/UI fields with sensitive values redacted. Re-quote on change. |
| `list_payment_methods` | Read of sensitive payment UI | Return opaque method ID/type plus a minimally necessary user-safe label; never return BIN, masked PAN, expiry, CVV metadata, or provider tokens. |
| `prepare_purchase` | Local preparation only | Recommendation: preserve a fresh `<checkout_id>` and exact `price_shadowing`; carry the currently selected catalog checksum where the observed representation path does. `N/A` and `{"$date": Date.now()}` are bundle evidence for this browser path. Nonce generation/reset timing, cross-flow field rules, and consent/challenge behavior remain unverified. |
| `place_order` | Financial/order side effect | Require a final, one-time, explicit user confirmation immediately after displaying venue, item/options/counts, delivery target alias, selected payment alias, tip, `payable_amount`, and `purchase_validation.end_amount` **as distinct fields**. Block automatic retries and any stale or changed quote. |
| `get_order_status` | Read of sensitive operational data | Return only the requested order’s state and necessary ETA/status fields; protect driver/location data and do not infer cancellation capability from UI metadata. |

Separate confirmations are recommended for: adding/replacing a payment method, initiating a payment challenge/redirect, cancellation/refund, changing delivery target, changing any item/options/counts, changing tip/payment/offer/credits, and any repeat order. Those flows need explicit product decisions and separate approved captures rather than assumptions derived from this reference.

## 8. Missing capture scenarios before a safe client

1. **Consumer authentication:** approved login, host discovery, access/refresh/expiry/logout behavior, scopes, unauthorized responses, and secure session handling.
2. **Signing/integrity:** nonce/signature/checksum provenance, freshness windows, validation failures, and whether fields are required for every flow.
3. **Purchase recovery:** duplicate-submit behavior, timeout after server acceptance, idempotency semantics, and a reliable server-side reconciliation path.
4. **Payment execution:** saved-card failure/decline/expiry, 3DS/SCA or redirect/challenge, payment status, fresh-card enrollment, alternate methods, and any payment tokenization contract.
5. **Quote invalidation:** stale checkout IDs, price/availability change, item-option rejection, delivery range, venue closed, minimum order, fees, offers/credits/loyalty/tip interactions, and backend error shapes.
6. **Consent and fulfillment:** nonempty post-checkout consent, age verification, pickup/preorder/multi-venue behavior, cancellation mutation, rejection/refund, and the new order through a terminal state.
7. **Basket lifecycle:** edit/remove/expiry semantics and whether a completed purchase should clear or preserve a basket.
8. **Tracking/privacy:** polling cadence, WebSocket token lifecycle/reconnect/replay semantics beyond the four observed frames, driver/location update handling, expiry meaning, and access control for historical/current orders.

Recommendation: until those captures are approved and studied, keep browsing and quoting separate from any human-confirmed purchase action.

## 9. Python library implementation guide

This section recommends a library design based on the evidence above; the proposed Python names are not Wolt APIs. A bot can call the library directly. MCP is optional, and the recommendations in section 7 do not require an MCP implementation.

### 9.1 Preserve the plan, quote, and selected catalog data

Keep three separate internal objects: the selected catalog item/options, the submitted `purchase_plan`, and the returned quote. Do not model the quote as a complete order draft: it does not return all selections. Keep sensitive fields available internally only where needed for serialization, and expose a smaller bot-facing summary.

| Proposed method | Operation and return value |
| --- | --- |
| `search_venues(query, latitude, longitude)` | Search endpoint; extract venue IDs/slugs from sections, skipping non-venue results. |
| `get_assortment(venue_slug)` | Catalog plus item/root-option lookup tables. Preserve constraints and opaque checksums. |
| `list_delivery_targets()` | Saved delivery-info references with suitable private display labels. |
| `get_payment_methods(context)` | Payment-service element tree flattened recursively to enabled method references; this is a POST despite being a discovery operation. |
| `save_basket(selection)` | Explicit persisted-basket mutation; keep it separate from local selection edits. |
| `quote_checkout(plan)` | Return an internal snapshot containing a deep copy of both the submitted plan and response. |
| `get_post_checkout_config(selection)` | Return required consents using the distinct post-checkout basket representation. |
| `prepare_purchase(snapshot, context)` | Local conversion and checks only; fail explicitly when required implementation inputs are unavailable. |
| `place_order(prepared_order)` | One explicit network submission, with automatic retries disabled. |
| `get_order_status(purchase_id)` | Operational tracking endpoint; preserve unknown future status strings. |

The recorded sequence is discovery, basket save, preliminary quote, delivery/payment context, revised quotes, post-checkout configuration, purchase, and tracking. A simpler library can expose these phases without reproducing every browser presentation request. However, the capture does not prove which browser calls can be omitted; in particular, absence of a basket ID from purchase does not prove basket persistence is unnecessary server-side.

### 9.2 Item adapters, not one shared wire model

For the selected item in HAR 469/587/757/759/762, these joins were checked directly:

| Source | Destination | Evidence / handling |
| --- | --- | --- |
| Assortment item `price` | Plan item `base_price`, purchase item `baseprice` | Equal for the selected item. The wire spelling changes. |
| Basket item `price` | Plan item and purchase item `end_amount` | Equal for this selected configuration, and different from base price. Not a universal pricing formula. |
| Plan item `id`, `count`, `end_amount` | Purchase item fields with the same names | Matched in the final plan/purchase. |
| Item option-reference `id` | Selected option `id` | Use the item configuration ID, not the root assortment option ID. |
| Basket/plan option `values` array | Post-checkout option `values` map | Convert selected `{id, count, price}` entries to `{value_id: count}` for post-checkout only. |
| Selected values plus catalog localized names | Purchase option `values` array | Purchase values include `id`, `count`, `price`, and localized `name` records. Do not send the post-checkout map here. |
| Assortment item `checksum` | Post-checkout and purchase item `checksum` | Identical in this capture; item-detail checksum was different. |

Names in basket payloads are display strings; post-checkout and purchase names are arrays of `{value, lang}` records. Likewise, plan `base_price` and `alcohol_permille` differ from purchase `baseprice` and `alcohol_percentage`. Do not mechanically rename alcohol fields without checking units: the selected sample is zero and cannot establish a conversion factor.

Use explicit serializers for basket, checkout plan, post-checkout configuration, and purchase. Keep `configIndex` in the local selected-item representation; its general assignment rules still need investigation. Avoid choosing default counts, prices, IDs, or checksums merely to make a payload serialize.

### 9.3 Quote snapshot and submission checks

A minimal local snapshot helper can be written using only Python's standard library:

```python
from copy import deepcopy


def capture_quote_snapshot(plan: dict, response: dict) -> dict:
    # The API returns pricing, not a complete copy of the selected order.
    return {
        "plan": deepcopy(plan),
        "quote": deepcopy(response),
    }
```

The deep copy prevents later item/payment edits from silently mutating a saved quote's plan. It does not establish a server expiry time or make the returned dictionaries immutable. A production client should keep snapshots private and invalidate them whenever its order draft changes.

Before preparing the captured single-card flow, check the selected payment is enabled, the final quote allocates the full payable amount (`payment_breakdown.unallocated.amount == 0`), and there are no unresolved purchasing restrictions or consents. Also inspect `is_age_verification_required` and `use_address_matching_for_age_verification`, which appear in all six checkout responses; an empty post-checkout `required_consents` array is not a substitute for handling age-verification requirements. These are conservative client checks, not a proven sufficient acceptance predicate.

Copy quote `purchase_validation` unchanged to `price_shadowing`; copy its `end_amount` and `delivery_price` to the respective purchase fields, and take delivery/payment selections from the retained plan. Do not set purchase `end_amount` to quote `payable_amount`. Preserve numeric JSON values as numbers, including coordinates: the coordinate strings in redacted examples are not valid numeric substitutes for live input. Preserve omitted fields versus explicit `null` rather than filling every possible field with `None`.

Use server-provided formatted amounts for display until the currency's amount units have been independently confirmed. Keep integer amounts for transport, not floating-point price calculations.

### 9.4 Transport and bot execution

- Inject authentication through a separate session component. Do not hard-code tokens from this HAR or assume the captured session/client IDs authenticate the user. Keep credentials scoped to explicitly configured service hosts; never forward them indiscriminately to URLs from response UI links or payment redirects.
- Keep the three ordering HTTP base URLs distinct. Some consumer paths literally begin with `/consumer-api/`; do not remove that prefix. Preserve the captured trailing slash on the dynamic venue route. Browser CORS `OPTIONS` calls are not application operations a Python client needs to reproduce.
- Define separate read/quote errors, purchase rejection errors where a response establishes rejection, and an `OrderOutcomeUnknown` condition for ambiguous submission results. Do not assume a non-JSON response, a `5xx`, or a missing response means no order was created. HAR 511 also demonstrates that an error body can be `null`.
- Disable purchase retries in the HTTP client, middleware, job queue, and bot-tool wrapper. Persist a local attempt state before sending and serialize competing submissions for the same confirmed draft. A local lock prevents local duplicates; it does not provide server idempotency.
- Store the returned purchase ID immediately when available. Use that ID for HTTP reconciliation and matching WebSocket events. Keep unknown outcomes pending for recovery rather than automatically turning them into another purchase attempt.
- Treat user confirmation or a clearly defined standing ordering policy as application-level authorization, not a field established by the HAR. Bind authorization to the actual items, delivery target, payment choice, tip, and approved spend. Stop when those inputs change or payment requires an unsupported challenge.
- Do not use the raw HAR as a checked-in test fixture. Build synthetic fixtures for payload adapters, plan/response separation, missing/null error bodies, unknown order statuses, and timeout-without-resubmission behavior.

The main blocker for an operational library remains the HTTP authentication lifecycle. Browser nonce generation/reset behavior, device context, payment challenges, and uncertain purchase outcomes also remain unresolved. The existing request shapes are sufficient to design offline serializers and tests, not to claim a working end-to-end client.

## Appendix: evidence index

| HAR index | Observed operation |
| ---: | --- |
| 439 | Search page request. |
| 457, 460 | Venue static and location-aware dynamic page models. |
| 467, 469, 541 | Venue content, normalized assortment, item detail/options. |
| 511 | Basket-by-venue request returned `404`. |
| 587, 688 | Matching persisted-basket POSTs. |
| 589, 591, 788 | Basket page/count, including post-purchase basket visibility. |
| 599, 685, 702, 722, 723, 757 | Iterative checkout quotes; 757 is the purchase-joined final quote. |
| 666 | Saved delivery-info lookup. |
| 675, 676, 720 | Payment-method checkout UI and selected method. |
| 759 | Post-checkout configuration. |
| 762 | Purchase creation. |
| 784, 798, 808, 869, 874, 878, 880 | Subscription/dedicated/by-ID tracking of the new order. |
| 789, 793 | Consumer orders page and consumer order-tracking page. |
| 195, 199, 201 | Support-chat token/conversation path. |
| 168 | Consumer-events WebSocket upgrade, login, and received/acknowledged purchase frames. |
| 6, 17, 24, 25, 27, 625 | Captured JS auth/payload/signing evidence. |
