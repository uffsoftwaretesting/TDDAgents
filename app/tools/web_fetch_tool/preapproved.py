"""
The code-documentation hosts WebFetch may fetch without asking.

Ported from `reference/claude-code/src/tools/WebFetchTool/preapproved.ts`: `PREAPPROVED_HOSTS` and
`isPreapprovedHost`. As upstream warns, the list is for WebFetch's GET requests only and is never
a network allowance for anything else.
"""

from __future__ import annotations

PREAPPROVED_HOSTS = frozenset({
    # Anthropic
    "platform.claude.com",
    "code.claude.com",
    "modelcontextprotocol.io",
    "github.com/anthropics",
    "agentskills.io",
    # Top Programming Languages
    "docs.python.org",
    "en.cppreference.com",
    "docs.oracle.com",
    "learn.microsoft.com",
    "developer.mozilla.org",
    "go.dev",
    "pkg.go.dev",
    "www.php.net",
    "docs.swift.org",
    "kotlinlang.org",
    "ruby-doc.org",
    "doc.rust-lang.org",
    "www.typescriptlang.org",
    # Web & JavaScript Frameworks/Libraries
    "react.dev",
    "angular.io",
    "vuejs.org",
    "nextjs.org",
    "expressjs.com",
    "nodejs.org",
    "bun.sh",
    "jquery.com",
    "getbootstrap.com",
    "tailwindcss.com",
    "d3js.org",
    "threejs.org",
    "redux.js.org",
    "webpack.js.org",
    "jestjs.io",
    "reactrouter.com",
    # Python Frameworks & Libraries
    "docs.djangoproject.com",
    "flask.palletsprojects.com",
    "fastapi.tiangolo.com",
    "pandas.pydata.org",
    "numpy.org",
    "www.tensorflow.org",
    "pytorch.org",
    "scikit-learn.org",
    "matplotlib.org",
    "requests.readthedocs.io",
    "jupyter.org",
    # PHP Frameworks
    "laravel.com",
    "symfony.com",
    "wordpress.org",
    # Java Frameworks & Libraries
    "docs.spring.io",
    "hibernate.org",
    "tomcat.apache.org",
    "gradle.org",
    "maven.apache.org",
    # .NET & C# Frameworks
    "asp.net",
    "dotnet.microsoft.com",
    "nuget.org",
    "blazor.net",
    # Mobile Development
    "reactnative.dev",
    "docs.flutter.dev",
    "developer.apple.com",
    "developer.android.com",
    # Data Science & Machine Learning
    "keras.io",
    "spark.apache.org",
    "huggingface.co",
    "www.kaggle.com",
    # Databases
    "www.mongodb.com",
    "redis.io",
    "www.postgresql.org",
    "dev.mysql.com",
    "www.sqlite.org",
    "graphql.org",
    "prisma.io",
    # Cloud & DevOps
    "docs.aws.amazon.com",
    "cloud.google.com",
    "kubernetes.io",
    "www.docker.com",
    "www.terraform.io",
    "www.ansible.com",
    "vercel.com/docs",
    "docs.netlify.com",
    "devcenter.heroku.com",
    # Testing & Monitoring
    "cypress.io",
    "selenium.dev",
    # Game Development
    "docs.unity.com",
    "docs.unrealengine.com",
    # Other Essential Tools
    "git-scm.com",
    "nginx.org",
    "httpd.apache.org",
})


def _split() -> tuple[frozenset[str], dict[str, tuple[str, ...]]]:
    hosts: set[str] = set()
    paths: dict[str, list[str]] = {}
    for entry in PREAPPROVED_HOSTS:
        slash = entry.find("/")
        if slash == -1:
            hosts.add(entry)
        else:
            paths.setdefault(entry[:slash], []).append(entry[slash:])
    return frozenset(hosts), {host: tuple(prefixes) for host, prefixes in paths.items()}


HOSTNAME_ONLY, PATH_PREFIXES = _split()


def is_preapproved_host(hostname: str, pathname: str) -> bool:
    """`isPreapprovedHost`: a listed host, or a listed path prefix on a segment boundary."""
    if hostname in HOSTNAME_ONLY:
        return True
    return any(pathname == prefix or pathname.startswith(prefix + "/") for prefix in PATH_PREFIXES.get(hostname, ()))


__all__ = ["PREAPPROVED_HOSTS", "is_preapproved_host"]
