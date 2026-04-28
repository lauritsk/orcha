# Changelog
All notable changes to this project will be documented in this file. See [conventional commits](https://www.conventionalcommits.org/) for commit guidelines.

- - -
## [v0.7.3](https://github.com/lauritsk/pid/compare/c3859bf9bf9600f41fb7c49b0228052752fd7ef7..v0.7.3) - 2026-04-28
#### Bug Fixes
- handle agent-pushed session branches (#33) - ([96307a0](https://github.com/lauritsk/pid/commit/96307a0155d41ea868186923901af468b66a056a)) - Karl Hans Laurits
#### Miscellaneous Chores
- update mise tools - ([c3859bf](https://github.com/lauritsk/pid/commit/c3859bf9bf9600f41fb7c49b0228052752fd7ef7)) - Karl Hans Laurits

- - -

## [v0.7.2](https://github.com/lauritsk/pid/compare/9adf05d13c3717fc8fc7c7deeb3f2f4133950e13..v0.7.2) - 2026-04-28
#### Bug Fixes
- use built wheel in release image - ([47dbef8](https://github.com/lauritsk/pid/commit/47dbef88cdb8549e0032a5fca26de6a407aca250)) - Karl Hans Laurits
#### Miscellaneous Chores
- update pid tool locally - ([9adf05d](https://github.com/lauritsk/pid/commit/9adf05d13c3717fc8fc7c7deeb3f2f4133950e13)) - Karl Hans Laurits

- - -

## [v0.7.1](https://github.com/lauritsk/pid/compare/ea7e7a2dd74be40b0a263f75efa94800a63111b4..v0.7.1) - 2026-04-28
#### Bug Fixes
- (**docker**) run pid by default - ([fdaff42](https://github.com/lauritsk/pid/commit/fdaff42193e1fcfd42e95ef28e9a6b7df9bd58a3)) - Karl Hans Laurits
#### Revert
- ci: skip check workflow for version bumpsThis reverts commit ea7e7a2dd74be40b0a263f75efa94800a63111b4. - ([7e489ee](https://github.com/lauritsk/pid/commit/7e489eeb83e596e74a5b53d15dcb53b8e09df50c)) - Karl Hans Laurits
#### Documentation
- move plans under docs/ and remove legacy todo and plan files - ([c039f99](https://github.com/lauritsk/pid/commit/c039f99b5fb74aab7ae1b8f044266190c579dd34)) - Karl Hans Laurits
#### Continuous Integration
- skip check workflow for version bumps - ([ea7e7a2](https://github.com/lauritsk/pid/commit/ea7e7a2dd74be40b0a263f75efa94800a63111b4)) - Karl Hans Laurits

- - -

## [v0.7.0](https://github.com/lauritsk/pid/compare/2dece949994939cbd0f8a0c46760cac36004f892..v0.7.0) - 2026-04-28
#### Features
- publish container image to GHCR - ([ca978ee](https://github.com/lauritsk/pid/commit/ca978ee18dc52a84424651b571a4220be18505dd)) - Karl Hans Laurits
#### Documentation
- add agent instructions - ([742d88d](https://github.com/lauritsk/pid/commit/742d88daf00ce070d66433cb0cbb092c14f74f8a)) - Karl Hans Laurits
#### Miscellaneous Chores
- update pid tool here - ([2dece94](https://github.com/lauritsk/pid/commit/2dece949994939cbd0f8a0c46760cac36004f892)) - Karl Hans Laurits

- - -

## [v0.6.0](https://github.com/lauritsk/pid/compare/171037d7bcf9787cb8cfb0166317d22ab8cc5408..v0.6.0) - 2026-04-27
#### Features
- improve CLI output structure and visuals - ([84ef18f](https://github.com/lauritsk/pid/commit/84ef18f3769841ec3d1e01b1e5adad04977cede7)) - Karl Hans Laurits
#### Bug Fixes
- update interactive prompts in place - ([d7faa07](https://github.com/lauritsk/pid/commit/d7faa07ba4192777402f423bf308e88c58e84491)) - Karl Hans Laurits
#### Tests
- run pytest suite in parallel - ([84ee385](https://github.com/lauritsk/pid/commit/84ee385ddce9727bd2068f8d30647e7c4c0fa495)) - Karl Hans Laurits
#### Continuous Integration
- ensure mise check passes before release - ([8644804](https://github.com/lauritsk/pid/commit/8644804a185304b4843c81efa97f75e6f4f9804e)) - Karl Hans Laurits
#### Miscellaneous Chores
- update pid tool here - ([171037d](https://github.com/lauritsk/pid/commit/171037d7bcf9787cb8cfb0166317d22ab8cc5408)) - Karl Hans Laurits

- - -

## [v0.5.1](https://github.com/lauritsk/pid/compare/ee638f50697445bb865de1966c506d813958a707..v0.5.1) - 2026-04-27
#### Bug Fixes
- wait for merge confirmation before cleanup - ([6722767](https://github.com/lauritsk/pid/commit/6722767fef024892bb7783428057ab6d6ac49a23)) - Karl Hans Laurits
#### Miscellaneous Chores
- update pid tool here - ([ee638f5](https://github.com/lauritsk/pid/commit/ee638f50697445bb865de1966c506d813958a707)) - Karl Hans Laurits

- - -

## [v0.5.0](https://github.com/lauritsk/pid/compare/fe956edad11d52c0fd382d71435b890bb4e99413..v0.5.0) - 2026-04-27
#### Features
- add bounded base refresh checkpoints - ([1be2f07](https://github.com/lauritsk/pid/commit/1be2f07afd320f993d243992e9f7cc5bf62c3c85)) - Karl Hans Laurits
- expose configurable session output visibility - ([6643e5e](https://github.com/lauritsk/pid/commit/6643e5e4c4e5bb088e13043844ede8c09b4fe05d)) - Karl Hans Laurits
- add init, prompt, and inspection CLI support - ([8451aba](https://github.com/lauritsk/pid/commit/8451aba17c2ce974e5eb30e883fb4724fe41ee22)) - Karl Hans Laurits
- add CLI inspection commands and interactive prompts - ([2b0dba3](https://github.com/lauritsk/pid/commit/2b0dba369ac41642db88a0709368e6b430e8cf45)) - Karl Hans Laurits
- add interactive prompts for missing CLI arguments - ([b3dcd26](https://github.com/lauritsk/pid/commit/b3dcd2620f9ec34f3e8edf3afcffbe7250309cd2)) - Karl Hans Laurits
#### Documentation
- add extensibility roadmap - ([ee0cc00](https://github.com/lauritsk/pid/commit/ee0cc007da791c86f10ce63de39d5aa31ab33011)) - Karl Hans Laurits
- add merge reliability improvement plan - ([3a0051d](https://github.com/lauritsk/pid/commit/3a0051d73fc013870c8b2234480003889e389b8f)) - Karl Hans Laurits
#### Miscellaneous Chores
- update pid tool here - ([fe956ed](https://github.com/lauritsk/pid/commit/fe956edad11d52c0fd382d71435b890bb4e99413)) - Karl Hans Laurits

- - -

## [v0.4.0](https://github.com/lauritsk/pid/compare/c0030032a7fcf3c6a38aef18607ab1b03d1b0af0..v0.4.0) - 2026-04-27
#### Features
- make pid prompts and forge commands configurable - ([6fe865b](https://github.com/lauritsk/pid/commit/6fe865b75a82a1d36812f6215ed73bd2e5cfc410)) - Karl Hans Laurits
- keep screen awake on macOS when configured - ([0e8c96c](https://github.com/lauritsk/pid/commit/0e8c96c5c9a803ddd9a34d9a59eb990c3bfda8db)) - Karl Hans Laurits
#### Tests
- expand edge-case coverage and enforce coverage gate - ([afc7da7](https://github.com/lauritsk/pid/commit/afc7da75b5342fcb3de5d080fad812be10246235)) - Karl Hans Laurits
#### Miscellaneous Chores
- update mise packages - ([b7391d2](https://github.com/lauritsk/pid/commit/b7391d2fe5cd6df609038ed2c82bdc45acf5329e)) - Karl Hans Laurits
- update todo - ([6b1d497](https://github.com/lauritsk/pid/commit/6b1d497c5fa03c6b6c2eb2e3925d2f4c882dee8e)) - Karl Hans Laurits
- add released version of pid as usable package to mise - ([28c6c7b](https://github.com/lauritsk/pid/commit/28c6c7b9922212e2bbf40efaef1dc3c3abc375f7)) - Karl Hans Laurits
- rename project to pid - ([c003003](https://github.com/lauritsk/pid/commit/c0030032a7fcf3c6a38aef18607ab1b03d1b0af0)) - Karl Hans Laurits

- - -

## [v0.3.0](https://github.com/lauritsk/pid/compare/9bf6b6ed1e2eb7999d898b6c9452ad7cc8e58628..v0.3.0) - 2026-04-27
#### Features
- use any agent - ([9c00a68](https://github.com/lauritsk/pid/commit/9c00a68d1cda7ddcb96a321d704b65009f52f15b)) - Karl Hans Laurits
- add logging - ([eda8d39](https://github.com/lauritsk/pid/commit/eda8d39a7b3633da64025bf3479fedece32d6f57)) - Karl Hans Laurits
- require review agents to update relevant docs - ([32f11f3](https://github.com/lauritsk/pid/commit/32f11f32a7055827d3129dc36612d5104bb0a4a9)) - Karl Hans Laurits
- require review agents to validate test coverage - ([d272af3](https://github.com/lauritsk/pid/commit/d272af385fa0229396137e37a65db103a7c7102d)) - Karl Hans Laurits
- launch interactive session - ([f112ec9](https://github.com/lauritsk/pid/commit/f112ec95493ffe45335a483ba5cdf4539a7f951b)) - Karl Hans Laurits
- agent commit msg - ([ea354b4](https://github.com/lauritsk/pid/commit/ea354b4cc699e2937cade41118f7f47aef6719f5)) - Karl Hans Laurits
- add orchestrator agent - ([6448c44](https://github.com/lauritsk/pid/commit/6448c44c822da1629851b8475222e2a968f5a2c3)) - Karl Hans Laurits
#### Bug Fixes
- keep merge retries from consuming agent attempts - ([67882f1](https://github.com/lauritsk/pid/commit/67882f1fb34724d98ca4e1429f20036273a0b310)) - Karl Hans Laurits
- defer review-triggered thinking bump until after follow-up - ([4cc1b6d](https://github.com/lauritsk/pid/commit/4cc1b6d57f9abea95e2d4e5948c960c8b6a6995b)) - Karl Hans Laurits
#### Refactoring
- generic2 - ([7ed9997](https://github.com/lauritsk/pid/commit/7ed9997bbd7f6aae2610984f01ed3706fd840e17)) - Karl Hans Laurits
- generic1 - ([847beb6](https://github.com/lauritsk/pid/commit/847beb69981fdf485441d99d2695bef738ee0ec2)) - Karl Hans Laurits
#### Miscellaneous Chores
- update todo.md - ([fc12244](https://github.com/lauritsk/pid/commit/fc122441295885d6e2e6d5e723c7e3c07f17c760)) - Karl Hans Laurits
- move audit into hk checks - ([cbb0553](https://github.com/lauritsk/pid/commit/cbb05537c1d54993edc2f376be0380712291bf2f)) - Karl Hans Laurits
- add released version of pid as usable package to mise - ([9bf6b6e](https://github.com/lauritsk/pid/commit/9bf6b6ed1e2eb7999d898b6c9452ad7cc8e58628)) - Karl Hans Laurits

- - -

## [v0.2.0](https://github.com/lauritsk/pid/compare/d3d4daceef76e2f1cd4bb2d124070fc479695cc6..v0.2.0) - 2026-04-27
#### Features
- add plumbum-backed command runner - ([001de9f](https://github.com/lauritsk/pid/commit/001de9f88dfba40534f2d051953de1efd0223c62)) - Karl Hans Laurits
#### Continuous Integration
- enforce locked mise installs - ([d3d4dac](https://github.com/lauritsk/pid/commit/d3d4daceef76e2f1cd4bb2d124070fc479695cc6)) - Karl Hans Laurits
#### Miscellaneous Chores
- namespace release tasks - ([6a5ac76](https://github.com/lauritsk/pid/commit/6a5ac76a6d68f375a018ebda4810140292d14f39)) - Karl Hans Laurits

- - -

## [v0.1.0](https://github.com/lauritsk/pid/compare/73dbe570240f8eb6620eef0b13e98f6dd7463ea8..v0.1.0) - 2026-04-27
#### Features
- improve Typer CLI handling - ([3398fe4](https://github.com/lauritsk/pid/commit/3398fe46f9d08d0814b94d39170227a6aff27d02)) - Karl Hans Laurits
- add rich cli summaries - ([fd96992](https://github.com/lauritsk/pid/commit/fd969929e647c8565f00bcdc03d294c280c1ebf7)) - Karl Hans Laurits
- add Typer CLI scaffold - ([af6e23d](https://github.com/lauritsk/pid/commit/af6e23df618499ea1ff70338c1868123452d8685)) - Karl Hans Laurits
#### Bug Fixes
- (**release**) make rumdl ignore cog-generated changelog file - ([51b9abd](https://github.com/lauritsk/pid/commit/51b9abd8001dc8b492ecb3e9619a6b2b68d7142e)) - Karl Hans Laurits
#### Continuous Integration
- add install task for checks - ([e24481b](https://github.com/lauritsk/pid/commit/e24481bc7cd1b2a0215c174324312ac74795b9d5)) - Karl Hans Laurits
- sync dependencies before lint - ([3573fd8](https://github.com/lauritsk/pid/commit/3573fd8cb7dbcffd2630da221c1d78f19ff3d496)) - Karl Hans Laurits
- add actionlint checks - ([5736a39](https://github.com/lauritsk/pid/commit/5736a39d23ada710c3d53c3705b42b5b949d13be)) - Karl Hans Laurits
#### Miscellaneous Chores
- shorten mise task descriptions - ([3427410](https://github.com/lauritsk/pid/commit/34274107a6b0def7fa9aaa65b6962ed08150f003)) - Karl Hans Laurits
- update pid project structure - ([7c7311c](https://github.com/lauritsk/pid/commit/7c7311caec92f84e151ff66e5e58947be329a35d)) - Karl Hans Laurits
- update pid workflow checks - ([97fdf34](https://github.com/lauritsk/pid/commit/97fdf34d0b3812d5a821a9859ce25a354511b5d8)) - Karl Hans Laurits
- update release hooks and wt fish coverage - ([955a927](https://github.com/lauritsk/pid/commit/955a9273f735c9e0f27a8109c6430ecef3bfc084)) - Karl Hans Laurits
- use hk builtins - ([3dd6689](https://github.com/lauritsk/pid/commit/3dd6689118b70f0436dc4d91998afac0c090b71d)) - Karl Hans Laurits
- configure GoReleaser release artifacts - ([feb662a](https://github.com/lauritsk/pid/commit/feb662a4552e60085e904acdefa7dba64cbae91e)) - Karl Hans Laurits
- set up package entry point - ([156db30](https://github.com/lauritsk/pid/commit/156db306ed4b8f5af89fb2230f33a23fbd6516a6)) - Karl Hans Laurits
- initial commit - ([73dbe57](https://github.com/lauritsk/pid/commit/73dbe570240f8eb6620eef0b13e98f6dd7463ea8)) - Karl Hans Laurits

- - -

Changelog generated by [cocogitto](https://github.com/cocogitto/cocogitto).