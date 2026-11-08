# PHP: PHPUnit

`composer.json` declares `phpunit/phpunit` in `require-dev`, and `composer.lock` is committed. PHPUnit writes both reports itself.

## Example

`phpunit.xml` names the test suite and the source to measure (the `<source>` element in PHPUnit 10 and later; `<coverage><include>` in PHPUnit 9 — check the docs for your version):

```xml
<phpunit bootstrap="vendor/autoload.php">
  <testsuites>
    <testsuite name="unit">
      <directory>tests</directory>
    </testsuite>
  </testsuites>
  <source>
    <include>
      <directory>src</directory>
    </include>
  </source>
</phpunit>
```

```json
{
  "version": {{PROJECT_CONFIG_VERSION}},
  "setup": "composer install --no-interaction",
  "test": "XDEBUG_MODE=coverage vendor/bin/phpunit --log-junit \"${{JUNIT_REPORT_ENV}}\" --coverage-cobertura \"${{COVERAGE_REPORT_ENV}}\"",
  "coverageReport": {"format": "cobertura"},
  "testFiles": ["tests/", "phpunit.xml"],
  "testSupportFiles": ["tests/fixtures/", "phpunit.xml"],
  "manifestFiles": ["composer.json", "composer.lock"],
  "exclude": ["vendor/", ".phpunit.cache/"]
}
```

- `--log-junit <file>` writes JUnit XML with a `file` attribute on each `<testcase>`, so TDDAgents can tell which test files ran.
- `--coverage-cobertura <file>` writes Cobertura XML. Coverage needs a driver: Xdebug (`XDEBUG_MODE=coverage`) or PCOV. Both are PHP extensions, not Composer packages, so the final run's machine must have one; without a driver PHPUnit warns and writes no coverage.
- PHPUnit exits non-zero when a test fails or errors.

## Pitfalls

- A test class whose file name does not end in `Test.php` is not collected from a `<directory>` by default.
- `markTestSkipped()` and `markTestIncomplete()` tests are skipped, and a skipped test is not a pass.
- PHPUnit's own options such as `--fail-on-warning` change the exit code; keep the defaults unless the project needs them.
