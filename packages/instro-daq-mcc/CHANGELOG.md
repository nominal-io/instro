# Changelog

## [0.5.1](https://github.com/nominal-io/instro/compare/instro-daq-mcc-v0.5.0...instro-daq-mcc-v0.5.1) (2026-06-17)


### Bug Fixes

* **daq:** mcc fetch path integrity fix ([#113](https://github.com/nominal-io/instro/issues/113)) ([aa68289](https://github.com/nominal-io/instro/commit/aa682895959804b506efa99a3b1571d757f050e3))
* **lib:** bundle every pyvisa-py backend for clean-install VISA support ([#103](https://github.com/nominal-io/instro/issues/103)) ([60604dc](https://github.com/nominal-io/instro/commit/60604dc79de9802c5d40720d8d0265ba85e4cac0))

## [0.5.0](https://github.com/nominal-io/instro/compare/instro-daq-mcc-v0.4.0...instro-daq-mcc-v0.5.0) (2026-06-10)


### ⚠ BREAKING CHANGES

* **daq:** make driver channel/timing state private and read-only ([#56](https://github.com/nominal-io/instro/issues/56))

### Features

* **daq:** make driver channel/timing state private and read-only ([#56](https://github.com/nominal-io/instro/issues/56)) ([c34cf0d](https://github.com/nominal-io/instro/commit/c34cf0d9508db3d73db1c8f537fb41f6763a0616))

## [0.4.0](https://github.com/nominal-io/instro/compare/instro-daq-mcc-v0.3.0...instro-daq-mcc-v0.4.0) (2026-06-02)


### ⚠ BREAKING CHANGES

* **daq:** split digital line and port configuration into separate methods ([#36](https://github.com/nominal-io/instro/issues/36))
* **daq:** remove InstroDAQFacade; driver owns channel/timing state ([#19](https://github.com/nominal-io/instro/issues/19))

### Features

* **psu:** add ovp, ocp, and remote sense method signatures ([#31](https://github.com/nominal-io/instro/issues/31)) ([ecd4071](https://github.com/nominal-io/instro/commit/ecd40718ec00227deb2b619d5d2fea0f01ea15fd))


### Miscellaneous

* **daq:** remove InstroDAQFacade; driver owns channel/timing state ([#19](https://github.com/nominal-io/instro/issues/19)) ([cd43847](https://github.com/nominal-io/instro/commit/cd43847904a492b83cc3c2f8da97e356a06e9435))
* **daq:** split digital line and port configuration into separate methods ([#36](https://github.com/nominal-io/instro/issues/36)) ([52c8c44](https://github.com/nominal-io/instro/commit/52c8c44e2981aae9610606309c411a9b44c4094c))

## [0.3.0](https://github.com/nominal-io/instro/compare/instro-daq-mcc-v0.2.0...instro-daq-mcc-v0.3.0) (2026-05-27)


### Features

* add PyPI project URLs ([#18](https://github.com/nominal-io/instro/issues/18)) ([5ffe6cf](https://github.com/nominal-io/instro/commit/5ffe6cfa8aec92504c9c4c2af91c33a5d7c3d26f))

## [0.2.0](https://github.com/nominal-io/instrumentation/compare/instro-daq-mcc-v0.1.0...instro-daq-mcc-v0.2.0) (2026-05-01)


### Features

* add mccdaq driver ([#82](https://github.com/nominal-io/instrumentation/issues/82)) ([c6acfee](https://github.com/nominal-io/instrumentation/commit/c6acfeed8a34d53cc83edf369b1c0eff23984187))
