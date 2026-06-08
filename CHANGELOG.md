# Changelog

## [0.8.0](https://github.com/nominal-io/instro/compare/instro-v0.7.0...instro-v0.8.0) (2026-06-02)


### ⚠ BREAKING CHANGES

* **daq:** split digital line and port configuration into separate methods ([#36](https://github.com/nominal-io/instro/issues/36))
* **daq:** require fully-qualified NI physical channel names ([#41](https://github.com/nominal-io/instro/issues/41))
* **daq:** remove InstroDAQFacade; driver owns channel/timing state ([#19](https://github.com/nominal-io/instro/issues/19))

### Features

* **daq:** implement digital port read/write for NI and Keysight drivers ([#50](https://github.com/nominal-io/instro/issues/50)) ([3150ae0](https://github.com/nominal-io/instro/commit/3150ae09a84f6b75b5800b128309173b65ca667b))
* **daq:** require fully-qualified NI physical channel names ([#41](https://github.com/nominal-io/instro/issues/41)) ([a9dbdfd](https://github.com/nominal-io/instro/commit/a9dbdfd481dca8424da4e892d28986acce024d87))
* **psu:** add ovp, ocp, and remote sense method signatures ([#31](https://github.com/nominal-io/instro/issues/31)) ([ecd4071](https://github.com/nominal-io/instro/commit/ecd40718ec00227deb2b619d5d2fea0f01ea15fd))


### Bug Fixes

* **psu:** expect "+0" from TDK Lambda SYST:ERR? response ([#54](https://github.com/nominal-io/instro/issues/54)) ([5f9e4a9](https://github.com/nominal-io/instro/commit/5f9e4a942c46b63895a218826eb5df46b4b91a59))
* **tests:** add tests/__init__.py so pyaardvark's bundled tests package stops shadowing ([#44](https://github.com/nominal-io/instro/issues/44)) ([d755f95](https://github.com/nominal-io/instro/commit/d755f95fcdf906fcb89cd41d1888a49ada2d45c1))


### Miscellaneous

* **daq:** remove InstroDAQFacade; driver owns channel/timing state ([#19](https://github.com/nominal-io/instro/issues/19)) ([cd43847](https://github.com/nominal-io/instro/commit/cd43847904a492b83cc3c2f8da97e356a06e9435))
* **daq:** split digital line and port configuration into separate methods ([#36](https://github.com/nominal-io/instro/issues/36)) ([52c8c44](https://github.com/nominal-io/instro/commit/52c8c44e2981aae9610606309c411a9b44c4094c))

## [0.7.0](https://github.com/nominal-io/instro/compare/instro-v0.6.0...instro-v0.7.0) (2026-05-27)


### Features

* add PyPI project URLs ([#18](https://github.com/nominal-io/instro/issues/18)) ([5ffe6cf](https://github.com/nominal-io/instro/commit/5ffe6cfa8aec92504c9c4c2af91c33a5d7c3d26f))

## [0.6.0](https://github.com/nominal-io/instrumentation/compare/instro-v0.5.2...instro-v0.6.0) (2026-05-01)


### Features

* add CD workflow to publish instro-unstable to GemFury ([#167](https://github.com/nominal-io/instrumentation/issues/167)) ([b210ccd](https://github.com/nominal-io/instrumentation/commit/b210ccd4a1068725d506e8f3822aaf2f8384c87a))
* add core logging capabilities ([#148](https://github.com/nominal-io/instrumentation/issues/148)) ([98c7c05](https://github.com/nominal-io/instrumentation/commit/98c7c050dd1f6672e5e408ce08892f8d471467b1))
* add experimental workspace package for in-development features ([#154](https://github.com/nominal-io/instrumentation/issues/154)) ([e1a4b82](https://github.com/nominal-io/instrumentation/commit/e1a4b8260155cd6ca9d77b6c90ecdc3d7638e0a0))
* add mccdaq driver ([#82](https://github.com/nominal-io/instrumentation/issues/82)) ([c6acfee](https://github.com/nominal-io/instrumentation/commit/c6acfeed8a34d53cc83edf369b1c0eff23984187))
* add instro-unstable package alongside experimental ([#165](https://github.com/nominal-io/instrumentation/issues/165)) ([e57c218](https://github.com/nominal-io/instrumentation/commit/e57c21880c5f846d9c033f0e16835dd31c41f259))
* **ethernetip:** add experimental rust bindings ([#164](https://github.com/nominal-io/instrumentation/issues/164)) ([6ec03b0](https://github.com/nominal-io/instrumentation/commit/6ec03b02e8866e853db8f0d1ee362ef963721bec))
* **modbus:** add config types foundation for InstroModbus ([#155](https://github.com/nominal-io/instrumentation/issues/155)) ([0aab181](https://github.com/nominal-io/instrumentation/commit/0aab1814a1f48886ad39b3f1de938833e5c94c60))
* **modbus:** add config validation for swaps, scale, and overlaps ([#157](https://github.com/nominal-io/instrumentation/issues/157)) ([2c5d86b](https://github.com/nominal-io/instrumentation/commit/2c5d86b9fff50fda8582578e55af8c1c6931f382))
* **modbus:** add InstroModbus core read/write ([#156](https://github.com/nominal-io/instrumentation/issues/156)) ([97a18f7](https://github.com/nominal-io/instrumentation/commit/97a18f7fef59f18bf3d87dcaaf539fd026fe7347))
* **modbus:** add read groups, bitmap extraction, background polling ([#159](https://github.com/nominal-io/instrumentation/issues/159)) ([8ddc7a0](https://github.com/nominal-io/instrumentation/commit/8ddc7a0106d17bbef24d54bc1aff6ded5dd21420))
* **modbus:** add write safety — value maps, limits, type checking ([#158](https://github.com/nominal-io/instrumentation/issues/158)) ([1cf0301](https://github.com/nominal-io/instrumentation/commit/1cf03015ac8052d45063e8374976de70aadfc09c))
* **scope:** add Keysight 1200X oscilloscope driver ([#175](https://github.com/nominal-io/instrumentation/issues/175)) ([34bfb2e](https://github.com/nominal-io/instrumentation/commit/34bfb2ec3375e8de22b3825669cadebacd7ab3a1))
* **scope:** add InstroScope instrument class ([#172](https://github.com/nominal-io/instrumentation/issues/172)) ([5e21937](https://github.com/nominal-io/instrumentation/commit/5e21937499e1522d2a8406b4b53b3f25a5d57646))
* **scope:** add oscilloscope types and driver base classes ([#171](https://github.com/nominal-io/instrumentation/issues/171)) ([ce666f2](https://github.com/nominal-io/instrumentation/commit/ce666f2d0d83d749779a07900cf3fdce289d3c05))
* **scope:** add scope public API and driver discovery ([#173](https://github.com/nominal-io/instrumentation/issues/173)) ([502b259](https://github.com/nominal-io/instrumentation/commit/502b2598f4f327aee5ba43f872c4b670b701be5a))
* **scope:** add Tektronix 2 Series MSO driver ([#174](https://github.com/nominal-io/instrumentation/issues/174)) ([855a61e](https://github.com/nominal-io/instrumentation/commit/855a61ea12a3f21591be86d1c2e75be03af64719))
* use absolute timestamps for fetch waveform data ([#187](https://github.com/nominal-io/instrumentation/issues/187)) ([ddced95](https://github.com/nominal-io/instrumentation/commit/ddced95e36a46387e0fbd31acdd8828424720809))


### Bug Fixes

* add actions read permission ([#153](https://github.com/nominal-io/instrumentation/issues/153)) ([dc696eb](https://github.com/nominal-io/instrumentation/commit/dc696eb150129243021d8af3110cf32d25ee373f))
* **dmm:** don't start reading from DMM if not fully configured ([#145](https://github.com/nominal-io/instrumentation/issues/145)) ([e56db93](https://github.com/nominal-io/instrumentation/commit/e56db933a25a05f7d502a9914953dba132e7fb61))

## [0.5.2](https://github.com/nominal-io/instrumentation/compare/instro-v0.5.1...instro-v0.5.2) (2026-03-20)


### Bug Fixes

* **daq:** fix DAQ data integrity due to poor timestamp algorithm ([#134](https://github.com/nominal-io/instrumentation/issues/134)) ([db67690](https://github.com/nominal-io/instrumentation/commit/db6769042b166faf6e9dc35feaf51ef275827f7f))

## [0.5.1](https://github.com/nominal-io/instrumentation/compare/v0.5.0...v0.5.1) (2026-02-23)


### Features

* adding relay for the keysight_34980a ([#114](https://github.com/nominal-io/instrumentation/issues/114)) ([0f25773](https://github.com/nominal-io/instrumentation/commit/0f257737322407ece9bb785fb3ad47b061fce2df))
* reduce NominalConnectPublisher import path ([#120](https://github.com/nominal-io/instrumentation/issues/120)) ([d6ca816](https://github.com/nominal-io/instrumentation/commit/d6ca81665eb87a1b8533367cd6331dde576b2605))


### Bug Fixes

* **dmm:** fix InstroDMM daemon not starting ([#93](https://github.com/nominal-io/instrumentation/issues/93)) ([51cadee](https://github.com/nominal-io/instrumentation/commit/51cadee1316df080fdd432a4300369ee06527f93))

* **daq:** raise exceptions with verbose messages instead of failing silently, including for unconfigured channels in write_digital_line() ([#118](https://github.com/nominal-io/instrumentation/issues/118)) ([ce6cf81](https://github.com/nominal-io/instrumentation/commit/ce6cf81f63d47fbad23b5bd47e4aa6bba09f17fd))
* **daq:** support multiple channels with same physical channel ([#87](https://github.com/nominal-io/instrumentation/issues/87)) ([aa069b6](https://github.com/nominal-io/instrumentation/commit/aa069b6c4135d5e1fffe92f2f734cb14886d320c))
* **keysight:** correct logic polarity comparison in digital channel config ([#116](https://github.com/nominal-io/instrumentation/issues/116)) ([976800b](https://github.com/nominal-io/instrumentation/commit/976800b879e7946b63efaf14ff661f38e162a7a6))

## [0.5.0](https://github.com/nominal-io/instrumentation/compare/v0.4.0...v0.5.0) (2026-02-04)


### Features

* add InstroDMM ([#88](https://github.com/nominal-io/instrumentation/issues/88)) ([ae04ad8](https://github.com/nominal-io/instrumentation/commit/ae04ad8893380e39f9052049e6f0568abc2d03d4))

## [0.4.0](https://github.com/nominal-io/instrumentation/compare/v0.3.0...v0.4.0) (2025-12-19)


### Features

* Handle bad network conditions by writing to fallback file ([#66](https://github.com/nominal-io/instrumentation/issues/66)) ([b7c3e4c](https://github.com/nominal-io/instrumentation/commit/b7c3e4c05ef1a89d374d5cefb85fc4cba11acddd))
* add .avro file logging capabilities ([#70](https://github.com/nominal-io/instrumentation/issues/70)) ([7b0c9a9](https://github.com/nominal-io/instrumentation/commit/7b0c9a971a8c9745d47123e281bfc067174862a2))
* add package name and version tag to measurements/commands([#74](https://github.com/nominal-io/instrumentation/issues/74)) ([d2e018a](https://github.com/nominal-io/instrumentation/commit/d2e018ae17b4603b992ac9ee48e6d625ba53f000))
* add support for terminal config in InstroDAQ ([#62](https://github.com/nominal-io/instrumentation/issues/62)) ([f92a712](https://github.com/nominal-io/instrumentation/commit/f92a71217cc0ddc01012d8ee0181b4b030df9c75))
* add configuration selection for visa backends ([#63](https://github.com/nominal-io/instrumentation/issues/63)) ([ab3051e](https://github.com/nominal-io/instrumentation/commit/ab3051e2cb790caacf6aa25d55578d0a8dc5ad52))
* data access from main thread to background daemons ([#68](https://github.com/nominal-io/instrumentation/issues/68)) ([0a04d6f](https://github.com/nominal-io/instrumentation/commit/0a04d6ff36d16bb6f1cc4a796bcb47ecdb53665e))
* software timed analog output for daq ([#72](https://github.com/nominal-io/instrumentation/issues/72)) ([1a5f443](https://github.com/nominal-io/instrumentation/commit/1a5f443de9d8b58bd011972da6ab671309469331))


### Bug Fixes

* labjack STREAM_ACTIVE and segfault ([#77](https://github.com/nominal-io/instrumentation/issues/77)) ([c9b7551](https://github.com/nominal-io/instrumentation/commit/c9b75515a6e223e6d00dca4470c9242aa4340b39))
* prevent nominal core publisher from raising rust runtime error on exit ([#76](https://github.com/nominal-io/instrumentation/issues/76)) ([510decb](https://github.com/nominal-io/instrumentation/commit/510decbed60aae5bcb047db81faca309016ab45b))
* remove analog input channels to avoid multiple channels with same physical channel ([#65](https://github.com/nominal-io/instrumentation/issues/65)) ([5d5ed50](https://github.com/nominal-io/instrumentation/commit/5d5ed501761dc80cad77c1b24dd05e4562e3f80a))
* nominaldaq loop rate lies when background enable is false ([#75](https://github.com/nominal-io/instrumentation/issues/75)) ([ca48db6](https://github.com/nominal-io/instrumentation/commit/ca48db694764b701e3fcdab295c92337ad721b57))

### Documentation

* add example and reference documentation ([#56](https://github.com/nominal-io/instrumentation/issues/56)) ([6f7d307](https://github.com/nominal-io/instrumentation/commit/6f7d3073eb42c2ecf5cf60c953153ddda3a7d82c))

## [0.3.0](https://github.com/nominal-io/instrumentation/compare/v0.2.0...v0.3.0) (2025-11-20)


### Features

* daq scaling support ([#57](https://github.com/nominal-io/instrumentation/issues/57)) ([9f78251](https://github.com/nominal-io/instrumentation/commit/9f78251b3f7aa4e76f40b824beab210d442ff366))

## [0.2.0](https://github.com/nominal-io/instrumentation/compare/v0.1.0...v0.2.0) (2025-11-05)


### Features

* i2c ([#33](https://github.com/nominal-io/instrumentation/issues/33)) ([d61c69f](https://github.com/nominal-io/instrumentation/commit/d61c69feb13d46fbef33a8c8f43c2e64863c80cc))
* interface for drivers to HAL ([#48](https://github.com/nominal-io/instrumentation/issues/48)) ([02ecafe](https://github.com/nominal-io/instrumentation/commit/02ecafe0c0ab1880946bdd0b7a582958172bf925))
* public abilities to define background daemon functions ([#50](https://github.com/nominal-io/instrumentation/issues/50)) ([36a155d](https://github.com/nominal-io/instrumentation/commit/36a155d75d350f59ed6b1b62af55306c03ef0e60))
* unifying the instrument, driver, data_handler and factory patterns across HALs ([#45](https://github.com/nominal-io/instrumentation/issues/45)) ([a8c720f](https://github.com/nominal-io/instrumentation/commit/a8c720f4b554239c7cb8ac23da1668920600bf8c))


### Bug Fixes

* docstrings ([#51](https://github.com/nominal-io/instrumentation/issues/51)) ([5427abc](https://github.com/nominal-io/instrumentation/commit/5427abcc1619dd0208e7d246997b5a7379234f8c))

## 0.1.0 (2025-10-15)


### Features

* add just build command ([#37](https://github.com/nominal-io/instrumentation/issues/37)) ([1a7454e](https://github.com/nominal-io/instrumentation/commit/1a7454ef040f628e5708fbecb7aede3a212b2f74))
* allow IDN match for "BK PRECISION" ([#34](https://github.com/nominal-io/instrumentation/issues/34)) ([0632d53](https://github.com/nominal-io/instrumentation/commit/0632d5305a4b46dc990324060ad96a630d8bf912))
* overhaul devx ([89b95a5](https://github.com/nominal-io/instrumentation/commit/89b95a51ec1f760d6cbfc35839363d905ffa9efe))
* packaging restructure for extensibility ([#35](https://github.com/nominal-io/instrumentation/issues/35)) ([0027400](https://github.com/nominal-io/instrumentation/commit/0027400cc9e8efaaaab76da00253aee7cb497af4))
* poetry -&gt; uv ([4ac7738](https://github.com/nominal-io/instrumentation/commit/4ac773835fe948c9b82ba77e456ebcb0da100438))
* psu registry/discovery without factory ([#23](https://github.com/nominal-io/instrumentation/issues/23)) ([e91c383](https://github.com/nominal-io/instrumentation/commit/e91c38364c4e80a408a5eee8c6997e73e7728b79))


### Bug Fixes

* documentation & typing cleanup ([#26](https://github.com/nominal-io/instrumentation/issues/26)) ([4a77f1e](https://github.com/nominal-io/instrumentation/commit/4a77f1e14c817ae480ca1f6d7175510fb173d7c6))
* rigol caps ([#28](https://github.com/nominal-io/instrumentation/issues/28)) ([3674f6a](https://github.com/nominal-io/instrumentation/commit/3674f6a843cb9d81ecb34f1e1db0477b1922bd41))
* small typos ([cc9ee8f](https://github.com/nominal-io/instrumentation/commit/cc9ee8f205b5ca58cd5635ed1d5045695d10fe19))
* small typos ([fea0ab5](https://github.com/nominal-io/instrumentation/commit/fea0ab565c7d9f5603b07d00d6474cc6f0aae459))
* sort imports ([#27](https://github.com/nominal-io/instrumentation/issues/27)) ([df21199](https://github.com/nominal-io/instrumentation/commit/df2119925997055c8430d9b8caced04f44170e3d))
* use global logger in scpi sim ([8eda021](https://github.com/nominal-io/instrumentation/commit/8eda021d24286bb3a1244274d7f9bab7cb3d4f52))
* use global logger in scpi sim ([ad8f96f](https://github.com/nominal-io/instrumentation/commit/ad8f96f8c99828c46b8f2d7d577b7fc81575b030))
