import os
import glob


def get_base_dir():
    # project root is one level up from tests/
    return os.path.dirname(os.path.dirname(__file__))


def test_governance_docs_files_exist():
    base = get_base_dir()
    expected_files = [
        'README.md',
        'CONTRIBUTING.md',
        'ISSUE_TEMPLATE.md',
        'PULL_REQUEST_TEMPLATE.md',
        'CHANGELOG.md',
    ]
    for fname in expected_files:
        path = os.path.join(base, fname)
        assert os.path.isfile(path), f"Governance file '{fname}' must exist at project root"


def test_docs_directory_contains_adr_markdown():
    base = get_base_dir()
    docs_dir = os.path.join(base, 'docs')
    assert os.path.isdir(docs_dir), f"Docs directory must exist at {docs_dir}"
    # Find any markdown file in docs/ directory
    pattern = os.path.join(docs_dir, '*.md')
    md_files = glob.glob(pattern)
    # Exclude potential index or placeholders if needed, but require at least one ADR
    assert len(md_files) >= 1, f"At least one ADR markdown file must exist in docs/ (found: {md_files})"