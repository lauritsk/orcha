# Changelog
All notable changes to this project will be documented in this file. See [conventional commits](https://www.conventionalcommits.org/) for commit guidelines.

- - -
## [v0.3.0](https://github.com/lauritsk/orcha/compare/9bf6b6ed1e2eb7999d898b6c9452ad7cc8e58628..v0.3.0) - 2026-04-27
#### Features
- use any agent - ([9c00a68](https://github.com/lauritsk/orcha/commit/9c00a68d1cda7ddcb96a321d704b65009f52f15b)) - Karl Hans Laurits
- add logging - ([eda8d39](https://github.com/lauritsk/orcha/commit/eda8d39a7b3633da64025bf3479fedece32d6f57)) - Karl Hans Laurits
- require review agents to update relevant docs - ([32f11f3](https://github.com/lauritsk/orcha/commit/32f11f32a7055827d3129dc36612d5104bb0a4a9)) - Karl Hans Laurits
- require review agents to validate test coverage - ([d272af3](https://github.com/lauritsk/orcha/commit/d272af385fa0229396137e37a65db103a7c7102d)) - Karl Hans Laurits
- launch interactive session - ([f112ec9](https://github.com/lauritsk/orcha/commit/f112ec95493ffe45335a483ba5cdf4539a7f951b)) - Karl Hans Laurits
- agent commit msg - ([ea354b4](https://github.com/lauritsk/orcha/commit/ea354b4cc699e2937cade41118f7f47aef6719f5)) - Karl Hans Laurits
- add orchestrator agent - ([6448c44](https://github.com/lauritsk/orcha/commit/6448c44c822da1629851b8475222e2a968f5a2c3)) - Karl Hans Laurits
#### Bug Fixes
- keep merge retries from consuming agent attempts - ([67882f1](https://github.com/lauritsk/orcha/commit/67882f1fb34724d98ca4e1429f20036273a0b310)) - Karl Hans Laurits
- defer review-triggered thinking bump until after follow-up - ([4cc1b6d](https://github.com/lauritsk/orcha/commit/4cc1b6d57f9abea95e2d4e5948c960c8b6a6995b)) - Karl Hans Laurits
#### Refactoring
- generic2 - ([7ed9997](https://github.com/lauritsk/orcha/commit/7ed9997bbd7f6aae2610984f01ed3706fd840e17)) - Karl Hans Laurits
- generic1 - ([847beb6](https://github.com/lauritsk/orcha/commit/847beb69981fdf485441d99d2695bef738ee0ec2)) - Karl Hans Laurits
#### Miscellaneous Chores
- update todo.md - ([fc12244](https://github.com/lauritsk/orcha/commit/fc122441295885d6e2e6d5e723c7e3c07f17c760)) - Karl Hans Laurits
- move audit into hk checks - ([cbb0553](https://github.com/lauritsk/orcha/commit/cbb05537c1d54993edc2f376be0380712291bf2f)) - Karl Hans Laurits
- add released version of orcha as usable package to mise - ([9bf6b6e](https://github.com/lauritsk/orcha/commit/9bf6b6ed1e2eb7999d898b6c9452ad7cc8e58628)) - Karl Hans Laurits

- - -

## [v0.2.0](https://github.com/lauritsk/orcha/compare/d3d4daceef76e2f1cd4bb2d124070fc479695cc6..v0.2.0) - 2026-04-27
#### Features
- add plumbum-backed command runner - ([001de9f](https://github.com/lauritsk/orcha/commit/001de9f88dfba40534f2d051953de1efd0223c62)) - Karl Hans Laurits
#### Continuous Integration
- enforce locked mise installs - ([d3d4dac](https://github.com/lauritsk/orcha/commit/d3d4daceef76e2f1cd4bb2d124070fc479695cc6)) - Karl Hans Laurits
#### Miscellaneous Chores
- namespace release tasks - ([6a5ac76](https://github.com/lauritsk/orcha/commit/6a5ac76a6d68f375a018ebda4810140292d14f39)) - Karl Hans Laurits

- - -

## [v0.1.0](https://github.com/lauritsk/orcha/compare/73dbe570240f8eb6620eef0b13e98f6dd7463ea8..v0.1.0) - 2026-04-27
#### Features
- improve Typer CLI handling - ([3398fe4](https://github.com/lauritsk/orcha/commit/3398fe46f9d08d0814b94d39170227a6aff27d02)) - Karl Hans Laurits
- add rich cli summaries - ([fd96992](https://github.com/lauritsk/orcha/commit/fd969929e647c8565f00bcdc03d294c280c1ebf7)) - Karl Hans Laurits
- add Typer CLI scaffold - ([af6e23d](https://github.com/lauritsk/orcha/commit/af6e23df618499ea1ff70338c1868123452d8685)) - Karl Hans Laurits
#### Bug Fixes
- (**release**) make rumdl ignore cog-generated changelog file - ([51b9abd](https://github.com/lauritsk/orcha/commit/51b9abd8001dc8b492ecb3e9619a6b2b68d7142e)) - Karl Hans Laurits
#### Continuous Integration
- add install task for checks - ([e24481b](https://github.com/lauritsk/orcha/commit/e24481bc7cd1b2a0215c174324312ac74795b9d5)) - Karl Hans Laurits
- sync dependencies before lint - ([3573fd8](https://github.com/lauritsk/orcha/commit/3573fd8cb7dbcffd2630da221c1d78f19ff3d496)) - Karl Hans Laurits
- add actionlint checks - ([5736a39](https://github.com/lauritsk/orcha/commit/5736a39d23ada710c3d53c3705b42b5b949d13be)) - Karl Hans Laurits
#### Miscellaneous Chores
- shorten mise task descriptions - ([3427410](https://github.com/lauritsk/orcha/commit/34274107a6b0def7fa9aaa65b6962ed08150f003)) - Karl Hans Laurits
- update orcha project structure - ([7c7311c](https://github.com/lauritsk/orcha/commit/7c7311caec92f84e151ff66e5e58947be329a35d)) - Karl Hans Laurits
- update orcha workflow checks - ([97fdf34](https://github.com/lauritsk/orcha/commit/97fdf34d0b3812d5a821a9859ce25a354511b5d8)) - Karl Hans Laurits
- update release hooks and wt fish coverage - ([955a927](https://github.com/lauritsk/orcha/commit/955a9273f735c9e0f27a8109c6430ecef3bfc084)) - Karl Hans Laurits
- use hk builtins - ([3dd6689](https://github.com/lauritsk/orcha/commit/3dd6689118b70f0436dc4d91998afac0c090b71d)) - Karl Hans Laurits
- configure GoReleaser release artifacts - ([feb662a](https://github.com/lauritsk/orcha/commit/feb662a4552e60085e904acdefa7dba64cbae91e)) - Karl Hans Laurits
- set up package entry point - ([156db30](https://github.com/lauritsk/orcha/commit/156db306ed4b8f5af89fb2230f33a23fbd6516a6)) - Karl Hans Laurits
- initial commit - ([73dbe57](https://github.com/lauritsk/orcha/commit/73dbe570240f8eb6620eef0b13e98f6dd7463ea8)) - Karl Hans Laurits

- - -

Changelog generated by [cocogitto](https://github.com/cocogitto/cocogitto).