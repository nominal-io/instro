# Changelog

## [0.7.1](https://github.com/nominal-io/instro/compare/instro-daq-ni-v0.7.0...instro-daq-ni-v0.7.1) (2026-06-17)


### Bug Fixes

* **daq:** guard NI fetch_analog against fetch before start() ([#116](https://github.com/nominal-io/instro/issues/116)) ([2e2f4bc](https://github.com/nominal-io/instro/commit/2e2f4bce22928e7ce1be04741e5995c79b6bf50f))
* **lib:** bundle every pyvisa-py backend for clean-install VISA support ([#103](https://github.com/nominal-io/instro/issues/103)) ([60604dc](https://github.com/nominal-io/instro/commit/60604dc79de9802c5d40720d8d0265ba85e4cac0))

## [0.7.0](https://github.com/nominal-io/instro/compare/instro-daq-ni-v0.6.0...instro-daq-ni-v0.7.0) (2026-06-10)


### ⚠ BREAKING CHANGES

* **daq:** make driver channel/timing state private and read-only ([#56](https://github.com/nominal-io/instro/issues/56))

### Features

* **daq:** make driver channel/timing state private and read-only ([#56](https://github.com/nominal-io/instro/issues/56)) ([c34cf0d](https://github.com/nominal-io/instro/commit/c34cf0d9508db3d73db1c8f537fb41f6763a0616))

## [0.6.0](https://github.com/nominal-io/instro/compare/instro-daq-ni-v0.5.0...instro-daq-ni-v0.6.0) (2026-06-02)


### ⚠ BREAKING CHANGES

* **daq:** split digital line and port configuration into separate methods ([#36](https://github.com/nominal-io/instro/issues/36))
* **daq:** require fully-qualified NI physical channel names ([#41](https://github.com/nominal-io/instro/issues/41))
* **daq:** remove InstroDAQFacade; driver owns channel/timing state ([#19](https://github.com/nominal-io/instro/issues/19))

### Features

* **daq:** implement digital port read/write for NI and Keysight drivers ([#50](https://github.com/nominal-io/instro/issues/50)) ([3150ae0](https://github.com/nominal-io/instro/commit/3150ae09a84f6b75b5800b128309173b65ca667b))
* **daq:** require fully-qualified NI physical channel names ([#41](https://github.com/nominal-io/instro/issues/41)) ([a9dbdfd](https://github.com/nominal-io/instro/commit/a9dbdfd481dca8424da4e892d28986acce024d87))
* **psu:** add ovp, ocp, and remote sense method signatures ([#31](https://github.com/nominal-io/instro/issues/31)) ([ecd4071](https://github.com/nominal-io/instro/commit/ecd40718ec00227deb2b619d5d2fea0f01ea15fd))


### Miscellaneous

* **daq:** remove InstroDAQFacade; driver owns channel/timing state ([#19](https://github.com/nominal-io/instro/issues/19)) ([cd43847](https://github.com/nominal-io/instro/commit/cd43847904a492b83cc3c2f8da97e356a06e9435))
* **daq:** split digital line and port configuration into separate methods ([#36](https://github.com/nominal-io/instro/issues/36)) ([52c8c44](https://github.com/nominal-io/instro/commit/52c8c44e2981aae9610606309c411a9b44c4094c))

## [0.5.0](https://github.com/nominal-io/instro/compare/instro-daq-ni-v0.4.0...instro-daq-ni-v0.5.0) (2026-05-27)


### Features

* add PyPI project URLs ([#18](https://github.com/nominal-io/instro/issues/18)) ([5ffe6cf](https://github.com/nominal-io/instro/commit/5ffe6cfa8aec92504c9c4c2af91c33a5d7c3d26f))

## [0.4.0](https://github.com/nominal-io/instrumentation/compare/instro-daq-ni-v0.3.1...instro-daq-ni-v0.4.0) (2026-05-01)


### Features

* add mccdaq driver ([#82](https://github.com/nominal-io/instrumentation/issues/82)) ([c6acfee](https://github.com/nominal-io/instrumentation/commit/c6acfeed8a34d53cc83edf369b1c0eff23984187))

## [0.3.1](https://github.com/nominal-io/instrumentation/compare/instro-daq-ni-v0.3.0...instro-daq-ni-v0.3.1) (2026-03-20)


### Bug Fixes

* **daq:** fix DAQ data integrity due to poor timestamp algorithm ([#134](https://github.com/nominal-io/instrumentation/issues/134)) ([db67690](https://github.com/nominal-io/instrumentation/commit/db6769042b166faf6e9dc35feaf51ef275827f7f))

## [0.3.0](https://github.com/nominal-io/instrumentation/compare/v0.2.0...v0.3.0) (2025-12-19)


### Features

* add support for terminal config in InstroDAQ ([#62](https://github.com/nominal-io/instrumentation/issues/62)) ([f92a712](https://github.com/nominal-io/instrumentation/commit/f92a71217cc0ddc01012d8ee0181b4b030df9c75))
* software timed analog output for daq ([#72](https://github.com/nominal-io/instrumentation/issues/72)) ([1a5f443](https://github.com/nominal-io/instrumentation/commit/1a5f443de9d8b58bd011972da6ab671309469331))

## [0.2.0](https://github.com/nominal-io/instrumentation/compare/v0.1.0...v0.2.0) (2025-11-05)


### Features

* interface for drivers to HAL ([#48](https://github.com/nominal-io/instrumentation/issues/48)) ([02ecafe](https://github.com/nominal-io/instrumentation/commit/02ecafe0c0ab1880946bdd0b7a582958172bf925))
* unifying the instrument, driver, data_handler and factory patterns across HALs ([#45](https://github.com/nominal-io/instrumentation/issues/45)) ([a8c720f](https://github.com/nominal-io/instrumentation/commit/a8c720f4b554239c7cb8ac23da1668920600bf8c))


### Bug Fixes

* docstrings ([#51](https://github.com/nominal-io/instrumentation/issues/51)) ([5427abc](https://github.com/nominal-io/instrumentation/commit/5427abcc1619dd0208e7d246997b5a7379234f8c))
