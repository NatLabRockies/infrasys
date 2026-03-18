# Changelog

## [1.1.0](https://github.com/NatLabRockies/infrasys/compare/v1.0.1...v1.1.0) (2026-03-18)


### Features

* Adding connection for supplemental attributes ([#134](https://github.com/NatLabRockies/infrasys/issues/134)) ([d3f7408](https://github.com/NatLabRockies/infrasys/commit/d3f74089729ebd2e050ba9288acdec431c047f3e))


### Bug Fixes

* handle sorting crash with mixed time series ([82645e8](https://github.com/NatLabRockies/infrasys/commit/82645e83b7c641532ef39651c4bd97e2c20cdacb))
* preserve all features in de-serialization ([5dc6da2](https://github.com/NatLabRockies/infrasys/commit/5dc6da2248b722e4abf2003c258a50aea5367b99))
* rendering of NonSequentialTimeSeries ([17fd366](https://github.com/NatLabRockies/infrasys/commit/17fd366278beef4700fdd98122a1e760109f9904))
* resolve time series serialization and rendering bugs ([8a190c5](https://github.com/NatLabRockies/infrasys/commit/8a190c5bc1eef3092175ed51f24731b83f6ce357))

## [1.0.1](https://github.com/NREL/infrasys/compare/v1.0.0...v1.0.1) (2025-12-18)


### Bug Fixes

* **h5:** compression settings where not being copied correctly. ([#130](https://github.com/NREL/infrasys/issues/130)) ([34dc36d](https://github.com/NREL/infrasys/commit/34dc36dfd1a0fd2be98c01ac9857a672a42a8d19))

## [1.0.0](https://github.com/NREL/infrasys/compare/v1.0.0...v1.0.0) (2025-12-09)


### ⚠ BREAKING CHANGES

* infrasys v1.0.0 ([#97](https://github.com/NREL/infrasys/issues/97))

### Features

* Add `round_trip=True` as a default json serialization. ([#65](https://github.com/NREL/infrasys/issues/65)) ([df0bd49](https://github.com/NREL/infrasys/commit/df0bd491ec1c8c11183f373fdfcaa7f1f2dba660))
* Add fuel_offtake representation from InfrastructureSystems ([c98f833](https://github.com/NREL/infrasys/commit/c98f833222e2c998974f3ac9993cb4590d1afe10))
* Add new enum `UnitSystem` and cleanup of value curves and cost functions ([#66](https://github.com/NREL/infrasys/issues/66)) ([6ff3368](https://github.com/NREL/infrasys/commit/6ff3368b7612a44992485dc73a8f89830c45de49))
* Add ValueCurves and cost functions from InfrastructureSystem.jl to infrasys ([#38](https://github.com/NREL/infrasys/issues/38)) ([db59ea2](https://github.com/NREL/infrasys/commit/db59ea2176415e9e91a052d89f771bceb3fc7235))
* Added `PydanticPintQuantity` as an option to enforce unit validation for fields ([#56](https://github.com/NREL/infrasys/issues/56)) ([7d7fbbf](https://github.com/NREL/infrasys/commit/7d7fbbf04953dfad7a7da665ff83d8e4897cd756))
* adding new abstract methods for storage conversion ([cbf3f8a](https://github.com/NREL/infrasys/commit/cbf3f8a15ef6f91810de6f25b857e7a726b5d818))
* Adding time_series_count for the system info ([#66](https://github.com/NREL/infrasys/issues/66)) ([7c48348](https://github.com/NREL/infrasys/commit/7c483481de9db84c1221368fee694892d8057900))
* Adding utility scripts to convert resolution as duration specified on the  iso 8601. ([#83](https://github.com/NREL/infrasys/issues/83)) ([4621171](https://github.com/NREL/infrasys/commit/46211710b24ee1c0159daa69594fd98d82b15ef2))
* Allow abstract types ([#81](https://github.com/NREL/infrasys/issues/81)) ([118eb00](https://github.com/NREL/infrasys/commit/118eb00a9ca55a76269225d5e1585fd6c5e520ff))
* **components-time-series:** Store time series metadata in SQLite ([f4e9ef8](https://github.com/NREL/infrasys/commit/f4e9ef81e1d4a193415bf05e709e46a16db27a89))
* **components:** Add deepcopy of component ([b19d82a](https://github.com/NREL/infrasys/commit/b19d82a2ca5a32a3c7cf3eb664fa1113f2920491))
* **components:** Add deepcopy of component ([5f7ab77](https://github.com/NREL/infrasys/commit/5f7ab77ec13c20ba66e8ab4cdc389023b253963b))
* **components:** Add function that returns dictionary representation of requested components. ([#67](https://github.com/NREL/infrasys/issues/67)) ([a6aaee1](https://github.com/NREL/infrasys/commit/a6aaee161e68787c37f9c6ddff0475e6f48e6deb))
* **components:** Add get_component_by_label ([ab33296](https://github.com/NREL/infrasys/commit/ab3329642a423a08b18b4245e8bb8f3ddaddcbcf))
* **components:** Add get_component_by_label ([e59d85a](https://github.com/NREL/infrasys/commit/e59d85ac9d895dd43aaeceadb7be844155f1919d))
* convert_storage methods in time_series_manager and system ([a7eae1b](https://github.com/NREL/infrasys/commit/a7eae1b64b76633bfc50c950685957fb5d3719a2))
* deserialize to in_memory ([d0841d5](https://github.com/NREL/infrasys/commit/d0841d50e5fc709e7130922e527c34b7e65b4439))
* **function_data:** Add function_data classes from InfrastructureSystems ([#33](https://github.com/NREL/infrasys/issues/33)) ([d17754e](https://github.com/NREL/infrasys/commit/d17754e7a10ec8ccaf5b84a8cdb7c1683537b9f7))
* **get_components:** Adding capability to request multiple component types for the `get_components` method. ([#63](https://github.com/NREL/infrasys/issues/63)) ([9aa2cfb](https://github.com/NREL/infrasys/commit/9aa2cfb3726683f49a067abdb1e327859f58eb9e))
* implement new methods for storage classes ([7dd9c74](https://github.com/NREL/infrasys/commit/7dd9c747c511511d70aa89c2f2e5a29e3dcc96b1))
* infrasys v1.0.0 ([#97](https://github.com/NREL/infrasys/issues/97)) ([7235c9b](https://github.com/NREL/infrasys/commit/7235c9b1a075774f92022d75ab02d72793365f64))
* **pprint:** Added pprint method that calls rich print for components. ([#54](https://github.com/NREL/infrasys/issues/54)) ([b09ac25](https://github.com/NREL/infrasys/commit/b09ac2501f55790d12a59f6308c160ab50712444))
* **system info:** Adding info method that shows summary of the system. ([#56](https://github.com/NREL/infrasys/issues/56)) ([6ab7b6b](https://github.com/NREL/infrasys/commit/6ab7b6b40382970ca6c97303999c61579bb72b24))
* **system:** Added new method to save the system to a given folder. ([#23](https://github.com/NREL/infrasys/issues/23)) ([3570237](https://github.com/NREL/infrasys/commit/3570237f624da6b4f61aff3647e4ca2439ec7e00)), closes [#10](https://github.com/NREL/infrasys/issues/10)
* **timeseries:** add DeterministicTimeSeries and propagate across backends with documentation ([7235c9b](https://github.com/NREL/infrasys/commit/7235c9b1a075774f92022d75ab02d72793365f64))


### Bug Fixes

* Add a better error message ([9b2e8c4](https://github.com/NREL/infrasys/commit/9b2e8c4e057032caf0062c0dd1855ad6f68d324b))
* Add correct representation of Value curves and remove name from cost and function data components ([#45](https://github.com/NREL/infrasys/issues/45)) ([ae517df](https://github.com/NREL/infrasys/commit/ae517df80447c0a0fb4cfb59e364abe1c0d1b663))
* Adding serialization capabilities for pint.Quantities ([#93](https://github.com/NREL/infrasys/issues/93)) ([31c9af7](https://github.com/NREL/infrasys/commit/31c9af75f89096f74413d84b8f84d9bcd2062194))
* Adding serialization for BaseQuantities on Supplemental Attribues ([#75](https://github.com/NREL/infrasys/issues/75)) ([776ea2f](https://github.com/NREL/infrasys/commit/776ea2f501075340d55ed04f0e14a81ea339bf1f))
* Allow many-to-many attributes. ([a86181b](https://github.com/NREL/infrasys/commit/a86181b2872ea0b178458dc094fffc4f32f0a398))
* Allowing serialization with numpy types inside of `BaseQuantity` ([#63](https://github.com/NREL/infrasys/issues/63)) ([855cf07](https://github.com/NREL/infrasys/commit/855cf07b69549019a4b7d382490f97451d020a78))
* **base_quantity:** Removed string return from serialization of pint quantity. ([#28](https://github.com/NREL/infrasys/issues/28)) ([bd1ab2e](https://github.com/NREL/infrasys/commit/bd1ab2e7c6ee4abb3aa3498340c72d024c7e4817))
* **component_mgr:** Removing duplicate update_function call. ([5d91f9f](https://github.com/NREL/infrasys/commit/5d91f9fb1d47a26844c2b15a0d51f48523e092b3))
* downgrade version for simple versioning ([#64](https://github.com/NREL/infrasys/issues/64)) ([91584d2](https://github.com/NREL/infrasys/commit/91584d2f6d84191896b1cb7af96dd4bdd88d6248))
* **pint_units:** Fixed `BaseQuantity` to allow quantities multiplications ([#25](https://github.com/NREL/infrasys/issues/25)) ([dd818c2](https://github.com/NREL/infrasys/commit/dd818c2b00dcb50ca5fc85ccef5dd8e418d900ea)), closes [#24](https://github.com/NREL/infrasys/issues/24)
* **pint:** Fixed error of pint from previous commit and added new functionality ([#26](https://github.com/NREL/infrasys/issues/26)) ([25a110a](https://github.com/NREL/infrasys/commit/25a110a09d54452101443b3c100221f6725e8a1a))
* **pyproject.toml:** Fix dependency definition ([fa8f125](https://github.com/NREL/infrasys/commit/fa8f125e7990ac70fda775b54d25471aa3740a91))
* **pyproject.toml:** Update required Pydantic version ([4de95a9](https://github.com/NREL/infrasys/commit/4de95a9b57d9b7e54baa61a834720947e7decd22))
* **remove_components:** Removing component from `_components_by_uuid` ([#61](https://github.com/NREL/infrasys/issues/61)) ([3069b40](https://github.com/NREL/infrasys/commit/3069b40af4a48fd5199dc825257896d45611964c))
* Removing `pint.Quantity` from linear function data and value curves.  ([#67](https://github.com/NREL/infrasys/issues/67)) ([9225c50](https://github.com/NREL/infrasys/commit/9225c5035ae9b45d15bd845c23088e989cf7a98f))
* **system_info:** Changing to dynamic widht for table display. ([#75](https://github.com/NREL/infrasys/issues/75)) ([39c5e20](https://github.com/NREL/infrasys/commit/39c5e202802db0944f85335b85ad33334801017e))
* **time-series:** Remove redundant SQLite indexes ([c225d4d](https://github.com/NREL/infrasys/commit/c225d4d4b4374dbf5c3a56f787f875aeead70fc8))
* use uuid_str for arrow table lookup ([a727888](https://github.com/NREL/infrasys/commit/a7278888301805e7e4e656320f4333231cb07ddb))


### Performance Improvements

* Improving component association look-up by separating index. ([#72](https://github.com/NREL/infrasys/issues/72)) ([675e5fd](https://github.com/NREL/infrasys/commit/675e5fd4eead45715cea952cedc66f6b023146bb))


### Documentation

* Add guidance for developers ([f711064](https://github.com/NREL/infrasys/commit/f7110646f5b8963e83d0f6919ef8e6701cd26381))


### Miscellaneous Chores

* release 1.0.0 ([f3ebc57](https://github.com/NREL/infrasys/commit/f3ebc57e0897001606ffb861fa6b7ffba9d961f3))
