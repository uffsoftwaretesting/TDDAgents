# C# (.NET): dotnet test, a JUnit logger and coverlet

`dotnet test` writes JUnit XML through a logger package, and coverlet measures coverage. Both are NuGet packages the **test project** declares, so `dotnet restore` installs them:

- `JunitXml.TestLogger`: the `junit` logger.
- `coverlet.msbuild`: coverage driven by MSBuild properties on the `dotnet test` command line.
- The test framework and its adapter (`xunit` + `xunit.runner.visualstudio`, or `NUnit` + `NUnit3TestAdapter`, or `MSTest`), and `Microsoft.NET.Test.Sdk`.

## Example

A solution with `src/Calculator/Calculator.csproj` and one test project, `tests/Calculator.Tests/Calculator.Tests.csproj`.

```json
{
  "version": {{PROJECT_CONFIG_VERSION}},
  "setup": "dotnet restore",
  "test": "dotnet test --no-restore --logger \"junit;LogFilePath=${{JUNIT_REPORT_ENV}}\" /p:CollectCoverage=true /p:CoverletOutputFormat=cobertura /p:CoverletOutput=\"$PWD/coverage/\"",
  "coverageReport": {"path": "coverage/coverage.cobertura.xml", "format": "cobertura"},
  "testFiles": ["tests/"],
  "testSupportFiles": ["tests/**/*.csproj", "tests/**/TestData/"],
  "manifestFiles": ["*.sln", "src/**/*.csproj", "tests/**/*.csproj", "Directory.Build.props", "Directory.Packages.props", "packages.lock.json", "global.json", "NuGet.config"],
  "exclude": ["bin/", "obj/", "TestResults/", "coverage/"]
}
```

- `--logger "junit;LogFilePath=<path>"` writes the report where TDDAgents reads it. With several test projects each writes its own report, and they would overwrite one another: check the logger's docs for its path tokens, or keep one test project.
- `CoverletOutput` is relative to the test project unless absolute; ending it with `/` names a directory, where the Cobertura report is `coverage.cobertura.xml`. Check coverlet's docs for your version.
- `dotnet test` exits non-zero when a test fails or the build fails.
- The logger names each test's class and method, not its file, so the uncollected-file check is unknown for .NET.
- A test project's `.csproj` is both test code and a manifest: as a manifest it stays writable in every phase, so a package the tests need can be added in any phase.

## Pitfalls

- Pin the SDK in `global.json` so the final run builds with the same SDK.
- `[Fact(Skip = "…")]` / `[Ignore]` tests are skipped, and a skipped test is not a pass.
