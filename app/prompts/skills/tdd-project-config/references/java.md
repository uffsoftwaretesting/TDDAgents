# Java: Maven Surefire or Gradle, JUnit 5

Both Maven Surefire and Gradle write one JUnit XML file per test class (`TEST-<class>.xml`), never one file for the run. The test command therefore removes the previous run's reports, runs the build, and joins the per-class files into the one report TDDAgents reads, keeping the build's exit status. The joined file wraps each class's `<testsuite>` in a `<testsuites>` root.

## Example: Maven

`pom.xml` declares JUnit Jupiter as a `test`-scoped dependency and a recent `maven-surefire-plugin`.

```json
{
  "version": {{PROJECT_CONFIG_VERSION}},
  "setup": "mvn -B -q dependency:go-offline",
  "test": "rm -rf target/surefire-reports; mvn -B test; status=$?; { echo '<testsuites>'; cat target/surefire-reports/TEST-*.xml 2>/dev/null | sed '/<?xml/d'; echo '</testsuites>'; } > \"${{JUNIT_REPORT_ENV}}\"; exit $status",
  "testFiles": ["src/test/"],
  "testSupportFiles": ["src/test/resources/"],
  "manifestFiles": ["pom.xml", "mvnw", ".mvn/wrapper/maven-wrapper.properties"],
  "exclude": ["target/"]
}
```

## Example: Gradle

`build.gradle(.kts)` declares JUnit Jupiter in `testImplementation` and `useJUnitPlatform()` on the `test` task. Use the wrapper, so the final run uses the declared Gradle version.

```json
{
  "version": {{PROJECT_CONFIG_VERSION}},
  "test": "rm -rf build/test-results/test; ./gradlew test --no-daemon; status=$?; { echo '<testsuites>'; cat build/test-results/test/TEST-*.xml 2>/dev/null | sed '/<?xml/d'; echo '</testsuites>'; } > \"${{JUNIT_REPORT_ENV}}\"; exit $status",
  "testFiles": ["src/test/"],
  "testSupportFiles": ["src/test/resources/"],
  "manifestFiles": ["build.gradle", "build.gradle.kts", "settings.gradle", "settings.gradle.kts", "gradle.properties", "gradle/libs.versions.toml", "gradle/wrapper/gradle-wrapper.properties", "gradlew"],
  "exclude": ["build/", ".gradle/"]
}
```

- Maven and Gradle exit non-zero when a test fails or the code does not compile.
- Surefire and Gradle name each test's **class** (`classname`), not its file, so the uncollected-file check is unknown for Java. Keep one top-level test class per file, named after the file.

## Coverage

JaCoCo (`jacoco-maven-plugin`, Gradle's `jacoco` plugin) is the usual tool, but its XML report is neither Cobertura nor LCOV, which are the formats TDDAgents reads. Either leave `coverageReport` out (coverage is then not reported), or add a step to the test command that converts JaCoCo's XML to Cobertura with a converter the project declares; check the converter's docs.

## Pitfalls

- A test class whose name Surefire's default includes do not match (`*Test`, `Test*`, `*Tests`, `*TestCase`) never runs.
- `@Disabled` tests are skipped, and a skipped test is not a pass.
