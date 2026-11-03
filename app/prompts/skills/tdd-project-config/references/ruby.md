# Ruby: RSpec, rspec_junit_formatter and SimpleCov

The `Gemfile` declares, in its `:test` (or `:development, :test`) group: `rspec`, `rspec_junit_formatter`, `simplecov` and `simplecov-lcov`. `Gemfile.lock` is committed.

## Example

SimpleCov starts at the top of `spec/spec_helper.rb`, before the code under test is loaded, and writes one LCOV file where TDDAgents reads it:

```ruby
require 'simplecov'
require 'simplecov-lcov'

SimpleCov::Formatter::LcovFormatter.config do |c|
  c.report_with_single_file = true
  c.single_report_path = ENV.fetch('{{COVERAGE_REPORT_ENV}}', 'coverage/lcov.info')
end
SimpleCov.formatter = SimpleCov::Formatter::LcovFormatter
SimpleCov.start do
  add_filter '/spec/'
end
```

```json
{
  "version": {{PROJECT_CONFIG_VERSION}},
  "setup": "bundle install",
  "test": "bundle exec rspec --format progress --format RspecJunitFormatter --out \"${{JUNIT_REPORT_ENV}}\"",
  "coverageReport": {"format": "lcov"},
  "testFiles": ["spec/", ".rspec"],
  "testSupportFiles": ["spec/spec_helper.rb", "spec/support/", "spec/fixtures/", ".rspec"],
  "manifestFiles": ["Gemfile", "Gemfile.lock", "*.gemspec"],
  "exclude": ["vendor/bundle/", ".bundle/", "coverage/"]
}
```

- Each `--format` may be followed by its own `--out`: the progress output goes to the terminal, the JUnit XML to the report path.
- `rspec_junit_formatter` writes a `file` attribute on each `<testcase>`, so TDDAgents can tell which spec files ran.
- RSpec exits non-zero when an example fails or a file fails to load.
- Check `simplecov-lcov`'s README for the configuration names in your version.

## Minitest

`minitest-reporters`' JUnit reporter writes one file per test class into a directory, not one report. TDDAgents reads a single JUnit file, so a Minitest test command must join those files into one report at `${{JUNIT_REPORT_ENV}}`: remove the previous run's files, run the tests, wrap every file's `<testsuite>` (without its `<?xml` line) in one `<testsuites>` root, and exit with the tests' status. Check the reporter's docs for its output directory. The SimpleCov setup above, at the top of `test/test_helper.rb`, works for Minitest too (TDDAgents reads Cobertura or LCOV coverage only).

## Pitfalls

- `bundle exec`, never a global `rspec`, so the declared versions run.
- `pending` and `skip` examples are skipped, and a skipped test is not a pass.
- `spec_helper.rb` is a support file: in GREEN it is locked with the tests.
