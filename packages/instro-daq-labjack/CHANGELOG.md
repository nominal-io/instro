# Changelog

## [0.4.0](https://github.com/nominal-io/instrumentation/compare/instro-daq-labjack-v0.3.2...instro-daq-labjack-v0.4.0) (2026-05-01)


### Features

* add mccdaq driver ([#82](https://github.com/nominal-io/instrumentation/issues/82)) ([c6acfee](https://github.com/nominal-io/instrumentation/commit/c6acfeed8a34d53cc83edf369b1c0eff23984187))

## [0.3.2](https://github.com/nominal-io/instrumentation/compare/instro-daq-labjack-v0.3.1...instro-daq-labjack-v0.3.2) (2026-03-20)


### Bug Fixes

* **daq:** fix DAQ data integrity due to poor timestamp algorithm ([#134](https://github.com/nominal-io/instrumentation/issues/134)) ([db67690](https://github.com/nominal-io/instrumentation/commit/db6769042b166faf6e9dc35feaf51ef275827f7f))

## [0.3.1](https://github.com/nominal-io/instrumentation/compare/v0.3.0...v0.3.1) (2026-02-23)


### Bug Fixes

* make t8 read_analog simultaneous ([#119](https://github.com/nominal-io/instrumentation/issues/119)) ([6ad913c](https://github.com/nominal-io/instrumentation/commit/6ad913c3dfc7cd42da13c29eb42082efbd63c8b6))
* allow non-USB LabJack connections via ctANY ([#115](https://github.com/nominal-io/instrumentation/issues/115)) ([461f665](https://github.com/nominal-io/instrumentation/commit/461f6657af223c7d0abba0d248fcec9ceb7fd1fd))

## [0.3.0](https://github.com/nominal-io/instrumentation/compare/v0.2.0...v0.3.0) (2025-12-19)


### Features

* add support for terminal config in InstroDAQ ([#62](https://github.com/nominal-io/instrumentation/issues/62)) ([f92a712](https://github.com/nominal-io/instrumentation/commit/f92a71217cc0ddc01012d8ee0181b4b030df9c75))
* software timed analog output for daq ([#72](https://github.com/nominal-io/instrumentation/issues/72)) ([1a5f443](https://github.com/nominal-io/instrumentation/commit/1a5f443de9d8b58bd011972da6ab671309469331))


### Bug Fixes

* labjack STREAM_ACTIVE and segfault ([#77](https://github.com/nominal-io/instrumentation/issues/77)) ([c9b7551](https://github.com/nominal-io/instrumentation/commit/c9b75515a6e223e6d00dca4470c9242aa4340b39))

## [0.2.0](https://github.com/nominal-io/instrumentation/compare/v0.1.0...v0.2.0) (2025-11-05)


### Features

* interface for drivers to HAL ([#48](https://github.com/nominal-io/instrumentation/issues/48)) ([02ecafe](https://github.com/nominal-io/instrumentation/commit/02ecafe0c0ab1880946bdd0b7a582958172bf925))
* unifying the instrument, driver, data_handler and factory patterns across HALs ([#45](https://github.com/nominal-io/instrumentation/issues/45)) ([a8c720f](https://github.com/nominal-io/instrumentation/commit/a8c720f4b554239c7cb8ac23da1668920600bf8c))


### Bug Fixes

* docstrings ([#51](https://github.com/nominal-io/instrumentation/issues/51)) ([5427abc](https://github.com/nominal-io/instrumentation/commit/5427abcc1619dd0208e7d246997b5a7379234f8c))
