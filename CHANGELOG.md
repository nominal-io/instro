# Changelog

## [0.13.0](https://github.com/nominal-io/instro/compare/instro-v0.12.1...instro-v0.13.0) (2026-06-25)


### ⚠ BREAKING CHANGES

* 149 bug documentation for get channel does not match behavior ([#151](https://github.com/nominal-io/instro/issues/151))

### Features

* **cli:** add instro discover command ([#99](https://github.com/nominal-io/instro/issues/99)) ([699648c](https://github.com/nominal-io/instro/commit/699648cf66e87f3d3c7c1122d1004c89fffb43c2))
* **instrument:** auto-prepend instrument name in get_channel ([#152](https://github.com/nominal-io/instro/issues/152)) ([e807209](https://github.com/nominal-io/instro/commit/e807209caccc5d28e55be69169c68f58db22984c)), closes [#147](https://github.com/nominal-io/instro/issues/147)


### Bug Fixes

* 149 bug documentation for get channel does not match behavior ([#151](https://github.com/nominal-io/instro/issues/151)) ([f4f3966](https://github.com/nominal-io/instro/commit/f4f396686fab9b437143730954d0638d28dbbb6d))
* closes [#149](https://github.com/nominal-io/instro/issues/149). First, updates documentation about timeout behavior. ([f4f3966](https://github.com/nominal-io/instro/commit/f4f396686fab9b437143730954d0638d28dbbb6d))
* **transports:** disable Nagle on raw TCP sockets for pyvisa-py ([#157](https://github.com/nominal-io/instro/issues/157)) ([e1ce26a](https://github.com/nominal-io/instro/commit/e1ce26a01384d940d8e7dda84c54f293311cf72d))

## [0.12.1](https://github.com/nominal-io/instro/compare/instro-v0.12.0...instro-v0.12.1) (2026-06-24)


### Bug Fixes

* **dmm:** apply Agilent34401A manual range without requiring set_digits ([#148](https://github.com/nominal-io/instro/issues/148)) ([563aabb](https://github.com/nominal-io/instro/commit/563aabbd366e60ceafc63e44a35c3aab588d3170))
* **dmm:** record measurement function only after driver accepts it ([#146](https://github.com/nominal-io/instro/issues/146)) ([d720cf1](https://github.com/nominal-io/instro/commit/d720cf146aa2f7edb52c58a64a6ac7d67247c67b))
* **ethernetip:** disable cpppo UDP in simulator tests ([#137](https://github.com/nominal-io/instro/issues/137)) ([4048fbf](https://github.com/nominal-io/instro/commit/4048fbf42889332b0fe8ebe4d75a6ca7445887c9))
* **ethernetip:** remove strings from Python EIP surface ([#128](https://github.com/nominal-io/instro/issues/128)) ([c133d81](https://github.com/nominal-io/instro/commit/c133d814a66b68775fb5a05114cf1ccac22aa466))
* **visa:** catch pyvisa errors so [@ivi](https://github.com/ivi) to [@py](https://github.com/py) fallback fires correctly ([#136](https://github.com/nominal-io/instro/issues/136)) ([9329df6](https://github.com/nominal-io/instro/commit/9329df6f48d92f196ce205a0e07f9bf0f3df21d0))

## [0.12.0](https://github.com/nominal-io/instro/compare/instro-v0.11.0...instro-v0.12.0) (2026-06-22)


### Features

* **ethernetip:** reconnect after transient failures ([#100](https://github.com/nominal-io/instro/issues/100)) ([e9a35da](https://github.com/nominal-io/instro/commit/e9a35da96e619719cad008ee591d6be250343cbd))
* **visa:** default to [@ivi](https://github.com/ivi) backend, fall back to [@py](https://github.com/py) ([#130](https://github.com/nominal-io/instro/issues/130)) ([61a2166](https://github.com/nominal-io/instro/commit/61a21664053cb5d81e1fee0df03817bfe83f056a))

## [0.11.0](https://github.com/nominal-io/instro/compare/instro-v0.10.0...instro-v0.11.0) (2026-06-17)


### ⚠ BREAKING CHANGES

* **psu:** update bk 914X with OVP, OCP, and remote sense ([#93](https://github.com/nominal-io/instro/issues/93))
* **psu:** remove default channel selection from psu drivers ([#106](https://github.com/nominal-io/instro/issues/106))

### Features

* **psu:** add OVP, OCP, remote sense to bk 9115 ([#94](https://github.com/nominal-io/instro/issues/94)) ([783f753](https://github.com/nominal-io/instro/commit/783f753501c541a9b6caa4952371d6d194ae573f))
* **psu:** add Rigol DP800 ovp, ocp, and remote sense ([#114](https://github.com/nominal-io/instro/issues/114)) ([d1f1587](https://github.com/nominal-io/instro/commit/d1f15875f91f896e1cecf86a520ba68073b5ba50))
* **psu:** update bk 914X with OVP, OCP, and remote sense ([#93](https://github.com/nominal-io/instro/issues/93)) ([df0e28f](https://github.com/nominal-io/instro/commit/df0e28f84aa8ad88f58b0a9e02c8e3f7efe4bcaf))
* **psu:** update tdk genesys with ovp, ocp, remote sense ([#104](https://github.com/nominal-io/instro/issues/104)) ([1bd22a0](https://github.com/nominal-io/instro/commit/1bd22a0d7e8eb9083971c13bb8d1d05b723839da))


### Bug Fixes

* **build:** resolve Git Bash for just recipes on Windows ([#110](https://github.com/nominal-io/instro/issues/110)) ([8c8ae6b](https://github.com/nominal-io/instro/commit/8c8ae6b91659555d8b07447cff486b3f9f7d04c9))
* **daq:** guard NI fetch_analog against fetch before start() ([#116](https://github.com/nominal-io/instro/issues/116)) ([2e2f4bc](https://github.com/nominal-io/instro/commit/2e2f4bce22928e7ce1be04741e5995c79b6bf50f))
* **daq:** make close() tolerant of double-close and failed open ([#108](https://github.com/nominal-io/instro/issues/108)) ([5474c98](https://github.com/nominal-io/instro/commit/5474c982044106fd91f9b000fb9bdcef6812c4a7))
* **daq:** mcc fetch path integrity fix ([#113](https://github.com/nominal-io/instro/issues/113)) ([aa68289](https://github.com/nominal-io/instro/commit/aa682895959804b506efa99a3b1571d757f050e3))
* **daq:** raise clear error when DAQ methods are called before open() ([#97](https://github.com/nominal-io/instro/issues/97)) ([9f9ed5d](https://github.com/nominal-io/instro/commit/9f9ed5dd496f632440d3d71e225350bd544b571a))
* **lib:** bundle every pyvisa-py backend for clean-install VISA support ([#103](https://github.com/nominal-io/instro/issues/103)) ([60604dc](https://github.com/nominal-io/instro/commit/60604dc79de9802c5d40720d8d0265ba85e4cac0))
* **psu:** remove default channel selection from psu drivers ([#106](https://github.com/nominal-io/instro/issues/106)) ([45c6423](https://github.com/nominal-io/instro/commit/45c6423f8b86cdb7f2c5dad165a6d18b8d6c1a00))

## [0.10.0](https://github.com/nominal-io/instro/compare/instro-v0.9.0...instro-v0.10.0) (2026-06-12)


### ⚠ BREAKING CHANGES

* **psu:** support simulated protection and sense ([#47](https://github.com/nominal-io/instro/issues/47))
* **psu:** add tui for sim and refactor command set for ovp ocp and remote sense ([#30](https://github.com/nominal-io/instro/issues/30))

### Features

* **ethernetip:** batch reads ([#68](https://github.com/nominal-io/instro/issues/68)) ([0afb900](https://github.com/nominal-io/instro/commit/0afb900ad01c752397ecbf23ea1012370658fc77))
* **psu:** add tui for sim and refactor command set for ovp ocp and remote sense ([#30](https://github.com/nominal-io/instro/issues/30)) ([16f89b2](https://github.com/nominal-io/instro/commit/16f89b25b0f6086fb3c988a13a9081bade5dae45))
* **psu:** support simulated protection and sense ([#47](https://github.com/nominal-io/instro/issues/47)) ([0ad4cf0](https://github.com/nominal-io/instro/commit/0ad4cf0baef6d9d994e9303668e0f5bc97d3941f))


### Bug Fixes

* **test:** bind ephemeral port in simulated PSU socket test ([#98](https://github.com/nominal-io/instro/issues/98)) ([0658721](https://github.com/nominal-io/instro/commit/06587213f5d2cd1ae7f5505d54a9d8e01b3168ff))

## [0.9.0](https://github.com/nominal-io/instro/compare/instro-v0.8.0...instro-v0.9.0) (2026-06-10)


### ⚠ BREAKING CHANGES

* **daq:** decouple acquisition start from the daemon thread ([#62](https://github.com/nominal-io/instro/issues/62))
* **daq:** make driver channel/timing state private and read-only ([#56](https://github.com/nominal-io/instro/issues/56))

### Features

* **daq:** make driver channel/timing state private and read-only ([#56](https://github.com/nominal-io/instro/issues/56)) ([c34cf0d](https://github.com/nominal-io/instro/commit/c34cf0d9508db3d73db1c8f537fb41f6763a0616))
* **psu:** add Keysight E36100 support ([#48](https://github.com/nominal-io/instro/issues/48)) ([417035e](https://github.com/nominal-io/instro/commit/417035eb3b688b4058d42ea3a46f86c90acbbf66))
* **psu:** update Siglent SPD3303X driver and tests ([#55](https://github.com/nominal-io/instro/issues/55)) ([a07f0f3](https://github.com/nominal-io/instro/commit/a07f0f3515c863101db6059eb82a65d9c6bccfba))
* **scope:** add hardware-validated Siglent SDS1104X-E driver ([#74](https://github.com/nominal-io/instro/issues/74)) ([b572a87](https://github.com/nominal-io/instro/commit/b572a876efc7b3d80e0b4eac18d4f8721fbd2171))


### Bug Fixes

* **daq:** stop kwarg crash and samples_per_channel=0 below 10 Hz ([#83](https://github.com/nominal-io/instro/issues/83)) ([6539284](https://github.com/nominal-io/instro/commit/65392845794060a0edd0aafaac59035f6ea6b513))
* **dmm:** accept the Keithley 2400 signed +0 no-error reply ([#85](https://github.com/nominal-io/instro/issues/85)) ([2af65da](https://github.com/nominal-io/instro/commit/2af65dace4e7441d5dcc76421e768007b7e8be4c))
* **lib,daq:** make Instrument start/stop/start restartable ([#81](https://github.com/nominal-io/instro/issues/81)) ([eb60201](https://github.com/nominal-io/instro/commit/eb60201b7738e3158386bca13f6bd4f5e7f1afb8))
* **lib:** stop closing the shared pyvisa ResourceManager in VisaDriver ([#79](https://github.com/nominal-io/instro/issues/79)) ([1865d61](https://github.com/nominal-io/instro/commit/1865d6172ef8c5fc951bddb1dd13ca9e60ebc929))


### Miscellaneous

* **daq:** decouple acquisition start from the daemon thread ([#62](https://github.com/nominal-io/instro/issues/62)) ([52c9f8b](https://github.com/nominal-io/instro/commit/52c9f8b19d6b2220f1bf78201a026256cdd7956d))

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
