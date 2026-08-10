# Changelog

## [1.4.1](https://github.com/nominal-io/instro/compare/instro-unstable-v1.4.0...instro-unstable-v1.4.1) (2026-08-10)


### Bug Fixes

* **awg:** change check_errors() from required to optional in awg ([#374](https://github.com/nominal-io/instro/issues/374)) ([753ecd5](https://github.com/nominal-io/instro/commit/753ecd55d64f9c7752126fb23e357c06e91f2f11))
* **awg:** move check_errors() into driver level instead of outer abstraction ([#381](https://github.com/nominal-io/instro/issues/381)) ([bc461b7](https://github.com/nominal-io/instro/commit/bc461b79f2e56e17a70cb7992a9bc557b85d752b))

## [1.4.0](https://github.com/nominal-io/instro/compare/instro-unstable-v1.3.0...instro-unstable-v1.4.0) (2026-08-05)


### Features

* **awg:** add modulation to AWG ([#335](https://github.com/nominal-io/instro/issues/335)) ([9ea6bbd](https://github.com/nominal-io/instro/commit/9ea6bbd3548de24b809503e5adc4d2e0d1772aab))

## [1.3.0](https://github.com/nominal-io/instro/compare/instro-unstable-v1.2.0...instro-unstable-v1.3.0) (2026-08-04)


### Features

* add support for alicat mc flow controller and the concept of an instro flow controller. First pass. Aimed for future compatibility for different flow control types and different vendors but expect to adapt as new vendors supported. ([d897ee2](https://github.com/nominal-io/instro/commit/d897ee2188117aec6faad1dacadad8b67623e225))
* add support for alicat mc flow controller and the concept of an InstroFlowController ([#132](https://github.com/nominal-io/instro/issues/132)) ([d897ee2](https://github.com/nominal-io/instro/commit/d897ee2188117aec6faad1dacadad8b67623e225))
* **awg:** add Rigol-DG1022Z driver ([#307](https://github.com/nominal-io/instro/issues/307)) ([168be78](https://github.com/nominal-io/instro/commit/168be78a092cf7847410059137f925d7cb7ea2c9))

## [1.2.0](https://github.com/nominal-io/instro/compare/instro-unstable-v1.1.0...instro-unstable-v1.2.0) (2026-07-27)


### Features

* add espec gl controller environmental chamber driver ([#299](https://github.com/nominal-io/instro/issues/299)) ([6cfb658](https://github.com/nominal-io/instro/commit/6cfb65804b549b4410a57bf2f27ce8aed6c54eae))
* **awg:** adding AWGDriverBase and InstroAWG ([#159](https://github.com/nominal-io/instro/issues/159)) ([71baa7e](https://github.com/nominal-io/instro/commit/71baa7ed1aff6166510008500de58db8ce049f12))

## [1.1.0](https://github.com/nominal-io/instro/compare/instro-unstable-v1.0.0...instro-unstable-v1.1.0) (2026-07-07)


### Features

* **dmm:** add Keithley 2750 DMM driver (unstable) ([#237](https://github.com/nominal-io/instro/issues/237)) ([ff01368](https://github.com/nominal-io/instro/commit/ff013685d879c6c8e2105d7c3048abf8d79e7121)), closes [#193](https://github.com/nominal-io/instro/issues/193)

## [1.0.0](https://github.com/nominal-io/instro/compare/instro-unstable-v0.4.0...instro-unstable-v1.0.0) (2026-07-02)


### Miscellaneous

* release main ([#198](https://github.com/nominal-io/instro/issues/198)) ([c12e274](https://github.com/nominal-io/instro/commit/c12e2744537241be228ba49312ab3f4c9be16c61))

## [0.4.0](https://github.com/nominal-io/instro/compare/instro-unstable-v0.3.0...instro-unstable-v0.4.0) (2026-06-30)


### ⚠ BREAKING CHANGES

* **scope:** graduate InstroScope from instro-unstable to core ([#167](https://github.com/nominal-io/instro/issues/167))

### Features

* **ethernetip:** graduate EtherNet/IP out of unstable into a dedicated package ([#178](https://github.com/nominal-io/instro/issues/178)) ([4f716e4](https://github.com/nominal-io/instro/commit/4f716e4e8e3b56fa0cfa4543732bd306f068cea5))
* **scope:** graduate InstroScope from instro-unstable to core ([#167](https://github.com/nominal-io/instro/issues/167)) ([787a8ba](https://github.com/nominal-io/instro/commit/787a8bad9a566f25bb161ee5b34c6dc90eab520f))

## [0.3.0](https://github.com/nominal-io/instro/compare/instro-unstable-v0.2.1...instro-unstable-v0.3.0) (2026-06-25)


### ⚠ BREAKING CHANGES

* 149 bug documentation for get channel does not match behavior ([#151](https://github.com/nominal-io/instro/issues/151))

### Bug Fixes

* 149 bug documentation for get channel does not match behavior ([#151](https://github.com/nominal-io/instro/issues/151)) ([f4f3966](https://github.com/nominal-io/instro/commit/f4f396686fab9b437143730954d0638d28dbbb6d))
* closes [#149](https://github.com/nominal-io/instro/issues/149). First, updates documentation about timeout behavior. ([f4f3966](https://github.com/nominal-io/instro/commit/f4f396686fab9b437143730954d0638d28dbbb6d))

## [0.2.1](https://github.com/nominal-io/instro/compare/instro-unstable-v0.2.0...instro-unstable-v0.2.1) (2026-06-24)


### Bug Fixes

* **ethernetip:** remove strings from Python EIP surface ([#128](https://github.com/nominal-io/instro/issues/128)) ([c133d81](https://github.com/nominal-io/instro/commit/c133d814a66b68775fb5a05114cf1ccac22aa466))

## [0.2.0](https://github.com/nominal-io/instro/compare/instro-unstable-v0.1.0...instro-unstable-v0.2.0) (2026-06-22)


### Features

* **ethernetip:** reconnect after transient failures ([#100](https://github.com/nominal-io/instro/issues/100)) ([e9a35da](https://github.com/nominal-io/instro/commit/e9a35da96e619719cad008ee591d6be250343cbd))

## [0.1.0](https://github.com/nominal-io/instro/compare/instro-unstable-v0.0.1...instro-unstable-v0.1.0) (2026-06-17)


### Features

* **ethernetip:** batch reads ([#68](https://github.com/nominal-io/instro/issues/68)) ([0afb900](https://github.com/nominal-io/instro/commit/0afb900ad01c752397ecbf23ea1012370658fc77))
* **psu:** add ovp, ocp, and remote sense method signatures ([#31](https://github.com/nominal-io/instro/issues/31)) ([ecd4071](https://github.com/nominal-io/instro/commit/ecd40718ec00227deb2b619d5d2fea0f01ea15fd))
* **scope:** add hardware-validated Siglent SDS1104X-E driver ([#74](https://github.com/nominal-io/instro/issues/74)) ([b572a87](https://github.com/nominal-io/instro/commit/b572a876efc7b3d80e0b4eac18d4f8721fbd2171))


### Bug Fixes

* **lib:** bundle every pyvisa-py backend for clean-install VISA support ([#103](https://github.com/nominal-io/instro/issues/103)) ([60604dc](https://github.com/nominal-io/instro/commit/60604dc79de9802c5d40720d8d0265ba85e4cac0))
