# Changelog

## [0.4.1](https://github.com/skorokithakis/woltapi/compare/v0.4.0...v0.4.1) (2026-09-06)


### Bug Fixes

* accept plain catalog item names ([4102cc9](https://github.com/skorokithakis/woltapi/commit/4102cc908b31bb59a09d935329047d4354f65f76))

## [0.4.0](https://github.com/skorokithakis/woltapi/compare/v0.3.0...v0.4.0) (2026-09-06)


### ⚠ BREAKING CHANGES

* WoltClient.get_venue_basket was removed. Its per-venue read returns no venue slug and no option data, so it cannot support rebuilding a basket. Use get_baskets_page() instead.

### Features

* card-free basket saves and saved-basket rebuilds ([8017137](https://github.com/skorokithakis/woltapi/commit/801713757c89849e79cfda1cc7f978bc691e70d5))


### Documentation

* list get_venue_checkout_context in useful methods and note the shape error ([622cc3b](https://github.com/skorokithakis/woltapi/commit/622cc3bba5192d47ca72e043882847bc50068f47))

## [Unreleased]


### Features

* add Basket.from_saved_basket, WoltClient.save_basket_items, and WoltClient.get_venue_checkout_context


### Breaking Changes

* remove WoltClient.get_venue_basket; its per-venue read returns no venue slug or option data, so it cannot rebuild a basket. Use get_baskets_page() instead


### Other

* remove the --context-file flag from examples/order.py

## [0.3.0](https://github.com/skorokithakis/woltapi/compare/v0.2.0...v0.3.0) (2026-09-06)


### Features

* add basket management and server basket reads ([327d84b](https://github.com/skorokithakis/woltapi/commit/327d84bc0802ae3f9e7f919e152ec6c6d60024d8))

## [0.2.0](https://github.com/skorokithakis/woltapi/compare/v0.1.0...v0.2.0) (2026-09-06)


### Features

* compute the configured item price from the catalog ([f97ae53](https://github.com/skorokithakis/woltapi/commit/f97ae533733a808c11a9ae7165a855b08c5b6de6))
* refresh-token examples and checkout-field derivation ([6ea315c](https://github.com/skorokithakis/woltapi/commit/6ea315c4e45906d66fc41adaefd57472562c1c1a))
* save rotated refresh tokens in the examples ([5261de5](https://github.com/skorokithakis/woltapi/commit/5261de59d4cbf1ce27dfe59febdae28c0abed6d4))
* show saved addresses and card labels in the order flow ([fc335ed](https://github.com/skorokithakis/woltapi/commit/fc335ed8d4a1a0e13fb2b50f9bf7aa1b064c7804))


### Bug Fixes

* send the platform header payment-service requires ([3afca69](https://github.com/skorokithakis/woltapi/commit/3afca69833d1c3294a32812fcf2d3c9ed1590f96))

## [0.1.0](https://github.com/skorokithakis/woltapi/compare/v0.0.1...v0.1.0) (2026-09-06)


### Features

* add token refresh and checkout example ([df68ed5](https://github.com/skorokithakis/woltapi/commit/df68ed5a7ba9aa359b0d9fcf438925755e88b6e1))
